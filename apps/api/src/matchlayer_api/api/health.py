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
* On success the response is ``200 {"status": "ok"}``.
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

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.db import get_session

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

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ok"},
    )


__all__ = [
    "HealthResponse",
    "HealthUnhealthyResponse",
    "healthz",
    "router",
]
