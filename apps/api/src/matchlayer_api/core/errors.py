"""RFC 7807-inspired error handling for the MatchLayer API.

Implements the cross-cutting error envelope mandated by ``conventions.md``::

    {
      "type":       "validation_error",
      "title":      "Resume file too large",
      "detail":     "Max upload size is 5MB",
      "status":     413,
      "request_id": "..."
    }

Three handlers are registered onto the FastAPI app via
:func:`register_exception_handlers`:

* :class:`MatchLayerError` — the application-defined base for any error
  the API itself raises. Subclasses (added as feature work lands) tune
  ``status_code`` / ``error_type`` / ``title`` while inheriting the
  envelope shape.
* :class:`fastapi.exceptions.RequestValidationError` — Pydantic /
  FastAPI request-shape validation. Handler returns 422 with a concise
  ``detail`` summary built from the field paths and error messages
  (never the user-supplied input values, which may carry PII per
  ``security.md`` data classification).
* The catch-all :class:`Exception` — anything else. In production the
  handler returns generic ``internal_server_error`` text and never
  leaks the original exception's class or message. In development and
  staging the detail line includes ``ExceptionClass: message`` so a
  failing request is debuggable from the response alone. Either way
  the full traceback is captured via :func:`structlog.exception`.

The ``request_id`` field is sourced from the structlog contextvar that
:class:`~matchlayer_api.core.middleware.RequestIdMiddleware` binds at
the start of each request. When the middleware is absent (e.g., in a
unit test that drives a handler directly) the field falls back to
``None`` rather than raising — safer than synthesizing a fresh id that
nothing else in the system has seen.

Design reference: §6.8.
Requirements covered: 4.13.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from matchlayer_api.config import Settings, get_settings

# Module-level logger. Per-request fields (request_id, route, method)
# arrive automatically via the ``merge_contextvars`` processor wired in
# ``core/logging.py``; nothing here has to thread them manually.
_log = structlog.get_logger(__name__)

# Cap how many individual field-level validation messages we copy into
# the ``detail`` string. A pathological request (e.g., a 10 000-row
# array body that fails validation on every element) would otherwise
# generate a kilobyte-scale response. Ten is enough to be useful in
# development without becoming a DoS amplifier.
_MAX_VALIDATION_DETAIL_ITEMS: Final[int] = 10

# Generic detail returned by the catch-all handler in production. The
# original exception is logged but never echoed back to the caller —
# ``security.md`` "No secrets or stack traces in error responses in
# production".
_GENERIC_INTERNAL_DETAIL: Final[str] = "An unexpected error occurred."

# Status codes used by the handlers. ``status.HTTP_422_UNPROCESSABLE_ENTITY``
# is deprecated in current Starlette in favour of the (semantically
# equivalent) ``HTTP_422_UNPROCESSABLE_CONTENT``; the old name still
# emits a DeprecationWarning at attribute access time, which our
# ``filterwarnings = ["error"]`` pytest config promotes to a real
# failure. Using the integer literal sidesteps the deprecation rename
# entirely without coupling to whichever Starlette name happens to win
# the renaming.
_HTTP_422_UNPROCESSABLE_CONTENT: Final[int] = 422


class MatchLayerError(Exception):
    """Base class for application-defined errors.

    Subclasses customize the envelope by overriding the class-level
    attributes ``status_code``, ``error_type``, and ``title``, or by
    passing them at construction. The supplied ``detail`` is what
    appears verbatim in the response body's ``detail`` field — callers
    are responsible for keeping it free of PII and stack traces.

    Example::

        class ResumeTooLargeError(MatchLayerError):
            status_code = 413
            error_type = "resume_too_large"
            title = "Resume file too large"

        raise ResumeTooLargeError("Max upload size is 5MB")
    """

    # Class-level defaults; subclasses override what they need.
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "internal_server_error"
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        if error_type is not None:
            self.error_type = error_type
        if title is not None:
            self.title = title


def _current_request_id() -> str | None:
    """Return the request_id bound by :class:`RequestIdMiddleware`, or ``None``.

    Reading from the structlog contextvar (instead of plumbing the id
    through ``request.state``) keeps the contract identical to what log
    consumers see, and avoids a second source of truth that could drift.
    """
    value = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(value, str):
        return value
    return None


def _problem_response(
    *,
    type_: str,
    title: str,
    detail: str,
    status_code: int,
) -> JSONResponse:
    """Build a JSONResponse with the canonical error envelope."""
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "detail": detail,
        "status": status_code,
        "request_id": _current_request_id(),
    }
    return JSONResponse(status_code=status_code, content=body)


def _summarize_validation_errors(errors: list[dict[str, Any]]) -> str:
    """Build a short, PII-safe ``detail`` string from a Pydantic error list.

    Each entry contributes ``loc.path: msg``. The ``input`` field of
    Pydantic's error dict — which echoes the user's submitted value — is
    deliberately ignored: that value may contain a password, email, or
    resume excerpt, all classified Restricted in ``security.md``.

    Pydantic's ``msg`` strings describe what was *expected* (e.g.,
    "field required", "String should have at least 12 characters") and
    do not echo the offending input, so they are safe to surface.
    """
    if not errors:
        return "Request validation failed."

    parts: list[str] = []
    for err in errors[:_MAX_VALIDATION_DETAIL_ITEMS]:
        # ``loc`` is a tuple like ``("body", "email")``. Drop the
        # leading "body" / "query" / "path" segment for readability;
        # it's redundant with the HTTP method/route already in the log.
        raw_loc = err.get("loc", ()) or ()
        loc_path = ".".join(str(part) for part in raw_loc if part not in ("body", "query", "path"))
        msg = str(err.get("msg", "")).strip()
        if loc_path and msg:
            parts.append(f"{loc_path}: {msg}")
        elif msg:
            parts.append(msg)
        elif loc_path:
            parts.append(loc_path)

    if len(errors) > _MAX_VALIDATION_DETAIL_ITEMS:
        parts.append(f"(+{len(errors) - _MAX_VALIDATION_DETAIL_ITEMS} more)")

    return "; ".join(parts) or "Request validation failed."


def register_exception_handlers(
    app: FastAPI,
    *,
    settings: Settings | None = None,
) -> None:
    """Wire the RFC 7807 error handlers onto *app*.

    The optional ``settings`` argument lets tests inject a Settings
    instance with a chosen ``environment`` (development vs production)
    without monkey-patching the cached :func:`get_settings` accessor.
    Production callers can omit it; the cached settings are read once
    here and captured by the handler closures so request-time work
    stays minimal.

    Args:
        app: The FastAPI application to mutate. Handlers are added in
            the conventional order — most-specific first, catch-all
            last — though Starlette's dispatcher resolves by exception
            type rather than registration order.
        settings: Optional Settings override for testing. Defaults to
            ``get_settings()``.
    """
    cfg = settings or get_settings()
    is_production = cfg.environment == "production"

    async def matchlayer_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Starlette types ``ExceptionHandler`` with ``Exception`` as the
        # second parameter; isinstance-narrowing here keeps the function
        # mypy-strict-clean while still binding ``exc`` to the concrete
        # subclass for attribute access.
        if not isinstance(exc, MatchLayerError):  # pragma: no cover - defensive
            raise exc
        # Application errors are expected, structured, and safe to log
        # at ``warning``: the detail string is author-controlled (see
        # the docstring on :class:`MatchLayerError`).
        _log.warning(
            "matchlayer_error",
            error_type=exc.error_type,
            status=exc.status_code,
            detail=exc.detail,
        )
        return _problem_response(
            type_=exc.error_type,
            title=exc.title,
            detail=exc.detail,
            status_code=exc.status_code,
        )

    async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RequestValidationError):  # pragma: no cover - defensive
            raise exc
        errors: list[dict[str, Any]] = list(exc.errors())
        detail = _summarize_validation_errors(errors)
        # Validation failures are normal client traffic — log at info.
        # ``error_count`` only; we do NOT log the per-field messages
        # here because PII may travel inside ``input`` and the redaction
        # processor is keyed on field names, not on a ``msg`` substring
        # search.
        _log.info(
            "request_validation_failed",
            error_count=len(errors),
            status=_HTTP_422_UNPROCESSABLE_CONTENT,
        )
        return _problem_response(
            type_="validation_error",
            title="Request validation failed",
            detail=detail,
            status_code=_HTTP_422_UNPROCESSABLE_CONTENT,
        )

    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        # ``log.exception`` captures the full traceback through the
        # ``format_exc_info`` processor wired in ``core/logging.py``.
        # The original exception is *always* logged, regardless of
        # environment — operators must be able to reconstruct what
        # happened from the audit trail even when the response body is
        # generic.
        _log.exception(
            "unhandled_exception",
            error_class=type(exc).__name__,
        )
        if is_production:
            detail = _GENERIC_INTERNAL_DETAIL
        else:
            # Dev/staging: surface the concrete class and message so a
            # failing request is debuggable straight from the response.
            # ``str(exc)`` is bounded by the original exception author;
            # we trust it the same way Python's default unhandled
            # traceback does. Production explicitly does NOT reach this
            # branch.
            detail = f"{type(exc).__name__}: {exc}".strip()
        return _problem_response(
            type_="internal_server_error",
            title="Internal Server Error",
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Order is informational — Starlette's exception dispatcher matches
    # by ``isinstance``, picking the most specific registered handler
    # for the raised exception's MRO. Most-specific-first registration
    # mirrors the conceptual stack and keeps the file readable.

    # Auth dependency exceptions.
    from matchlayer_api.core.dependencies import (
        CsrfMismatchError,
        RateLimited,
        RateLimiterUnavailableError,
        UnauthenticatedError,
    )

    async def unauthenticated_handler(_request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            type_="unauthenticated",
            title="Unauthenticated",
            detail="Missing or invalid authentication credentials.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    async def csrf_mismatch_handler(_request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            type_="csrf_mismatch",
            title="CSRF Mismatch",
            detail="CSRF token validation failed.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    async def rate_limited_handler(_request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RateLimited):  # pragma: no cover
            raise exc
        return _problem_response(
            type_="rate_limited",
            title="Rate Limited",
            detail="Too many requests. Try again later.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    async def rate_limiter_unavailable_handler(_request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            type_="rate_limiter_unavailable",
            title="Service Unavailable",
            detail="Rate limiter temporarily unavailable.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    app.add_exception_handler(UnauthenticatedError, unauthenticated_handler)
    app.add_exception_handler(CsrfMismatchError, csrf_mismatch_handler)
    app.add_exception_handler(RateLimited, rate_limited_handler)
    app.add_exception_handler(RateLimiterUnavailableError, rate_limiter_unavailable_handler)
    app.add_exception_handler(MatchLayerError, matchlayer_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = [
    "MatchLayerError",
    "register_exception_handlers",
]
