"""Smoke tests for the application factory in :mod:`matchlayer_api.main`.

Covers Task 3.8 (Requirements 4.1, 4.12 / Design §6.1, §6.6, §6.8):

* :func:`create_app` returns a :class:`fastapi.FastAPI` instance.
* The factory wires the ``/healthz`` router so the route is registered
  on the app's router table.
* The middleware stack contains both
  :class:`~matchlayer_api.core.middleware.RequestIdMiddleware` and
  :class:`fastapi.middleware.cors.CORSMiddleware`, in the order the
  factory documents (CORS added first, RequestIdMiddleware added
  second so the resulting Starlette stack is RequestIdMiddleware →
  CORSMiddleware → routes on inbound — Starlette stores the LAST
  added at index 0 of ``user_middleware``).
* The lifespan handler awaits
  :func:`~matchlayer_api.core.db.verify_database_connection` at
  startup, propagating the underlying :class:`SQLAlchemyError` on
  failure (Requirement 4.12 — fail-fast at startup).
* The CORS allowlist is materialised from
  :attr:`Settings.cors_allowed_origins`, never from a wildcard.

Lifespan tests use :class:`fastapi.testclient.TestClient` because it
drives the ASGI ``lifespan.startup`` / ``lifespan.shutdown`` events on
``__enter__`` / ``__exit__`` — :class:`httpx.AsyncClient` with
:class:`httpx.ASGITransport` does not invoke lifespan, so it would
silently skip the probe and produce false-positive green tests.

Tests deliberately avoid real Postgres: the lifespan probe is patched
to either return ``None`` (success path) or raise
:class:`OperationalError` (failure path).
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from matchlayer_api.config import Environment, Settings
from matchlayer_api.core.middleware import RequestIdMiddleware
from matchlayer_api.main import create_app

# Environment kwargs that satisfy every required Settings field. Mirrors
# the helper used by ``test_errors`` so the two suites build comparable
# Settings instances without depending on the repo ``.env``.
_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": ["http://localhost:3000", "https://app.example.com"],
}


def _build_settings(environment: Environment = "development") -> Settings:
    """Return a Settings with the requested environment.

    Built from explicit kwargs rather than the cached ``get_settings``
    so each test owns its configuration and the factory's
    ``settings=`` override path is exercised end-to-end.
    """
    return Settings(environment=environment, **_BASE_SETTINGS_KWARGS)


# ---------------------------------------------------------------------------
# Shape of the returned app
# ---------------------------------------------------------------------------


def test_create_app_returns_fastapi_instance() -> None:
    """:func:`create_app` returns a :class:`fastapi.FastAPI` instance.

    Requirement 4.1 mandates "an explicit application-factory
    function (for example ``create_app()``)". A non-FastAPI return
    value (or a raise here) would fail the AC outright.
    """
    app = create_app(_build_settings())
    assert isinstance(app, FastAPI)


def test_healthz_route_is_registered() -> None:
    """The factory mounts the ``/healthz`` router from §3.7.

    Asserts on the app's router table rather than firing a request so
    the test stays decoupled from the lifespan and the DB probe.
    """
    app = create_app(_build_settings())
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/healthz" in paths


# ---------------------------------------------------------------------------
# Middleware stack
# ---------------------------------------------------------------------------


def test_middleware_stack_contains_request_id_and_cors() -> None:
    """Both required middlewares are registered exactly once."""
    app = create_app(_build_settings())
    # ``mw.cls`` is typed by starlette as ``_MiddlewareFactory[P]``, a
    # Protocol that mypy treats as incompatible with concrete
    # middleware classes. The runtime values ARE the concrete classes,
    # so identity comparison works correctly — the targeted ignore
    # documents the stub mismatch without weakening the assertion.
    classes: list[object] = [mw.cls for mw in app.user_middleware]
    assert RequestIdMiddleware in classes
    assert CORSMiddleware in classes
    assert classes.count(RequestIdMiddleware) == 1
    assert classes.count(CORSMiddleware) == 1


def test_request_id_middleware_runs_before_cors_on_inbound() -> None:
    """RequestIdMiddleware must wrap CORS on the inbound path.

    Starlette stores middlewares in ``user_middleware`` LIFO: the LAST
    ``add_middleware`` call ends up at index 0 and becomes the
    *outermost* wrapper, which is what runs FIRST on the inbound path.
    The factory documents the desired stack as
    ``RequestIdMiddleware → CORSMiddleware → routes`` on inbound, so
    RequestIdMiddleware must appear *before* CORSMiddleware in
    ``user_middleware`` (i.e., have a lower index).
    """
    app = create_app(_build_settings())
    # See note in ``test_middleware_stack_contains_request_id_and_cors``
    # — widening to ``list[object]`` lets identity-based ``index``
    # work without a ``_MiddlewareFactory[P]`` stub mismatch.
    classes: list[object] = [mw.cls for mw in app.user_middleware]
    request_id_index = classes.index(RequestIdMiddleware)
    cors_index = classes.index(CORSMiddleware)
    assert request_id_index < cors_index, (
        "RequestIdMiddleware must be added AFTER CORSMiddleware so it "
        "runs FIRST on the inbound path (Starlette stores user "
        "middleware LIFO; the last-added is outermost)."
    )


def test_cors_origins_come_from_settings_without_wildcard() -> None:
    """CORS allowlist is built from Settings, never falls back to ``*``.

    ``security.md`` Anti-patterns explicitly forbids ``*`` for
    authenticated endpoints; the factory satisfies this structurally.
    Pydantic's :class:`AnyHttpUrl` adds a trailing slash on render,
    which the factory strips because browsers compare the inbound
    ``Origin`` header literally and the header never carries one.
    """
    app = create_app(_build_settings())
    cors_options = next(mw.kwargs for mw in app.user_middleware if mw.cls is CORSMiddleware)
    # Narrow each accessed key to a concrete sequence type. ``mw.kwargs``
    # is typed as ``dict[str, Any]`` by starlette's stubs in some
    # versions but as ``dict[str, object]`` in newer ones; the casts
    # let mypy verify the assertions independently of stub drift.
    expose_headers = cast(list[str], cors_options["expose_headers"])
    allow_origins = cast(list[str], cors_options["allow_origins"])
    assert allow_origins == [
        "http://localhost:3000",
        "https://app.example.com",
    ]
    assert "*" not in allow_origins
    assert cors_options["allow_credentials"] is True
    assert cors_options["allow_methods"] == ["*"]
    assert cors_options["allow_headers"] == ["*"]
    assert "X-Request-Id" in expose_headers


def test_cors_origins_empty_list_when_settings_empty() -> None:
    """An empty allowlist must NOT fall back to a wildcard.

    Defensive test for ``security.md`` "Anti-patterns to refuse —
    Wildcard CORS on authenticated endpoints".
    """
    settings = Settings(
        environment="production",
        **{**_BASE_SETTINGS_KWARGS, "cors_allowed_origins": []},
    )
    app = create_app(settings)
    cors_options = next(mw.kwargs for mw in app.user_middleware if mw.cls is CORSMiddleware)
    assert cors_options["allow_origins"] == []


# ---------------------------------------------------------------------------
# Lifespan / startup probe (Requirement 4.12)
# ---------------------------------------------------------------------------


def test_lifespan_awaits_verify_database_connection() -> None:
    """Successful startup invokes ``verify_database_connection`` exactly once.

    :class:`fastapi.testclient.TestClient` drives the ASGI lifespan on
    ``__enter__`` / ``__exit__``; the probe is patched at the module
    where :func:`create_app` looks it up so the lifespan handler hits
    the mock instead of touching real Postgres.
    """
    probe = AsyncMock(return_value=None)
    with patch("matchlayer_api.main.verify_database_connection", probe):
        app = create_app(_build_settings())
        with TestClient(app):
            # Entering the TestClient context manager triggers the
            # ASGI ``lifespan.startup`` event and awaits the probe.
            pass
    probe.assert_awaited_once()


def test_lifespan_propagates_database_failure() -> None:
    """A failing startup probe propagates as a SQLAlchemyError.

    Requirement 4.12: "IF the API_App cannot establish a connection to
    Postgres during startup, THEN THE API_App SHALL log a structured
    error and exit with a non-zero status code rather than accept HTTP
    traffic."

    Starlette's :class:`TestClient` re-raises the original lifespan
    exception out of ``__enter__`` — the same path uvicorn takes when
    it exits non-zero in production.
    """
    probe = AsyncMock(side_effect=OperationalError("SELECT 1", {}, Exception("boom")))
    with patch("matchlayer_api.main.verify_database_connection", probe):
        app = create_app(_build_settings())
        with pytest.raises(OperationalError), TestClient(app):
            pass  # pragma: no cover - lifespan startup raises before this runs
    probe.assert_awaited_once()
