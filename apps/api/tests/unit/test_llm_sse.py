"""Unit tests for ``api/matches/llm/sse.py`` (phase-3-llm-layer task 10.2).

Drives :func:`llm_stream_response` against scripted fakes, covering the
SSE delivery contract (Requirements 11.2, 11.3, 11.5-11.7, 15.5):

* Event wire format: ``event:`` line, ``data:`` line(s), blank line.
* Immediate ``prepare`` outcomes (persisted reuse / cache hit / pre-call
  fallback) open the stream and emit their terminal event directly —
  ``complete`` for validated results (Req 15.5), ``degraded`` for
  fallbacks — with no ``delta`` events and no commit.
* A provider-call plan relays ``delta`` events in order, commits the
  staged rows, then emits exactly one terminal event and closes.
* An LLM failure resolved by the orchestrator lands as the ``degraded``
  terminal (Req 11.3) — the timeout path included (Req 11.5), since the
  adapter surfaces expiry as an ``LLMError`` the orchestrator maps to a
  fallback outcome.
* An unexpected exception after the stream opened still terminates the
  stream with the ``error`` RFC 7807 event (Req 11.3).
* Client disconnect (``asyncio.CancelledError`` in the generator)
  cancels the provider-call task (Req 11.7).
* ``X-LLM-Quota-Remaining`` is present when the count is known and
  omitted when it is not (Req 13.5).

Property 19 (task 10.8) covers the exactly-one-terminal invariant across
generated failure schedules; router integration is task 10.9.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from uuid_utils.compat import uuid7

from matchlayer_api.api.matches.llm.sse import (
    EVENT_COMPLETE,
    EVENT_DEGRADED,
    EVENT_DELTA,
    EVENT_ERROR,
    format_sse_event,
    llm_stream_response,
)
from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.llm.client import LLMMessage, LLMRequest
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOrchestrator,
    LLMOutcome,
    PromptInputs,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope

# ---------------------------------------------------------------------------
# Test schema and wiring.
# ---------------------------------------------------------------------------

_USER_ID = UUID("01890000-0000-7000-8000-000000000001")


class _EchoResult(BaseModel):
    """Minimal result schema standing in for a feature payload."""

    model_config = ConfigDict(extra="forbid")

    message: str


_EnvelopeCls = LLMResultEnvelope[_EchoResult]


def _spec() -> LLMFeatureSpec[None, _EchoResult]:
    return LLMFeatureSpec(
        feature=LLMFeature.RESUME_COACH,
        result_schema=_EchoResult,
        build_inputs=lambda match, feature_input: PromptInputs(values={}, sections=[]),
        build_fallback=lambda match, feature_input, reason: _EchoResult(
            message=f"fallback:{reason.value}"
        ),
    )


def _success_envelope(message: str = "hello") -> LLMResultEnvelope[_EchoResult]:
    return _EnvelopeCls(
        id=str(uuid7()),
        is_fallback=False,
        fallback_reason=None,
        prompt_template_version=1,
        created_at=datetime.now(UTC),
        result=_EchoResult(message=message),
    )


def _fallback_envelope(reason: FailureReason) -> LLMResultEnvelope[_EchoResult]:
    return _EnvelopeCls(
        id=None,
        is_fallback=True,
        fallback_reason=reason,
        prompt_template_version=None,
        created_at=None,
        result=_EchoResult(message=f"fallback:{reason.value}"),
    )


def _plan(*, quota_remaining: int = 4) -> ProviderCallPlan[None, _EchoResult]:
    return ProviderCallPlan(
        spec=_spec(),
        envelope_cls=_EnvelopeCls,
        user_id=_USER_ID,
        match=MatchResult(id=uuid7(), user_id=_USER_ID),
        feature_input=None,
        request=LLMRequest(
            messages=[LLMMessage(role="system", content="instructions")],
            output_schema=_EchoResult.model_json_schema(),
            max_output_tokens=100,
        ),
        template_version=1,
        input_hash="a" * 64,
        quota_remaining=quota_remaining,
    )


class _FakeSession:
    """Records commits; the SSE generator owns the commit (design note)."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeOrchestrator:
    """Scripted ``execute``: replays deltas, then resolves or raises."""

    def __init__(
        self,
        *,
        deltas: list[str] | None = None,
        outcome: LLMOutcome[_EchoResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.deltas = deltas if deltas is not None else []
        self.outcome = outcome
        self.error = error
        self.execute_calls = 0

    async def execute(
        self,
        plan: ProviderCallPlan[None, _EchoResult],
        *,
        on_delta: Any = None,
    ) -> LLMOutcome[_EchoResult]:
        self.execute_calls += 1
        for delta in self.deltas:
            if on_delta is not None:
                await on_delta(delta)
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


def _as_orchestrator(fake: object) -> LLMOrchestrator:
    return cast("LLMOrchestrator", fake)


async def _collect_events(response: StreamingResponse) -> list[tuple[str, dict[str, Any]]]:
    """Consume the stream and parse it into (event_type, payload) pairs."""
    raw = "".join([cast("str", chunk) async for chunk in response.body_iterator])
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        assert lines[0].startswith("event: ")
        event_type = lines[0][len("event: ") :]
        data = "\n".join(line[len("data: ") :] for line in lines[1:] if line.startswith("data: "))
        events.append((event_type, json.loads(data)))
    return events


# ---------------------------------------------------------------------------
# Event formatting.
# ---------------------------------------------------------------------------


def test_format_sse_event_shape() -> None:
    """One ``event:`` line, one ``data:`` line, blank-line terminator."""
    assert format_sse_event("delta", '{"text": "hi"}') == 'event: delta\ndata: {"text": "hi"}\n\n'


def test_format_sse_event_splits_multiline_data() -> None:
    """Multi-line payloads become consecutive ``data:`` lines (SSE spec)."""
    assert format_sse_event("error", "a\nb") == "event: error\ndata: a\ndata: b\n\n"


# ---------------------------------------------------------------------------
# Immediate ``prepare`` outcomes (reuse / cache hit / pre-call fallback).
# ---------------------------------------------------------------------------


async def test_immediate_outcome_emits_single_complete_event() -> None:
    """A cache/reuse hit streams exactly one ``complete`` terminal (Req 15.5)."""
    session = _FakeSession()
    envelope = _success_envelope()
    outcome: LLMOutcome[_EchoResult] = LLMOutcome(envelope=envelope, quota_remaining=5)
    orchestrator = _FakeOrchestrator()

    response = llm_stream_response(
        _as_orchestrator(orchestrator),
        outcome,
        session=session,  # type: ignore[arg-type]
    )
    events = await _collect_events(response)

    assert [event_type for event_type, _ in events] == [EVENT_COMPLETE]
    assert events[0][1]["is_fallback"] is False
    assert events[0][1]["id"] == envelope.id
    assert events[0][1]["result"] == {"message": "hello"}
    # No provider call ran and nothing was staged: no commit.
    assert orchestrator.execute_calls == 0
    assert session.commits == 0
    assert response.media_type == "text/event-stream"


async def test_immediate_fallback_emits_single_degraded_event() -> None:
    """A pre-call fallback (e.g. key absent) streams one ``degraded`` event."""
    session = _FakeSession()
    outcome: LLMOutcome[_EchoResult] = LLMOutcome(
        envelope=_fallback_envelope(FailureReason.LLM_UNAVAILABLE),
        quota_remaining=None,
    )

    response = llm_stream_response(
        _as_orchestrator(_FakeOrchestrator()),
        outcome,
        session=session,  # type: ignore[arg-type]
    )
    events = await _collect_events(response)

    assert [event_type for event_type, _ in events] == [EVENT_DEGRADED]
    assert events[0][1]["is_fallback"] is True
    assert events[0][1]["fallback_reason"] == "llm_unavailable"


# ---------------------------------------------------------------------------
# Provider-call plans.
# ---------------------------------------------------------------------------


async def test_plan_relays_deltas_then_complete_terminal() -> None:
    """Deltas stream in order; the staged rows commit before ``complete``."""
    session = _FakeSession()
    envelope = _success_envelope()
    orchestrator = _FakeOrchestrator(
        deltas=['{"message": ', '"hello"}'],
        outcome=LLMOutcome(envelope=envelope, quota_remaining=4),
    )

    response = llm_stream_response(
        _as_orchestrator(orchestrator),
        _plan(),
        session=session,  # type: ignore[arg-type]
    )
    events = await _collect_events(response)

    assert [event_type for event_type, _ in events] == [EVENT_DELTA, EVENT_DELTA, EVENT_COMPLETE]
    assert events[0][1] == {"text": '{"message": '}
    assert events[1][1] == {"text": '"hello"}'}
    assert events[2][1]["result"] == {"message": "hello"}
    assert session.commits == 1


async def test_plan_llm_failure_lands_as_degraded_terminal() -> None:
    """An LLM failure (timeout included, Req 11.5) ends in one ``degraded``.

    ``execute`` never raises for an LLM failure — it resolves to the
    fallback outcome, which the stream must deliver as the terminal even
    when deltas already flowed (Req 11.3).
    """
    session = _FakeSession()
    orchestrator = _FakeOrchestrator(
        deltas=['{"message": '],
        outcome=LLMOutcome(
            envelope=_fallback_envelope(FailureReason.TIMEOUT),
            quota_remaining=4,
        ),
    )

    response = llm_stream_response(
        _as_orchestrator(orchestrator),
        _plan(),
        session=session,  # type: ignore[arg-type]
    )
    events = await _collect_events(response)

    assert [event_type for event_type, _ in events] == [EVENT_DELTA, EVENT_DEGRADED]
    assert events[1][1]["fallback_reason"] == "timeout"
    # The failure-category invocation-log row was staged: still committed.
    assert session.commits == 1


async def test_unexpected_failure_emits_error_terminal() -> None:
    """An unexpected exception still terminates the stream (Req 11.3)."""
    session = _FakeSession()
    orchestrator = _FakeOrchestrator(
        deltas=['{"message": '],
        error=RuntimeError("infrastructure failure"),
    )

    response = llm_stream_response(
        _as_orchestrator(orchestrator),
        _plan(),
        session=session,  # type: ignore[arg-type]
    )
    events = await _collect_events(response)

    assert [event_type for event_type, _ in events] == [EVENT_DELTA, EVENT_ERROR]
    problem = events[1][1]
    assert problem["type"] == "internal_server_error"
    assert problem["status"] == 500
    # User-safe fixed copy: never the exception text (conventions.md).
    assert "infrastructure failure" not in json.dumps(problem)


async def test_failure_before_any_delta_still_gets_terminal_event() -> None:
    """A failure preceding every delta still yields a terminal (Req 11.3)."""
    session = _FakeSession()
    orchestrator = _FakeOrchestrator(deltas=[], error=RuntimeError("boom"))

    response = llm_stream_response(
        _as_orchestrator(orchestrator),
        _plan(),
        session=session,  # type: ignore[arg-type]
    )
    events = await _collect_events(response)

    assert [event_type for event_type, _ in events] == [EVENT_ERROR]


# ---------------------------------------------------------------------------
# Client disconnect (Req 11.7).
# ---------------------------------------------------------------------------


async def test_client_disconnect_cancels_provider_call() -> None:
    """Cancelling the stream consumer cancels the provider-call task."""

    class _HangingOrchestrator:
        def __init__(self) -> None:
            self.first_delta_sent = asyncio.Event()
            self.cancelled = False

        async def execute(
            self,
            plan: ProviderCallPlan[None, _EchoResult],
            *,
            on_delta: Any = None,
        ) -> LLMOutcome[_EchoResult]:
            assert on_delta is not None
            await on_delta("partial")
            self.first_delta_sent.set()
            try:
                await asyncio.Event().wait()  # never set: simulates a slow call
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

    session = _FakeSession()
    orchestrator = _HangingOrchestrator()
    response = llm_stream_response(
        _as_orchestrator(orchestrator),
        _plan(),
        session=session,  # type: ignore[arg-type]
    )

    stream = aiter(cast("AsyncIterator[str]", response.body_iterator))
    first = await anext(stream)
    assert "partial" in first  # the delta reached the client

    # The next read suspends at the internal queue; cancelling it models
    # Starlette's disconnect handling (CancelledError in the generator).
    next_read = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)  # let the read reach its suspension point
    next_read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_read

    assert orchestrator.cancelled is True  # the provider call was aborted
    assert session.commits == 0


# ---------------------------------------------------------------------------
# Quota header (Req 13.5).
# ---------------------------------------------------------------------------


async def test_quota_header_present_when_count_known() -> None:
    response = llm_stream_response(
        _as_orchestrator(_FakeOrchestrator()),
        LLMOutcome(envelope=_success_envelope(), quota_remaining=7),
        session=_FakeSession(),  # type: ignore[arg-type]
    )
    assert response.headers["X-LLM-Quota-Remaining"] == "7"
    assert response.headers["Cache-Control"] == "no-cache"


async def test_quota_header_omitted_when_count_unreadable() -> None:
    response = llm_stream_response(
        _as_orchestrator(_FakeOrchestrator()),
        LLMOutcome(
            envelope=_fallback_envelope(FailureReason.QUOTA_ACCOUNTING_UNAVAILABLE),
            quota_remaining=None,
        ),
        session=_FakeSession(),  # type: ignore[arg-type]
    )
    assert "X-LLM-Quota-Remaining" not in response.headers
