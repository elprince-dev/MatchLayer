"""Shared pytest fixtures for the API test suite.

Centralises the wiring that test modules under ``apps/api/tests/``
share. Today only :mod:`tests.test_health` imports these fixtures
(the sibling modules drive their subjects directly because they don't
need a fully wired FastAPI app), but every future router will land
here as an opt-in pair of ``client`` + dependency-override fixtures.

Three fixtures are exposed:

* :func:`app` — a fresh :class:`fastapi.FastAPI` per test, built via
  :func:`~matchlayer_api.main.create_app`. A new instance per test
  guarantees that dependency overrides registered on
  ``app.dependency_overrides`` cannot leak into a sibling test even
  if its teardown silently misbehaves.
* :func:`client` — an :class:`httpx.AsyncClient` driving the app
  through :class:`httpx.ASGITransport`. ``ASGITransport`` is the
  modern shape of httpx's in-process ASGI driver (the old
  ``app=`` shortcut is deprecated). It deliberately does NOT invoke
  the ASGI ``lifespan.startup`` event, which is exactly the behaviour
  Phase 1 unit-style tests want: the lifespan handler in
  :func:`~matchlayer_api.main.create_app` calls
  :func:`~matchlayer_api.core.db.verify_database_connection`, which
  requires a real Postgres. Lifespan-driven coverage of that probe
  lives in :mod:`tests.test_main`, which uses
  :class:`fastapi.testclient.TestClient` for that exact reason.
* :func:`override_get_session` — a factory fixture that registers a
  per-test stub for the :func:`~matchlayer_api.core.db.get_session`
  dependency. Tests call it with no arguments to install a
  ``execute()``-succeeds stub, or pass a :class:`SQLAlchemyError`
  instance to install a ``execute()``-raises stub. The fixture clears
  the override on teardown as belt-and-braces against a leaked
  override (the per-test ``app`` fixture already provides isolation;
  this is defence in depth).

Cleanup discipline: ``app.dependency_overrides`` is a plain dict on
the app instance. Even though every test gets its own app, the
teardown explicitly pops the override so the pattern stays correct
when ``app`` ever moves to a wider scope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.db import get_session
from matchlayer_api.main import create_app

# Type alias for the factory the override fixture exposes. Tests call
# the returned callable with either no argument (success path) or a
# single :class:`SQLAlchemyError` instance (failure path).
OverrideGetSession = Callable[[SQLAlchemyError | None], None]


@pytest.fixture
def app() -> FastAPI:
    """Return a fresh :class:`FastAPI` built from :func:`create_app`.

    A new instance per test ensures dependency overrides never leak
    across cases. :func:`create_app` reads the cached
    :func:`~matchlayer_api.config.get_settings` accessor, which is
    populated from the repo-level ``.env`` (per the foundation
    contract: ``cp .env.example .env`` is the prerequisite for any
    test run).
    """
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An :class:`httpx.AsyncClient` driving ``app`` via :class:`ASGITransport`.

    ``ASGITransport`` handles ``http.request``/``http.response``
    events but does NOT invoke the ASGI ``lifespan`` events — the
    startup probe in
    :func:`~matchlayer_api.core.db.verify_database_connection` is
    therefore skipped, which is the desired behaviour for tests that
    stub :func:`get_session` out entirely. Lifespan-driven coverage
    of the startup probe lives in :mod:`tests.test_main`.

    The base URL is the conventional ``http://testserver``; httpx
    requires *some* origin even for ASGI-driven traffic.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def override_get_session(app: FastAPI) -> Iterator[OverrideGetSession]:
    """Register a per-test stub for the ``get_session`` dependency.

    Returns a factory callable. Invoking it with no argument installs
    a stub whose :py:meth:`AsyncSession.execute` returns a benign
    :class:`MagicMock`; passing a :class:`SQLAlchemyError` (or any
    subclass) installs a stub whose ``execute`` raises that error
    instead. The stub itself is an ``async def`` generator so FastAPI
    treats it the same way it treats the real
    :func:`~matchlayer_api.core.db.get_session` — yield once, drain
    on cleanup.

    Two contracts the override holds:

    * The error instance passed by the test is the *exact* exception
      the route handler sees — no wrapping, no chaining. Tests can
      therefore assert on the response body for the absence of the
      original exception's message string (Requirement 4.14).
    * The stub session is a ``MagicMock(spec=AsyncSession)``, which
      means accessing any attribute the real ``AsyncSession`` doesn't
      have raises ``AttributeError``. The route handler in
      :mod:`matchlayer_api.api.health` only touches ``execute``, so
      this never matters today; it does mean future regressions that
      reach for, say, ``session.commit`` will fail loudly instead of
      silently no-op'ing through the mock.
    """

    def _install(error: SQLAlchemyError | None = None) -> None:
        async def _stub() -> AsyncIterator[AsyncSession]:
            session = MagicMock(spec=AsyncSession)
            execute_mock: AsyncMock
            if error is None:
                execute_mock = AsyncMock(return_value=MagicMock())
            else:
                execute_mock = AsyncMock(side_effect=error)
            session.execute = execute_mock
            yield session

        app.dependency_overrides[get_session] = _stub

    yield _install
    app.dependency_overrides.pop(get_session, None)


__all__ = [
    "OverrideGetSession",
    "app",
    "client",
    "override_get_session",
]
