"""Liveness/readiness endpoint: ``GET /healthz``.

Exposes the single endpoint the production runtime, the container
healthcheck (Design §11.1), and Phase 6 load balancers will probe to
decide whether this API process is healthy.

Behaviour follows Design §6.5 / Requirements 4.7-4.9:

* The handler executes ``SELECT 1`` against the request-scoped
  :class:`AsyncSession` yielded by
  :func:`~matchlayer_api.core.db.get_session`. Reusing the same
  dependency every other route uses means ``/healthz`` reflects the
  exact connection path real traffic takes — pool checkout,
  ``pool_pre_ping``, asyncpg socket — not a parallel codepath that
  could pass while real requests fail.
* On success the response is
  ``200 {"status": "ok", "semantic_scoring": ..., "llm": ...}`` where
  both subsystem fields carry ``"available" | "unavailable"``.
  The ``semantic_scoring`` field (Phase 2, Requirement 7.5) reports
  semantic-pipeline availability via
  :func:`~matchlayer_api.ml.semantic_adapter.semantic_available` and
  never changes the status code — a Degraded_Mode instance still
  reports serving.
* The ``llm`` field (Phase 3, Requirements 10.1, 10.2, 10.5, 10.6)
  follows the same additive pattern: ``unavailable`` iff the provider
  API key was absent at startup
  (:func:`~matchlayer_api.ml.llm.availability.llm_key_present`) or the
  Spend_Circuit_Breaker is open (the memoized
  :attr:`~matchlayer_api.services.llm.spend.SpendCircuitBreaker.state`
  — no storage query per probe, design decision D5). The value never
  changes the 200 status and never exposes the key, spend figures, or
  provider account details. Recovery — a valid key at the next
  startup, a UTC month rollover, or a raised spend limit — flips it
  back to ``available`` without a code change (Requirement 10.4),
  because both sources are re-read on every probe.
* On any :class:`SQLAlchemyError` the response is
  ``503 {"status": "unhealthy", "reason": "database_unreachable"}``
  and a structured warning log line is emitted carrying ONLY the
  exception class name (``security.md`` "Logging & audit": DSN and
  credentials are Confidential and never logged or returned).

Two distinct Pydantic response models are declared so the OpenAPI
schema FastAPI emits at ``app.openapi()`` types each branch precisely.
This matters for two downstream consumers:

* ``apps/api/src/matchlayer_api/tools/dump_openapi.py`` (task 3.10)
  dumps the live spec.
* ``packages/shared-types`` (task 5.4) re-exports the curated
  ``HealthResponse`` alias derived from
  ``paths["/healthz"]["get"]["responses"]["200"]`` — a precise type
  on the 200 response only emerges if the router declares it
  explicitly.

Tests for this router land in task 3.11; the implementation here is
intentionally shaped to make those tests trivial — override
``get_session`` with a stub that either returns or raises, drive a
single ``GET /healthz`` request, assert on the status code and JSON
body.

Design reference: §6.5.
Requirements covered: 4.7, 4.8, 4.9.
"""

from __future__ import annotations

import time
from typing import Annotated, Final, Literal

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.db import get_session
from matchlayer_api.ml.llm.availability import llm_key_present
from matchlayer_api.ml.semantic_adapter import semantic_available
from matchlayer_api.services.agent_jobs.queue import get_job_queue
from matchlayer_api.services.llm.spend import get_spend_circuit_breaker

# Module-level logger. The request-id middleware (§6.4) binds
# ``request_id`` / ``route`` / ``method`` to a structlog contextvar at
# the start of the request, so the warning emitted on the failure path
# inherits them automatically — operators correlate the failed probe
# with the surrounding access-log line via the shared request_id.
_log = structlog.get_logger(__name__)

# Reason code returned on the 503 path. A symbolic, machine-readable
# token rather than a human-readable sentence — Phase 6 load balancers
# and downstream alerting can branch on it without string parsing, and
# it carries zero PII or DSN content (Requirement 4.9).
_REASON_DATABASE_UNREACHABLE = "database_unreachable"

# ---------------------------------------------------------------------------
# Agents (Job_Queue) availability — phase-4-agentic Requirements 16.1, 16.6.
#
# ``JobQueue.healthcheck()`` performs a real GetQueueAttributes round trip
# under a short timeout; probing SQS on *every* /healthz hit would make the
# liveness endpoint pay a network round trip per probe. The design (§7
# "/healthz") therefore caches the boolean outcome for ~10 seconds — long
# enough to keep healthz cheap under orchestration-frequency probing, short
# enough that recovery (LocalStack/SQS coming back) is observed within one
# cache window. The cached value is the *boolean only*: no queue URL,
# endpoint address, or credential is ever held or returned (Req 16.6).
# ---------------------------------------------------------------------------

_AGENTS_HEALTH_CACHE_TTL_SECONDS: Final[float] = 10.0

# ``(recorded_at_monotonic, reachable)`` — module-level so every request
# shares one cache window per process. Tests reset it via monkeypatch.
_agents_cache: tuple[float, bool] | None = None


async def _agents_available() -> bool:
    """Return Job_Queue reachability, memoized for ~10 seconds.

    Delegates to :meth:`~matchlayer_api.services.agent_jobs.queue.JobQueue.healthcheck`,
    which never raises (failures resolve to ``False`` after its internal
    short timeout and emit at most one structured warning carrying the
    exception class name only — never the queue URL or credentials,
    Requirement 16.6).
    """
    global _agents_cache
    now = time.monotonic()
    if _agents_cache is not None and (now - _agents_cache[0]) < _AGENTS_HEALTH_CACHE_TTL_SECONDS:
        return _agents_cache[1]
    reachable = await get_job_queue().healthcheck()
    _agents_cache = (time.monotonic(), reachable)
    return reachable


class HealthResponse(BaseModel):
    """Body returned on the success path: ``{"status": "ok"}``.

    Typed with :class:`typing.Literal` so the generated OpenAPI schema
    pins the response to that exact shape, which in turn produces a
    precise type when :mod:`packages/shared-types` re-exports it as
    ``HealthResponse`` (task 5.4).
    """

    status: Literal["ok"] = Field(
        default="ok",
        description="Liveness signal. Always the literal string 'ok' on a 200 response.",
    )
    semantic_scoring: Literal["available", "unavailable"] = Field(
        description=(
            "Phase 2 semantic-pipeline availability (Requirement 7.5). "
            "'available' when the Embedding_Model + spaCy pipeline loaded at "
            "startup; 'unavailable' in Degraded_Mode. Exactly these two "
            "machine-readable values — a degraded instance still returns 200 "
            "so orchestration never restart-loops it; operators detect "
            "Degraded_Mode from this field without reading logs."
        ),
    )
    llm: Literal["available", "unavailable"] = Field(
        description=(
            "Phase 3 LLM-subsystem availability (Requirement 10.1). "
            "'unavailable' iff the provider API key was absent at startup "
            "or the Spend_Circuit_Breaker is open (LLM_Unavailable, "
            "Requirement 10.2); 'available' otherwise (Requirement 10.6). "
            "Exactly these two machine-readable values — the field never "
            "changes the HTTP status code and never exposes the API key, "
            "spend figures, or provider account details (Requirement 10.5)."
        ),
    )
    agents: Literal["available", "unavailable"] = Field(
        description=(
            "Phase 4 agent-subsystem availability (phase-4-agentic "
            "Requirements 16.1, 16.6). 'available' means the Job_Queue "
            "(SQS) was reachable from this API process at the time of "
            "evaluation (result cached ~10 s to keep the probe cheap); "
            "'unavailable' means it was not. Exactly these two "
            "machine-readable values — the field never changes the 200 "
            "status and never exposes queue URLs, endpoint addresses, or "
            "credentials."
        ),
    )


class HealthUnhealthyResponse(BaseModel):
    """Body returned on the failure path.

    Surfaced in the OpenAPI ``responses`` map for ``503`` so the
    contract is explicit: the response carries a short symbolic
    ``reason`` token and never a DSN, credentials, or any PII.
    """

    status: Literal["unhealthy"] = Field(
        default="unhealthy",
        description="Liveness signal. Always the literal string 'unhealthy' on a 503 response.",
    )
    reason: Literal["database_unreachable"] = Field(
        description=(
            "Symbolic, machine-readable failure code. Phase 6 load balancers "
            "and alerting branch on this token; it never contains DSN, "
            "credentials, or PII."
        ),
    )


# Router rather than direct ``app.get`` decoration so the application
# factory in ``main.py`` (task 3.8) can include this router alongside
# future feature routers via a single ``app.include_router(...)`` call.
# No ``prefix`` — Design §6.5 / Requirement 4.7 mount ``/healthz`` at
# the root; the production Dockerfile healthcheck (§11.1) probes
# ``http://127.0.0.1:8000/healthz`` directly.
router = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness/readiness probe with Postgres connectivity check.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": HealthUnhealthyResponse,
            "description": (
                "Postgres is unreachable. The probe never returns DSN or "
                "credentials in the response body."
            ),
        },
    },
)
async def healthz(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    """Probe Postgres and return the canonical health envelope.

    The handler intentionally returns :class:`fastapi.responses.JSONResponse`
    rather than the Pydantic model directly so the failure branch can
    set the 503 status code without raising an exception (which would
    route through the RFC 7807 catch-all in
    :mod:`matchlayer_api.core.errors` and produce the wrong response
    shape for a healthcheck).

    Args:
        session: Request-scoped async SQLAlchemy session, yielded by
            :func:`~matchlayer_api.core.db.get_session`. Tests override
            this dependency via FastAPI's ``app.dependency_overrides``
            mapping (task 3.11).

    Returns:
        :class:`JSONResponse` with status 200 and body ``{"status": "ok"}``
        when the ``SELECT 1`` probe succeeds; status 503 and body
        ``{"status": "unhealthy", "reason": "database_unreachable"}``
        when SQLAlchemy raises any subclass of :class:`SQLAlchemyError`.
    """
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Log only the exception class name. ``security.md`` "Logging
        # & audit" forbids DSN / credentials / PII in log output;
        # ``str(exc)`` from SQLAlchemy can chain in the original
        # asyncpg error which sometimes carries connection details, so
        # we deliberately do not include it.
        _log.warning(
            "healthz_db_probe_failed",
            error_class=type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "reason": _REASON_DATABASE_UNREACHABLE},
        )

    # Phase 2 (Requirement 7.5): report semantic-pipeline availability as a
    # machine-readable field with exactly two values. The mapping is
    # ``semantic_available() -> "available" | "unavailable"``; it never
    # affects the status code — a Degraded_Mode instance is still serving,
    # so it still returns 200 and orchestration does not restart-loop it.
    semantic_scoring: Literal["available", "unavailable"] = (
        "available" if semantic_available() else "unavailable"
    )

    # Phase 3 (Requirements 10.1, 10.2, 10.5, 10.6): report LLM-subsystem
    # availability the same way. LLM_Unavailable has exactly two causes —
    # the provider API key was absent at startup, or the app-wide
    # Spend_Circuit_Breaker is open — and the field composes both. The
    # breaker read is the memoized process-local snapshot (design decision
    # D5): no storage query per probe. Both sources are re-read on every
    # request, so recovery (key at next startup, UTC month rollover, or a
    # raised limit closing the breaker at its next evaluation) flips the
    # value back without a code change (Requirement 10.4). Like
    # ``semantic_scoring``, the value never affects the 200 status, and it
    # carries no key material, spend figures, or provider account details.
    llm_available = llm_key_present() and not get_spend_circuit_breaker().state.is_open
    llm: Literal["available", "unavailable"] = "available" if llm_available else "unavailable"

    # Phase 4 (phase-4-agentic Requirements 16.1, 16.6): report Job_Queue
    # reachability the same additive way. The probe result is memoized for
    # ~10 s (see ``_agents_available``); the value never affects the 200
    # status — an instance whose queue is down still serves every
    # synchronous endpoint — and the body carries no queue URL, endpoint
    # address, or credential in either state.
    agents: Literal["available", "unavailable"] = (
        "available" if await _agents_available() else "unavailable"
    )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ok",
            "semantic_scoring": semantic_scoring,
            "llm": llm,
            "agents": agents,
        },
    )


__all__ = [
    "HealthResponse",
    "HealthUnhealthyResponse",
    "healthz",
    "router",
]
