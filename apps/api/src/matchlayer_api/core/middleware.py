"""Cross-cutting ASGI middleware: request-id logging and API non-indexing.

Both classes here are pure-ASGI middleware (not
``starlette.middleware.base.BaseHTTPMiddleware``, which has known
performance and cancellation-handling issues with streaming responses).

:class:`RequestIdMiddleware`:

* Reuses an inbound ``X-Request-Id`` header when it matches the format
  ``^[A-Za-z0-9_-]{8,128}$``, otherwise generates a fresh UUIDv7
  through :mod:`uuid_utils` (the stdlib does not yet ship UUIDv7 — see
  Design §6.4).
* Binds ``request_id``, ``route``, and ``method`` to a structlog
  contextvar so every log line emitted under this request inherits
  them automatically (Requirement 4.4).
* Echoes the request-id back to the caller as ``X-Request-Id`` on the
  outbound response (Requirement 4.5).
* Emits one structured access-log line per request with ``status`` and
  ``latency_ms`` once the response has been fully sent.

:class:`ApiNoIndexMiddleware`:

* Sets ``X-Robots-Tag: noindex, nofollow`` on every response whose path
  starts with ``/api/v1/``, for all status codes including the RFC 7807
  error envelopes produced by the exception handlers (Requirement 15.3).
  This is a privacy control (defense in depth), not only SEO — resume
  text, job descriptions, and match results must never be crawled or
  indexed (``seo.md``, ``security.md``, ADR 0006).

Design reference: §6.4, "Cross-cutting middleware addition (Requirement
15.3)".
Requirements covered: 4.4, 4.5, 4.6, 15.3.
"""

from __future__ import annotations

import re
import time
from typing import Final

import structlog
import uuid_utils
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Header name used both to read the inbound id and to set it on the
# outbound response. ASGI normalises HTTP header names to lowercase
# bytes, so all comparisons happen on the lowercase form.
_REQUEST_ID_HEADER_NAME: Final[bytes] = b"x-request-id"

# Pattern lifted verbatim from Requirement 4.6. The bound is generous
# enough to accept distributed-tracing identifiers (e.g., AWS X-Ray
# trace IDs, W3C trace-context request ids) while rejecting obvious
# garbage that could log-poison structured output.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

# Header name and value for the API non-indexing control (Requirement
# 15.3). ASGI carries header names/values as lowercase-normalised byte
# strings; the value is a fixed ASCII token list.
_X_ROBOTS_TAG_HEADER_NAME: Final[bytes] = b"x-robots-tag"
_X_ROBOTS_TAG_VALUE: Final[bytes] = b"noindex, nofollow"

# Path prefix whose responses must be marked non-indexable. Matched
# against the raw ``scope["path"]`` exactly (no normalisation), so only
# the versioned API surface — never the marketing/static surface — is
# affected.
_API_PATH_PREFIX: Final[str] = "/api/v1/"

_access_log = structlog.get_logger("matchlayer_api.access")


def _is_valid_request_id(candidate: str) -> bool:
    """Return ``True`` when *candidate* matches the request-id format."""
    return bool(_VALID_REQUEST_ID.match(candidate))


def _extract_inbound_request_id(scope: Scope) -> str | None:
    """Return the inbound ``X-Request-Id`` value, if present and valid.

    HTTP allows duplicate headers; we deliberately consider only the
    first occurrence so a downstream proxy that appends a second value
    cannot override an upstream-provided id. Invalid values are dropped
    silently so the middleware always produces a usable id.
    """
    for name, value in scope.get("headers", ()):
        if name == _REQUEST_ID_HEADER_NAME:
            try:
                decoded = value.decode("latin-1")
            except UnicodeDecodeError:
                return None
            return decoded if _is_valid_request_id(decoded) else None
    return None


def _generate_request_id() -> str:
    """Return a fresh UUIDv7 string.

    UUIDv7 is time-ordered, which keeps log lines roughly sortable by
    arrival order even when ingested across multiple shards. The stdlib
    does not yet ship a v7 generator (Python 3.13 still tops out at v5
    /v3); :mod:`uuid_utils` fills the gap (Design §6.4).
    """
    return str(uuid_utils.uuid7())


def _build_outbound_headers(
    existing: list[tuple[bytes, bytes]],
    request_id: str,
) -> list[tuple[bytes, bytes]]:
    """Return *existing* with a single ``X-Request-Id`` header set.

    Any pre-existing ``X-Request-Id`` headers are stripped so the value
    the middleware emits is authoritative — application code should not
    be quietly overriding the request-id contract.
    """
    encoded = request_id.encode("ascii")
    filtered = [
        (name, value) for name, value in existing if name.lower() != _REQUEST_ID_HEADER_NAME
    ]
    filtered.append((_REQUEST_ID_HEADER_NAME, encoded))
    return filtered


class RequestIdMiddleware:
    """Pure-ASGI request-id + access-log middleware.

    Wraps the downstream ASGI app, manages a per-request structlog
    context (``request_id``/``route``/``method``), injects the
    request-id into the outbound response headers, and emits exactly
    one access-log line per request once the response has been sent.

    Non-HTTP scopes (``"lifespan"``, ``"websocket"``) pass through
    untouched: the request-id contract is HTTP-only at this stage of
    the build (Phase 1 has no websockets).
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _extract_inbound_request_id(scope) or _generate_request_id()
        method: str = scope.get("method", "")
        # ``scope["path"]`` is the raw URL path; the matched route
        # template is only available after Starlette resolves the
        # endpoint, which is too late for the binding here. Using the
        # path keeps the value stable per request and avoids leaking
        # query-string contents (which may carry PII per
        # ``security.md``).
        route: str = scope.get("path", "")

        # Use ``bind_contextvars`` rather than a context manager so the
        # bindings remain visible to background tasks the route handler
        # may schedule (e.g., audit-log writes) without us having to
        # manually re-bind. ``clear_contextvars`` at the end keeps the
        # contextvars hygienic across requests served by the same task.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            route=route,
            method=method,
        )

        start = time.perf_counter()
        # Default in case the downstream app raises before sending a
        # response start; the access log still records ``status=500``
        # so operators can see the failure.
        status_code: int = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message.get("status", 500))
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", ()))
                message = {
                    **message,
                    "headers": _build_outbound_headers(headers, request_id),
                }
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        except Exception:
            # Re-raise so downstream error handlers still run, but make
            # sure the access log records the failure first. ``status``
            # remains 500 because the response never started.
            #
            # We deliberately do NOT clear the structlog contextvars
            # here. Starlette's ``ServerErrorMiddleware`` — which owns
            # the catch-all ``Exception`` handler registered by
            # ``core/errors.register_exception_handlers`` — sits
            # *outside* user-added middleware in the ASGI stack, so it
            # invokes that handler after this ``except`` branch
            # re-raises. The handler sources ``request_id`` from the
            # contextvar; clearing it here would render every catch-all
            # error envelope with ``"request_id": null``. Per-task
            # contextvar isolation (asyncio + ``ContextVar``) plus the
            # ``clear_contextvars()`` call at the top of every request
            # means cross-request leakage is impossible regardless.
            latency_ms = (time.perf_counter() - start) * 1000.0
            _access_log.exception(
                "request_failed",
                status=status_code,
                latency_ms=round(latency_ms, 3),
            )
            raise

        latency_ms = (time.perf_counter() - start) * 1000.0
        # Pick a level that matches the response class so noisy 4xx/5xx
        # responses surface in default ``info``-level deployments
        # without flooding ``warning`` for ordinary traffic.
        log_event = "request_completed" if response_started else "request_aborted"
        if status_code >= 500:
            _access_log.error(
                log_event,
                status=status_code,
                latency_ms=round(latency_ms, 3),
            )
        elif status_code >= 400:
            _access_log.warning(
                log_event,
                status=status_code,
                latency_ms=round(latency_ms, 3),
            )
        else:
            _access_log.info(
                log_event,
                status=status_code,
                latency_ms=round(latency_ms, 3),
            )

        structlog.contextvars.clear_contextvars()


def _with_noindex_header(
    existing: list[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    """Return *existing* headers with a single ``X-Robots-Tag`` set.

    Any pre-existing ``X-Robots-Tag`` header is stripped first so the
    middleware's ``noindex, nofollow`` value is authoritative and never
    duplicated, regardless of what the downstream app or an exception
    handler emitted.
    """
    filtered = [
        (name, value) for name, value in existing if name.lower() != _X_ROBOTS_TAG_HEADER_NAME
    ]
    filtered.append((_X_ROBOTS_TAG_HEADER_NAME, _X_ROBOTS_TAG_VALUE))
    return filtered


class ApiNoIndexMiddleware:
    """Pure-ASGI middleware that marks every API response non-indexable.

    Sets ``X-Robots-Tag: noindex, nofollow`` on the outbound response of
    every request whose path starts with ``/api/v1/`` (Requirement
    15.3). The header is injected on the ``http.response.start`` message,
    so it lands on **every** response — 2xx, 3xx, and the 4xx/5xx RFC
    7807 error envelopes alike — provided this middleware is registered
    so it wraps (outlives) the exception-handling layer. That wiring is
    owned by the application factory (a separate task); this class only
    guarantees the header is applied on the way out regardless of status
    code.

    This is a privacy control, not merely SEO: the API surface returns
    Restricted PII (resume text, job descriptions, match results) that
    must never be crawled or indexed (``seo.md``, ``security.md``, ADR
    0006). It is intentionally defense in depth alongside the frontend
    ``robots`` metadata and ``robots.txt`` disallow rules.

    Non-``/api/v1/`` paths and non-HTTP scopes (``"lifespan"``,
    ``"websocket"``) pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP requests under the versioned API prefix are marked.
        # Everything else (lifespan, websockets, marketing/static paths)
        # passes straight through with no added header.
        if scope["type"] != "http" or not scope.get("path", "").startswith(_API_PATH_PREFIX):
            await self._app(scope, receive, send)
            return

        async def send_with_noindex(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", ()))
                message = {
                    **message,
                    "headers": _with_noindex_header(headers),
                }
            await send(message)

        await self._app(scope, receive, send_with_noindex)
