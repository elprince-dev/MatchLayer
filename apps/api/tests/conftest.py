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

import asyncio
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from hypothesis import settings as hypothesis_settings
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.db import get_session
from matchlayer_api.main import create_app

# ---------------------------------------------------------------------------
# Session-end event-loop cleanup (task 16.9).
#
# ``pytest-asyncio`` (function loop scope) closes each test's event loop on
# teardown and then immediately installs a *fresh* replacement loop as the
# policy's current loop (see ``pytest_asyncio/plugin.py`` ``new_event_loop``).
# That replacement keeps the loop API usable for any between-test code, but
# the LAST replacement created in the session is never superseded — nothing
# closes it. At interpreter shutdown CPython GC-collects that orphaned
# ``_UnixSelectorEventLoop`` while it is still open, and
# ``BaseEventLoop.__del__`` emits ``ResourceWarning: unclosed event loop``
# plus two ``ResourceWarning: unclosed <socket.socket ... family=1>`` for the
# loop's AF_UNIX self-pipe (``socket.socketpair()`` in
# ``selector_events._make_self_pipe``). ``filterwarnings = ["error"]`` then
# promotes those three unraisable warnings into a session-end
# ``ExceptionGroup`` that makes pytest exit non-zero even though every test
# body passed — failing the foundation's ``backend`` CI check.
#
# Confirmed via ``PYTHONTRACEMALLOC=25``: all three leaked resources trace to
# ``pytest_asyncio/plugin.py`` ``_provide_clean_event_loop`` →
# ``policy.new_event_loop()``, not to any MatchLayer fixture or production
# code. pytest-asyncio installs a fresh "clean" loop after every async test
# so between-test ``get_event_loop()`` calls don't see a closed loop; the
# final such loop is never superseded and never closed.
#
# The fix closes that orphaned loop in ``pytest_unconfigure`` (which runs
# during ``config._ensure_unconfigure()``, before the unraisable-exception
# plugin's cleanup callback forces the ``gc.collect()`` that would otherwise
# surface the warning). ``gc.collect()`` is called first so any other
# dereferenced-but-not-finalized loops are reclaimed deterministically while
# warnings are suppressed, then the policy's current loop is closed. Both
# steps are guarded and idempotent.
# ---------------------------------------------------------------------------


def _close_orphaned_event_loop() -> None:
    """Close the leftover pytest-asyncio replacement loop (task 16.9)."""
    import gc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Reclaim any dereferenced-but-unfinalized loops while warnings are
        # suppressed, so their __del__ doesn't fire during the later
        # unraisable-collection gc pass that runs under error-as-warning.
        gc.collect()
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except Exception:
            return
        if loop is not None and not loop.is_closed():
            loop.close()
        gc.collect()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Close the orphaned event loop before unraisable-warning collection (task 16.9)."""
    _close_orphaned_event_loop()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Close the leftover pytest-asyncio replacement event loop (task 16.9)."""
    _close_orphaned_event_loop()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    """Close pytest-asyncio's orphaned replacement loop after *every* test.

    The session-end hooks above only catch the final leaked loop. The real
    failure mode is *mid-session*: after each async test, pytest-asyncio
    closes that test's loop and installs a fresh ``_UnixSelectorEventLoop``
    as the policy's current loop (an open, unused "clean" loop so any
    between-test ``get_event_loop()`` keeps working). When the *next* test
    installs its own replacement, the previous one is dereferenced and
    garbage-collected — and ``BaseEventLoop.__del__`` emits
    ``ResourceWarning: unclosed event loop`` plus two
    ``ResourceWarning: unclosed <socket.socket ...>`` for its AF_UNIX
    self-pipe. The builtin ``unraisableexception`` plugin collects those
    during teardown and ``filterwarnings = ["error"]`` escalates them into a
    session-failing ``ExceptionGroup`` attributed to whatever test happened
    to trigger the GC pass (hence the "floating" failure).

    This wrapper's post-``yield`` body runs after all fixture finalizers for
    the test (including pytest-asyncio's loop swap), so the policy's current
    loop is the freshly-installed, never-run replacement. Closing it here —
    while it is still referenced, so before it can be GC-collected — means no
    ``__del__`` warning ever fires, and it runs before the builtin unraisable
    plugin's own teardown-phase collection (conftest hookwrappers nest inside
    builtin ones, so their post-yield runs first). Closing an unused,
    not-running loop is safe: the next async test installs its own loop, and
    any between-test ``get_event_loop()`` call simply creates a new one.
    """
    yield
    _close_current_loop_if_idle()


def _close_current_loop_if_idle() -> None:
    """Close the policy's current event loop when it is open and not running."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except Exception:
            return
        if loop is not None and not loop.is_running() and not loop.is_closed():
            loop.close()


# ---------------------------------------------------------------------------
# Hypothesis configuration for the property-based test suite (phase-1-auth).
#
# Argon2id at the design's parameters (m=64MiB, t=2) takes >=80 ms per hash on
# a developer laptop. Hypothesis's default per-example deadline (200 ms) is
# too tight for hash-roundtrip properties, so the "auth" profile disables it.
# We also bump max_examples to 200 so the property is exercised meaningfully
# without dragging the suite past CI budgets.
# ---------------------------------------------------------------------------
hypothesis_settings.register_profile(
    "auth",
    deadline=None,
    max_examples=200,
)
hypothesis_settings.load_profile("auth")

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
