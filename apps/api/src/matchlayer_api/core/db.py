"""Async database engine, session factory, and per-request dependency.

The MatchLayer API talks to Postgres exclusively through SQLAlchemy 2.x's
async API. This module owns the three pieces every other layer reuses:

* :data:`engine` — a process-wide :class:`AsyncEngine` configured with
  ``pool_pre_ping=True``, ``pool_size=5``, and ``max_overflow=5`` per
  Design §6.6. The pre-ping makes runtime connection loss recoverable
  (Requirement 4.13): a stale or broken connection is detected at
  checkout and replaced rather than handed to a route handler.
* :data:`SessionLocal` — an :func:`async_sessionmaker` bound to the
  engine. ``expire_on_commit=False`` is the canonical pairing with
  request-scoped sessions in FastAPI: ORM-loaded objects stay usable
  in the response serialization step without forcing a refetch.
* :func:`get_session` — an ``async`` generator suitable as a FastAPI
  dependency. It opens a session, yields it for the duration of the
  request, and closes it when the response has been sent (the
  ``async with`` guarantees release even when the route raises). Tests
  override this dependency through FastAPI's
  ``app.dependency_overrides`` mapping rather than monkey-patching
  module globals.

The :func:`verify_database_connection` lifespan helper runs ``SELECT 1``
once at startup. Failure is fatal — the coroutine re-raises the
underlying :class:`sqlalchemy.exc.SQLAlchemyError` so uvicorn's
lifespan startup fails, the process exits non-zero, and the
orchestrator (Fly.io machine, ECS task, docker-compose) notices a
broken deploy instead of routing real traffic at it (Requirement 4.12).
This is the fail-fast-at-startup half of the design's "fail-fast at
startup, fail-soft at runtime" contract — the runtime half is owned by
``/healthz`` (Design §6.5) and individual route handlers, which surface
``database_unreachable`` rather than crashing the process.

Settings are read once at module import via :func:`get_settings`, the
cached accessor from ``config.py``. Reading the URL at import time —
rather than rebuilding the engine per request — keeps the connection
pool warm across requests and matches the shape Design §6.6 prescribes.
Tests that want to point at a different database use FastAPI's
dependency override on :func:`get_session`; production deployments rely
on environment variables already being correct by the time this module
imports (which is the same contract every other ``core/`` module has
with :class:`~matchlayer_api.config.Settings`).

Design reference: §6.6.
Requirements covered: 4.10, 4.12, 4.13.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from matchlayer_api.config import get_settings

# Module-level logger. The request-id middleware binds ``request_id`` /
# ``route`` / ``method`` to the structlog contextvar at the start of
# every request, so log lines emitted from session-scoped code inherit
# them automatically. The startup probe runs *before* any request, so
# its log lines carry no request_id — that's expected.
_log = structlog.get_logger(__name__)

# Cached, validated settings. Reading at module import time means a
# misconfigured ``MATCHLAYER_DATABASE_URL`` raises a Pydantic
# ``ValidationError`` before FastAPI gets a chance to bind a port —
# the fail-fast behavior Requirement 4.3 requires.
_settings = get_settings()


# Engine sizing rationale (Design §6.6):
# * ``pool_size=5`` — five long-lived connections per process. With
#   uvicorn running a handful of worker processes and Phase 1's
#   read-light workload, this comfortably absorbs the access pattern.
# * ``max_overflow=5`` — five additional burst connections under load.
#   Past ten concurrent in-flight queries per worker we'd rather queue
#   than open unbounded sockets at Postgres.
# * ``pool_pre_ping=True`` — issues a one-roundtrip liveness check on
#   each connection checkout. Cheap, and the only reliable defense
#   against the long-tail "asyncpg held a TCP connection that the DB
#   has already forgotten about" failure mode that surfaces after
#   Postgres restarts, network blips, or idle-connection timeouts in
#   front-of-Postgres pgbouncer-like proxies.
# * ``str(...)`` — Pydantic's ``PostgresDsn`` is a typed URL object;
#   SQLAlchemy expects a string. ``str()`` round-trips it back to the
#   canonical ``postgresql+asyncpg://...`` form the user supplied.
engine: AsyncEngine = create_async_engine(
    str(_settings.database_url),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)


# ``async_sessionmaker`` is a typed factory class — annotating it with
# its ``AsyncSession`` type parameter keeps mypy strict-clean across
# every router that depends on :func:`get_session`. ``expire_on_commit``
# is False because FastAPI serializes ORM objects after the route
# handler returns; with ``expire_on_commit=True`` (the default), every
# attribute access after commit would trigger a fresh SELECT against a
# session whose connection has already been released.
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a fresh :class:`AsyncSession` for the duration of one request.

    FastAPI dependency. The ``async with`` block guarantees the session
    is closed (and its connection returned to the pool) even when the
    route handler raises — including the catch-all paths covered by the
    RFC 7807 error handlers in :mod:`matchlayer_api.core.errors`.

    Tests override this dependency via FastAPI's ``dependency_overrides``
    mapping rather than monkey-patching this function or the module-level
    :data:`SessionLocal`. The override pattern is documented in the
    forthcoming ``tests/conftest.py`` (task 3.11).

    Yields:
        AsyncSession: A session bound to the module-level engine. The
        caller MUST NOT call ``session.close()`` directly — the
        surrounding ``async with`` owns lifecycle.
    """
    async with SessionLocal() as session:
        yield session


async def verify_database_connection() -> None:
    """Run ``SELECT 1`` against the database; raise on failure.

    Invoked from the FastAPI lifespan handler at startup. The contract
    is intentionally narrow: the probe either succeeds silently or
    re-raises the original :class:`SQLAlchemyError`. Lifespan startup
    failures propagate up through uvicorn, which then exits with a
    non-zero status — exactly the fail-fast behavior Requirement 4.12
    mandates.

    A connection is opened directly off :data:`engine` (rather than
    going through :data:`SessionLocal`) because this probe runs before
    the application has bound any HTTP listener: there is no
    request-scoped session to inherit, and we deliberately do not want
    a partial Session object lying around if the probe fails.

    Raises:
        SQLAlchemyError: If connecting to or querying Postgres fails
            for any reason — DSN unreachable, credentials rejected,
            schema missing, etc. The exception class name is logged
            via ``error_class`` so operators have a correlation handle
            in the audit trail; the original DSN and credentials are
            NEVER logged (security.md "Logging & audit").
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # ``log.error`` rather than ``log.exception`` — the traceback
        # is preserved on the re-raise for uvicorn to surface, and we
        # don't want the structured log line carrying a stack trace
        # that may incidentally include the DSN through chained
        # exception messages.
        _log.error(
            "database_startup_probe_failed",
            error_class=type(exc).__name__,
        )
        raise


__all__ = [
    "SessionLocal",
    "engine",
    "get_session",
    "verify_database_connection",
]
