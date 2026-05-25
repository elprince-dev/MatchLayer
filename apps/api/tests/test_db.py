"""Tests for the async database engine, session factory, and lifespan probe.

Covers Requirements 4.10, 4.12, 4.13 and Design §6.6:

* The module-level engine is created with the design-mandated pool
  configuration (``pool_pre_ping=True``, ``pool_size=5``,
  ``max_overflow=5``).
* :data:`SessionLocal` is bound to the module-level engine and built
  with ``expire_on_commit=False`` so request-time serialization stays
  cheap.
* :func:`get_session` is an async generator that yields exactly one
  session and closes it on exit, even when the consumer raises.
* :func:`verify_database_connection` issues a ``SELECT 1`` against the
  engine on success and re-raises :class:`SQLAlchemyError` on failure
  (the contract uvicorn relies on for fail-fast startup).

The tests deliberately avoid hitting a real Postgres. Engine internals
are introspected directly (the pool API is part of SQLAlchemy's public
contract for this purpose) and ``verify_database_connection`` is
exercised against ``unittest.mock`` doubles. The module itself is
imported once at module load — its module-level :data:`engine` reads
``MATCHLAYER_DATABASE_URL`` from the repo's ``.env``, which already
points at the local docker-compose Postgres. The DSN is never connected
to in this test module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matchlayer_api.core import db

# ---------------------------------------------------------------------------
# Engine configuration — Design §6.6 / Requirement 4.13.
# ---------------------------------------------------------------------------


def test_engine_uses_async_dialect() -> None:
    """The engine must speak ``postgresql+asyncpg``.

    Requirement 4.10 mandates SQLAlchemy 2.x async; the asyncpg driver
    is the only async Postgres driver pinned in ``pyproject.toml``.
    """
    assert db.engine.dialect.name == "postgresql"
    # ``driver`` is the lowercase form of the dialect's async driver.
    assert db.engine.dialect.driver == "asyncpg"


def test_engine_pool_pre_ping_is_enabled() -> None:
    """``pool_pre_ping=True`` is the load-bearing flag for Requirement 4.13.

    Without it, asyncpg holds onto TCP connections that the database
    has already forgotten about (Postgres restart, idle timeout in a
    proxy, network blip). Requests then fail with cryptic
    ``InterfaceError`` instead of the pool quietly replacing the dead
    connection. The flag is part of the explicit design contract, so
    we assert on it directly.
    """
    # ``_pre_ping`` is SQLAlchemy's internal storage for the flag; the
    # public ``QueuePool`` API does not expose it as an attribute, but
    # the name has been stable across the 2.x line.
    assert db.engine.pool._pre_ping is True  # type: ignore[attr-defined]


def test_engine_pool_size_matches_design() -> None:
    """Five long-lived connections per process (Design §6.6)."""
    # ``size()`` lives on ``QueuePool``, the default implementation for
    # an async engine without ``poolclass=NullPool``; the abstract
    # ``Pool`` base does not advertise it, so we narrow with cast.
    assert db.engine.pool.size() == 5  # type: ignore[attr-defined]


def test_engine_pool_max_overflow_matches_design() -> None:
    """Five additional burst connections (Design §6.6)."""
    # ``_max_overflow`` is the canonical attribute name on QueuePool;
    # exposed publicly through ``Pool.recreate(max_overflow=...)`` but
    # not as a getter, so we read the attribute directly.
    assert db.engine.pool._max_overflow == 5  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SessionLocal — async_sessionmaker bound to the module engine.
# ---------------------------------------------------------------------------


def test_session_local_is_async_sessionmaker_bound_to_engine() -> None:
    """``SessionLocal`` must be an :func:`async_sessionmaker` for the engine."""
    assert isinstance(db.SessionLocal, async_sessionmaker)
    # ``kw["bind"]`` is where async_sessionmaker stashes the engine it
    # was built with. Asserting this catches accidental rebinding to a
    # stray sync engine.
    assert db.SessionLocal.kw["bind"] is db.engine


def test_session_local_disables_expire_on_commit() -> None:
    """``expire_on_commit=False`` keeps post-commit ORM access cheap.

    With the default (``True``), every attribute access on an ORM
    object after commit triggers a fresh SELECT against a session
    whose connection has already been returned to the pool — which is
    exactly the wrong shape for a FastAPI handler that commits and
    then serializes the result.
    """
    assert db.SessionLocal.kw["expire_on_commit"] is False


# ---------------------------------------------------------------------------
# get_session — FastAPI dependency contract.
# ---------------------------------------------------------------------------


async def test_get_session_yields_session_then_closes_it() -> None:
    """``get_session`` opens, yields, and closes a session per call.

    The session itself is replaced with a ``MagicMock`` configured to
    behave like an async context manager so the test exercises the
    real ``async with`` handshake without touching Postgres.
    """
    fake_session = MagicMock(spec=AsyncSession)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    fake_factory = MagicMock(return_value=fake_session)

    with patch.object(db, "SessionLocal", fake_factory):
        agen: AsyncIterator[AsyncSession] = db.get_session()
        yielded = await agen.__anext__()
        assert yielded is fake_session
        # The factory must have been invoked exactly once and the
        # async-context-manager entry must have happened by now.
        fake_factory.assert_called_once_with()
        fake_session.__aenter__.assert_awaited_once()
        # Session is still open while the dependency is "yielded".
        fake_session.__aexit__.assert_not_called()

        # Drain the generator. ``StopAsyncIteration`` is expected — the
        # body of ``get_session`` falls off the end after the ``yield``.
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

    # ``__aexit__`` runs as the ``async with`` unwinds when the
    # generator exits.
    fake_session.__aexit__.assert_awaited_once()


async def test_get_session_closes_session_when_consumer_raises() -> None:
    """The session must be closed even when the route handler raises.

    FastAPI propagates exceptions raised by route handlers through the
    dependency generator via :py:meth:`agen.athrow`. The ``async with``
    inside ``get_session`` is responsible for releasing the connection
    in that case.
    """

    class _BoomError(RuntimeError):
        """Marker exception for this test only."""

    fake_session = MagicMock(spec=AsyncSession)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_factory = MagicMock(return_value=fake_session)

    with patch.object(db, "SessionLocal", fake_factory):
        agen = db.get_session()
        await agen.__anext__()
        # ``athrow`` is a coroutine on async generators (PEP 525);
        # mypy's stubs for ``AsyncIterator`` don't expose it, so we
        # narrow to the concrete type at use-site.
        with pytest.raises(_BoomError):
            await agen.athrow(_BoomError("simulated route failure"))  # type: ignore[attr-defined]

    fake_session.__aexit__.assert_awaited_once()
    # The ``async with`` was given an exception triple — assert the
    # exception class made it through so future refactors that swallow
    # the exception inside ``get_session`` are caught here.
    aexit_args, _aexit_kwargs = fake_session.__aexit__.call_args
    assert aexit_args[0] is _BoomError


# ---------------------------------------------------------------------------
# verify_database_connection — Requirement 4.12.
# ---------------------------------------------------------------------------


def _patched_engine_with_connect_result(
    connection_mock: MagicMock | AsyncMock,
) -> MagicMock:
    """Build a MagicMock engine whose ``connect()`` returns ``connection_mock``.

    SQLAlchemy's :py:meth:`AsyncEngine.connect` returns an async
    context manager whose ``__aenter__`` resolves to an
    :class:`AsyncConnection`. The helper wires that contract up onto a
    MagicMock so tests can substitute the engine without instantiating
    a real one.
    """
    fake_engine = MagicMock()
    fake_engine.connect = MagicMock(return_value=connection_mock)
    return fake_engine


async def test_verify_database_connection_executes_select_1_on_success() -> None:
    """The probe must issue ``SELECT 1`` once and return None on success."""
    fake_connection = MagicMock()
    fake_connection.__aenter__ = AsyncMock(return_value=fake_connection)
    fake_connection.__aexit__ = AsyncMock(return_value=None)
    fake_connection.execute = AsyncMock(return_value=MagicMock())

    fake_engine = _patched_engine_with_connect_result(fake_connection)

    with patch.object(db, "engine", fake_engine):
        # ``verify_database_connection`` returns ``None`` on success;
        # ``await`` on a value-less coroutine is the contract we want
        # to assert. Suppressing the lint here keeps the assertion
        # readable.
        await db.verify_database_connection()

    fake_engine.connect.assert_called_once_with()
    fake_connection.__aenter__.assert_awaited_once()
    fake_connection.__aexit__.assert_awaited_once()

    fake_connection.execute.assert_awaited_once()
    (executed_clause,), _kwargs = fake_connection.execute.call_args
    # ``text("SELECT 1")`` builds a TextClause; the SQL stays attached
    # as the ``.text`` attribute. Asserting on the rendered SQL lets
    # the test outlive any future change to the imported helper name.
    assert executed_clause.text == "SELECT 1"
    # And the helper produced a TextClause, not a raw string (raw
    # strings would bypass parameter binding).
    assert isinstance(executed_clause, type(text("SELECT 1")))


async def test_verify_database_connection_reraises_sqlalchemy_error() -> None:
    """Failure must propagate so uvicorn's lifespan startup exits non-zero.

    The exception class on the re-raise must be the original — wrapping
    it would hide the DBAPIError class names operators rely on for
    troubleshooting (e.g., ``OperationalError`` for "could not connect
    to server").
    """
    boom = OperationalError("SELECT 1", params=None, orig=Exception("connection refused"))

    fake_connection = MagicMock()
    # ``connect()`` is the call that fails for "DSN unreachable" /
    # "credentials rejected" / "DB not listening" — exactly the
    # scenarios Requirement 4.12 asks us to fail-fast on.
    fake_connection.__aenter__ = AsyncMock(side_effect=boom)
    fake_connection.__aexit__ = AsyncMock(return_value=None)

    fake_engine = _patched_engine_with_connect_result(fake_connection)

    with patch.object(db, "engine", fake_engine), pytest.raises(OperationalError) as exc_info:
        await db.verify_database_connection()

    # The same exception object propagates up — no wrapping, no
    # re-instantiation, so callers can ``except OperationalError`` and
    # have it work as expected.
    assert exc_info.value is boom


async def test_verify_database_connection_reraises_when_select_fails() -> None:
    """Errors raised from the SELECT itself also propagate."""
    boom = SQLAlchemyError("query failure")

    fake_connection = MagicMock()
    fake_connection.__aenter__ = AsyncMock(return_value=fake_connection)
    fake_connection.__aexit__ = AsyncMock(return_value=None)
    fake_connection.execute = AsyncMock(side_effect=boom)

    fake_engine = _patched_engine_with_connect_result(fake_connection)

    with patch.object(db, "engine", fake_engine), pytest.raises(SQLAlchemyError) as exc_info:
        await db.verify_database_connection()

    assert exc_info.value is boom
    # The connection was opened *before* the SELECT failed, so it must
    # still be released — that's what the ``async with`` guarantees.
    fake_connection.__aexit__.assert_awaited_once()


async def test_verify_database_connection_logs_error_class_without_dsn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log line must carry the exception class but NEVER the DSN.

    ``security.md`` "Logging & audit": connection strings are
    Confidential and must never appear in log output. The handler logs
    only ``error_class``; this test asserts the negative — the DSN
    string from the active settings does not show up in any captured
    log record's text.
    """
    boom = OperationalError("SELECT 1", params=None, orig=Exception("connection refused"))

    fake_connection = MagicMock()
    fake_connection.__aenter__ = AsyncMock(side_effect=boom)
    fake_connection.__aexit__ = AsyncMock(return_value=None)

    fake_engine = _patched_engine_with_connect_result(fake_connection)

    with (
        patch.object(db, "engine", fake_engine),
        structlog.testing.capture_logs() as captured,
        pytest.raises(OperationalError),
    ):
        await db.verify_database_connection()

    failure_events = [
        event for event in captured if event.get("event") == "database_startup_probe_failed"
    ]
    assert len(failure_events) == 1
    event = failure_events[0]
    assert event["error_class"] == "OperationalError"
    assert event["log_level"] == "error"

    # Defense-in-depth assertion: the rendered DSN from the active
    # settings must not appear in any captured log record. The local
    # ``.env`` ships ``dev_only_password`` as the Postgres password —
    # if any future change starts logging the DSN by accident, this
    # check fires.
    rendered = repr(captured)
    assert "dev_only_password" not in rendered
    assert "asyncpg" not in rendered
