"""SSE streaming delivery for the LLM feature POST endpoints (Req 11).

The ``?stream=true`` path of the three POST sub-resources: event
formatting plus the ``StreamingResponse`` generator that drives the
orchestrator's provider call while relaying display-progressive deltas
(design §"Routers and SSE"; decision D2/D3).

Contract (Requirements 11.1-11.7, 15.5):

* **All gates run before the stream opens** (Req 11.4): the router calls
  :meth:`LLMOrchestrator.prepare` first and maps its gate rejections
  (429 quota, 503 spend limit) onto plain RFC 7807 responses — this
  module only ever receives a ``prepare`` product, so a stream can never
  open for a rejected request.
* **Machine-readable event types** (Req 11.2): ``delta`` carries one
  incremental display fragment ``{"text": ...}``; the three terminal
  kinds are ``complete`` (the validated LLM_Result envelope, identical
  in content to the persisted row), ``degraded`` (the Fallback_Response
  envelope), and ``error`` (an RFC 7807 body, used only for failures
  that map to an error rather than a fallback — e.g. a commit failure
  after the stream opened).
* **Exactly one terminal event, then close** (Req 11.2, 11.3): every
  stream ends with one terminal event — including when the failure
  precedes any ``delta`` — and the generator returns immediately after
  yielding it, closing the stream.
* **Timeout over the open stream** (Req 11.5): the adapter bounds the
  full provider call with ``asyncio.timeout``; on expiry
  :meth:`LLMOrchestrator.execute` resolves to the fallback outcome,
  which lands here as the ``degraded`` terminal event.
* **Client disconnect aborts the provider call** (Req 11.7): Starlette
  raises :class:`asyncio.CancelledError` inside the generator when the
  client goes away; the generator cancels the provider-call task, which
  propagates the cancellation into the adapter's ``async for`` and
  unwinds its ``httpx`` context managers, closing the upstream HTTP
  stream so no further tokens are consumed.
* **Deltas are display content only** (Req 11.6): the generator relays
  the raw output fragments the orchestrator's ``on_delta`` callback
  receives — never system-prompt text, provider metadata, or the key.
* **Cache hits / persisted reuse / pre-call fallbacks** (Req 15.5): a
  ``prepare`` that resolves without a provider call still opens the
  stream (the request asked for SSE) and emits its terminal event
  directly — ``complete`` for a cached/reused validated result,
  ``degraded`` for a pre-call fallback — with no ``delta`` events.
* ``X-LLM-Quota-Remaining`` (Req 13.5) is set from the ``prepare``
  product's remaining count; ``None`` (counter unreadable) omits the
  header rather than fabricating a value.

Transaction model: the provider call stages LLM_Result and
invocation-log rows on the request session (the orchestrator never
commits); the generator commits after ``execute`` returns — success and
fallback alike, since a failed call still staged its invocation-log row
— before emitting the terminal event. ``prepare`` products that skip the
provider call stage nothing, so their branch performs no commit.

PRIVACY (``security.md``): deltas and terminal payloads are
Restricted-derived display content — never logged here; the structured
``llm_stream_failed`` event carries the feature name only.

Design reference: phase-3-llm-layer §"Routers and SSE (api/matches/llm/)".
Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 15.5.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any, Final

import structlog
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.services.llm.orchestrator import (
    LLMOrchestrator,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

_log = structlog.get_logger(__name__)

__all__ = [
    "EVENT_COMPLETE",
    "EVENT_DEGRADED",
    "EVENT_DELTA",
    "EVENT_ERROR",
    "format_sse_event",
    "llm_stream_response",
]

# Machine-readable event types (Req 11.2): one incremental kind, three
# terminal kinds. The frontend's SSE parser dispatches on these names.
EVENT_DELTA: Final[str] = "delta"
EVENT_COMPLETE: Final[str] = "complete"
EVENT_DEGRADED: Final[str] = "degraded"
EVENT_ERROR: Final[str] = "error"

_QUOTA_HEADER: Final[str] = "X-LLM-Quota-Remaining"

# SSE transport headers: disable response caching and reverse-proxy
# buffering so events reach the client as they are emitted.
_SSE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def format_sse_event(event: str, data: str) -> str:
    """Serialize one SSE event: ``event:`` line, ``data:`` line(s), blank line.

    Multi-line payloads are split across consecutive ``data:`` lines per
    the SSE wire format (clients rejoin them with ``\\n``); our payloads
    are compact single-line JSON, so the split is a defensive no-op.
    """
    lines = data.splitlines() or [""]
    data_block = "".join(f"data: {line}\n" for line in lines)
    return f"event: {event}\n{data_block}\n"


def _current_request_id() -> str | None:
    """The request_id bound by ``RequestIdMiddleware``, or ``None``."""
    value = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(value, str):
        return value
    return None


def _delta_event(delta: str) -> str:
    """One incremental content event (Req 11.6): display text only."""
    return format_sse_event(EVENT_DELTA, json.dumps({"text": delta}, ensure_ascii=False))


def _terminal_event(envelope: LLMResultEnvelope[Any]) -> str:
    """The success-shaped terminal event for a pipeline outcome (Req 11.2).

    ``complete`` carries a validated LLM_Result envelope — the same
    serialization the non-streaming 200 body uses, identical in content
    to the persisted row; ``degraded`` carries the Fallback_Response
    envelope, distinguished by ``is_fallback`` (Req 9.2).
    """
    kind = EVENT_DEGRADED if envelope.is_fallback else EVENT_COMPLETE
    return format_sse_event(kind, envelope.model_dump_json())


def _error_event() -> str:
    """The ``error`` terminal event: a user-safe RFC 7807 body (Req 11.3).

    Used only for failures that map to an error rather than a fallback —
    an unexpected exception after the stream opened (the non-streaming
    equivalent would have been the shared 500 handler). Fixed copy: no
    stack trace, no PII, no key material (``conventions.md`` error rules).
    """
    problem: dict[str, Any] = {
        "type": "internal_server_error",
        "title": "Internal Server Error",
        "detail": "The request could not be completed.",
        "status": 500,
        "request_id": _current_request_id(),
    }
    return format_sse_event(EVENT_ERROR, json.dumps(problem))


def llm_stream_response[TInput, TResult: BaseModel](
    orchestrator: LLMOrchestrator,
    prepared: LLMOutcome[TResult] | ProviderCallPlan[TInput, TResult],
    *,
    session: AsyncSession,
) -> StreamingResponse:
    """Build the ``text/event-stream`` response for a ``prepare`` product.

    Callable only after every gate passed (Req 11.4): *prepared* is what
    :meth:`LLMOrchestrator.prepare` returned — an immediate
    :class:`LLMOutcome` (persisted reuse, cache hit, pre-call fallback)
    or a :class:`ProviderCallPlan` with the quota reservation already
    counted. Gate rejections never reach this function; the router maps
    them onto plain RFC 7807 responses.
    """
    headers = dict(_SSE_HEADERS)
    if prepared.quota_remaining is not None:
        headers[_QUOTA_HEADER] = str(prepared.quota_remaining)
    return StreamingResponse(
        _event_stream(orchestrator, prepared, session),
        media_type="text/event-stream",
        headers=headers,
    )


async def _event_stream[TInput, TResult: BaseModel](
    orchestrator: LLMOrchestrator,
    prepared: LLMOutcome[TResult] | ProviderCallPlan[TInput, TResult],
    session: AsyncSession,
) -> AsyncIterator[str]:
    """Yield the stream's events: zero or more ``delta``, one terminal.

    The single place the Req 11.2/11.3 invariant is enforced: every exit
    path yields exactly one terminal event as its final yield (except a
    client disconnect, where there is no client left to receive one —
    Req 11.7 governs that path instead).
    """
    feature: str | None = (
        prepared.spec.feature.value if isinstance(prepared, ProviderCallPlan) else None
    )
    try:
        if isinstance(prepared, LLMOutcome):
            # Resolved without a provider call — persisted reuse, cache
            # hit (Req 15.5), or a pre-call fallback. Nothing was staged
            # on the session, so no commit; emit the terminal directly.
            yield _terminal_event(prepared.envelope)
            return

        # Deltas flow from the orchestrator's callback into this
        # generator through a queue: ``execute`` runs as a task so the
        # generator can yield fragments as they arrive (and so a client
        # disconnect has a task to cancel, Req 11.7). ``None`` is the
        # stream-end sentinel, enqueued in ``finally`` so it lands on
        # success, LLM failure, and unexpected exception alike.
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_delta(delta: str) -> None:
            queue.put_nowait(_delta_event(delta))

        async def run_call() -> LLMOutcome[TResult]:
            try:
                return await orchestrator.execute(prepared, on_delta=on_delta)
            finally:
                queue.put_nowait(None)

        call_task = asyncio.create_task(run_call())
        try:
            while (item := await queue.get()) is not None:
                yield item
            # ``execute`` never raises for an LLM failure (Req 9.1) — it
            # resolves to the fallback outcome; an exception here is an
            # unexpected infrastructure failure, handled below.
            outcome = await call_task
        except asyncio.CancelledError:
            # Client disconnect (Req 11.7): cancel the provider call so
            # the adapter's HTTP stream closes and stops consuming
            # tokens, then let the cancellation propagate.
            call_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await call_task
            raise

        # Land the staged rows — the LLM_Result + invocation log on
        # success, the failure-category invocation log on fallback —
        # before the terminal event, so the ``complete`` envelope is
        # identical in content to the persisted row (Req 11.2).
        await session.commit()
        yield _terminal_event(outcome.envelope)
    except asyncio.CancelledError:
        raise
    except Exception:
        # An unexpected failure after the stream opened must still end
        # in a terminal event rather than a silently dropped connection
        # (Req 11.3). Structured event only — never the payload, never
        # PII (security.md).
        _log.error("llm_stream_failed", feature=feature, exc_info=True)
        yield _error_event()
