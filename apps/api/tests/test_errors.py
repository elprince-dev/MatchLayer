"""Tests for the RFC 7807 error handlers.

Covers Requirement 4.13 and Design §6.8:

* :class:`~matchlayer_api.core.errors.MatchLayerError` is rendered with
  the canonical ``{type, title, detail, status, request_id}`` envelope.
* :class:`fastapi.exceptions.RequestValidationError` produces a 422
  with a ``detail`` string that summarizes the validation failure but
  does NOT echo any user-supplied input value (PII protection per
  ``security.md`` data classification).
* The catch-all :class:`Exception` handler:
    - In development, the detail line surfaces ``ExceptionClass: message``
      for debuggability.
    - In production, the detail line is generic; the original
      exception class name and message are *not* present in the response
      body — but the exception is still captured by the logger.
* ``request_id`` is sourced from the structlog contextvar bound by
  :class:`~matchlayer_api.core.middleware.RequestIdMiddleware`, so the
  RequestId middleware + error handlers compose end-to-end.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from matchlayer_api.config import Environment, Settings
from matchlayer_api.core.errors import MatchLayerError, register_exception_handlers
from matchlayer_api.core.middleware import RequestIdMiddleware

# A canary string we attach to test exception messages and inputs.
# Asserting its absence in production responses is the load-bearing
# test for "the catch-all handler does not leak the original
# exception" (Design §6.8).
_LEAKY_MARKER = "leaky-exception-detail-canary-9f3c"

# All the env vars Settings requires. Tests construct Settings directly
# so they don't depend on the repo's ``.env`` and so the ``environment``
# field can be parameterized per test.
_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
}


def _build_settings(environment: Environment) -> Settings:
    """Return a Settings instance with the given environment.

    Avoids touching the LRU cache on :func:`get_settings`; tests pass
    the constructed Settings directly to ``register_exception_handlers``.
    """
    return Settings(environment=environment, **_BASE_SETTINGS_KWARGS)


# ---------------------------------------------------------------------------
# Pydantic input model used by the RequestValidationError test. Field
# names are deliberately PII-flavoured ("password") so we can assert the
# user-supplied value never round-trips into the response body.
# ---------------------------------------------------------------------------


class _LoginInput(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=12)


def _build_app(environment: Environment) -> FastAPI:
    """Construct a FastAPI app wired with the error handlers under test.

    The :class:`RequestIdMiddleware` is included so the ``request_id``
    contextvar is bound during request handling — that's the source of
    truth the error handlers read from.
    """
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app, settings=_build_settings(environment))

    @app.get("/raise-matchlayer")
    async def _raise_matchlayer() -> None:
        raise MatchLayerError(
            "Max upload size is 5MB",
            status_code=413,
            error_type="resume_too_large",
            title="Resume file too large",
        )

    @app.get("/raise-default-matchlayer")
    async def _raise_default_matchlayer() -> None:
        # Exercises the class-level defaults (no kwargs supplied).
        raise MatchLayerError("Something inherent failed.")

    @app.get("/boom")
    async def _boom() -> None:
        raise RuntimeError(_LEAKY_MARKER)

    @app.post("/login")
    async def _login(_payload: _LoginInput) -> dict[str, str]:
        return {"status": "ok"}  # pragma: no cover - never reached in tests

    return app


@pytest.fixture(autouse=True)
def _clear_contextvars() -> Iterator[None]:
    """Make sure structlog contextvars are clean between tests.

    The middleware clears them at the end of every request, but tests
    that drive handlers without going through the middleware (or that
    inspect contextvars after the request) benefit from a hard reset.
    """
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# MatchLayerError → envelope
# ---------------------------------------------------------------------------


async def test_matchlayer_error_returns_full_envelope() -> None:
    app = _build_app("development")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/raise-matchlayer",
            headers={"X-Request-Id": "abcdef12-canary"},
        )

    assert response.status_code == 413
    body = response.json()
    assert body == {
        "type": "resume_too_large",
        "title": "Resume file too large",
        "detail": "Max upload size is 5MB",
        "status": 413,
        "request_id": "abcdef12-canary",
    }


async def test_matchlayer_error_uses_class_defaults_when_no_kwargs() -> None:
    app = _build_app("development")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/raise-default-matchlayer")

    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "internal_server_error"
    assert body["title"] == "Internal Server Error"
    assert body["detail"] == "Something inherent failed."
    assert body["status"] == 500
    # The middleware is wired, so a UUIDv7 falls into the request_id
    # field even though no inbound header was supplied.
    assert isinstance(body["request_id"], str)
    assert len(body["request_id"]) >= 8


# ---------------------------------------------------------------------------
# RequestValidationError → 422, no PII echo
# ---------------------------------------------------------------------------


async def test_request_validation_error_returns_safe_envelope() -> None:
    app = _build_app("development")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    # Both fields fail validation: email is below ``min_length=3`` and
    # password is below ``min_length=12``. Using a payload that fails
    # multiple fields at once exercises the per-field summarization
    # path of ``_summarize_validation_errors``.
    leaky_password = "shortpw"  # test data only; never persisted.
    leaky_email = "x@"  # below min_length 3.
    payload = {"email": leaky_email, "password": leaky_password}

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/login", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "validation_error"
    assert body["title"] == "Request validation failed"
    assert body["status"] == 422
    assert isinstance(body["request_id"], str) and len(body["request_id"]) >= 8

    detail = body["detail"]
    # Field paths are surfaced — the frontend uses them to highlight
    # offending inputs.
    assert "email" in detail
    assert "password" in detail
    # The user-supplied input values MUST NOT appear anywhere in the
    # response body. ``security.md``: passwords are Confidential,
    # email addresses are Restricted.
    serialized = response.text
    assert leaky_password not in serialized
    assert leaky_email not in serialized


# ---------------------------------------------------------------------------
# Catch-all Exception — development vs production
# ---------------------------------------------------------------------------


async def test_unhandled_exception_in_development_includes_class_and_message() -> None:
    app = _build_app("development")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "internal_server_error"
    assert body["title"] == "Internal Server Error"
    assert body["status"] == 500
    # Development surfaces both the exception class and the message so
    # the response is debuggable on its own.
    assert "RuntimeError" in body["detail"]
    assert _LEAKY_MARKER in body["detail"]


async def test_unhandled_exception_in_production_returns_generic_detail() -> None:
    app = _build_app("production")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    with structlog.testing.capture_logs() as captured:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "internal_server_error"
    assert body["title"] == "Internal Server Error"
    assert body["status"] == 500

    # The whole response body — not just ``detail`` — must be free of
    # the original exception class and message in production. Belt and
    # braces against accidental leakage through any other field.
    serialized = response.text
    assert _LEAKY_MARKER not in serialized
    assert "RuntimeError" not in serialized

    assert body["detail"] == "An unexpected error occurred."

    # The handler logs the original exception even when it isn't
    # returned. Operators can correlate the request via request_id.
    unhandled_events = [event for event in captured if event.get("event") == "unhandled_exception"]
    assert len(unhandled_events) == 1
    assert unhandled_events[0]["error_class"] == "RuntimeError"
    assert unhandled_events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# request_id propagation — covers the integration with RequestIdMiddleware.
# ---------------------------------------------------------------------------


async def test_request_id_from_inbound_header_appears_in_error_envelope() -> None:
    app = _build_app("development")
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    inbound = "trace-id_with-dashes_and_underscores"
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # ``MatchLayerError`` is dispatched by Starlette's
        # ``ExceptionMiddleware`` — which sits *inside* our
        # ``RequestIdMiddleware`` in the ASGI stack — so the response
        # round-trips through ``send_with_request_id`` and the outbound
        # ``X-Request-Id`` header is guaranteed. Catch-all
        # :class:`Exception` is handled by Starlette's
        # ``ServerErrorMiddleware``, which sits *outside* user
        # middleware and bypasses that wrapper; for that path the body
        # ``request_id`` field is the contract. We assert each path's
        # contract here.
        matchlayer_response = await client.get(
            "/raise-matchlayer", headers={"X-Request-Id": inbound}
        )
        catchall_response = await client.get("/boom", headers={"X-Request-Id": inbound})

    # MatchLayerError path: both body field AND outbound header carry the id.
    assert matchlayer_response.status_code == 413
    assert matchlayer_response.json()["request_id"] == inbound
    assert matchlayer_response.headers["x-request-id"] == inbound

    # Catch-all path: body field carries the id (sourced from the
    # structlog contextvar bound by RequestIdMiddleware). The outbound
    # header is set by RequestIdMiddleware on responses that pass
    # through it, but Starlette's ServerErrorMiddleware bypasses our
    # send wrapper for unhandled exceptions — so the header on this
    # specific path is best-effort. The body field is the load-bearing
    # correlation handle that operators use to tie a 5xx to its log
    # line.
    assert catchall_response.status_code == 500
    assert catchall_response.json()["request_id"] == inbound
