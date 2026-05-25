"""Tests for the request-id ASGI middleware.

Covers Requirements 4.4 (structured access log fields), 4.5 (UUIDv7 is
generated when no inbound id is present) and 4.6 (valid inbound
``X-Request-Id`` is reused). Tests drive the middleware as a raw ASGI
app so they exercise the actual contract surface — not Starlette
helpers — and remain independent of the FastAPI application factory
(which is wired in a later task).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

import pytest
import structlog
from starlette.types import Message, Receive, Scope, Send

from matchlayer_api.core.middleware import RequestIdMiddleware

# UUIDv7 emitted as the canonical 36-char string. Used to confirm the
# middleware's generator path (Requirement 4.5) without coupling to the
# uuid_utils internals.
_UUID_V7_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_scope(
    *,
    method: str = "GET",
    path: str = "/healthz",
    headers: Iterable[tuple[bytes, bytes]] = (),
    scope_type: str = "http",
) -> Scope:
    """Build a minimal ASGI scope dict for the middleware under test."""
    return {
        "type": scope_type,
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": list(headers),
    }


def _empty_receive() -> Receive:
    async def receive() -> Message:  # pragma: no cover - never called
        return {"type": "http.disconnect"}

    return receive


class _RecordingSend:
    """Capture every ASGI message the app sends downstream."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def response_start(self) -> Message:
        for message in self.messages:
            if message["type"] == "http.response.start":
                return message
        raise AssertionError("no http.response.start message captured")

    @property
    def headers(self) -> dict[bytes, bytes]:
        return {name: value for name, value in self.response_start.get("headers", ())}


def _ok_app(
    status: int = 200, headers: Iterable[tuple[bytes, bytes]] = ()
) -> Callable[[Scope, Receive, Send], Any]:
    """Return a tiny ASGI app that emits a fixed response."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": list(headers),
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app


def _capturing_app(
    captured: dict[str, dict[str, Any]],
) -> Callable[[Scope, Receive, Send], Any]:
    """Return an ASGI app that snapshots structlog contextvars during the call."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        captured["bindings"] = dict(structlog.contextvars.get_contextvars())
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app


# ---------------------------------------------------------------------------
# Requirement 4.5 — generates a UUIDv7 when no inbound id is present.
# ---------------------------------------------------------------------------


async def test_generates_uuidv7_when_inbound_header_missing() -> None:
    middleware = RequestIdMiddleware(_ok_app())
    send = _RecordingSend()

    await middleware(_make_scope(), _empty_receive(), send)

    request_id = send.headers[b"x-request-id"].decode("ascii")
    assert _UUID_V7_PATTERN.match(request_id), f"expected UUIDv7, got {request_id!r}"


# ---------------------------------------------------------------------------
# Requirement 4.6 — valid inbound X-Request-Id is reused verbatim.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "abc12345",  # min length 8
        "deadbeefcafebabe",
        "trace-id_with-dashes-and_underscores",
        "A" * 128,  # max length 128
    ],
)
async def test_reuses_valid_inbound_request_id(candidate: str) -> None:
    middleware = RequestIdMiddleware(_ok_app())
    send = _RecordingSend()

    await middleware(
        _make_scope(headers=[(b"x-request-id", candidate.encode("ascii"))]),
        _empty_receive(),
        send,
    )

    assert send.headers[b"x-request-id"].decode("ascii") == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        b"short",  # below 8 chars
        b"A" * 129,  # above 128 chars
        b"has spaces inside",
        b"semicolons;not;allowed",
        b"\xff\xfe\xfd\xfc\xfb\xfa\xf9\xf8",  # non-ascii / control
    ],
)
async def test_replaces_invalid_inbound_request_id(candidate: bytes) -> None:
    middleware = RequestIdMiddleware(_ok_app())
    send = _RecordingSend()

    await middleware(
        _make_scope(headers=[(b"x-request-id", candidate)]),
        _empty_receive(),
        send,
    )

    emitted = send.headers[b"x-request-id"].decode("ascii")
    assert emitted.encode("ascii") != candidate
    assert _UUID_V7_PATTERN.match(emitted), f"expected UUIDv7 fallback, got {emitted!r}"


# ---------------------------------------------------------------------------
# Requirement 4.4 — request_id / route / method bound to structlog context.
# ---------------------------------------------------------------------------


async def test_binds_request_id_route_method_to_structlog_context() -> None:
    captured: dict[str, dict[str, Any]] = {}
    middleware = RequestIdMiddleware(_capturing_app(captured))

    await middleware(
        _make_scope(method="POST", path="/api/v1/things"),
        _empty_receive(),
        _RecordingSend(),
    )

    bindings = captured["bindings"]
    assert bindings["method"] == "POST"
    assert bindings["route"] == "/api/v1/things"
    assert _UUID_V7_PATTERN.match(bindings["request_id"])


async def test_clears_contextvars_after_request_completes() -> None:
    middleware = RequestIdMiddleware(_ok_app())

    await middleware(_make_scope(), _empty_receive(), _RecordingSend())

    assert structlog.contextvars.get_contextvars() == {}


# ---------------------------------------------------------------------------
# Outbound header hygiene — pre-existing X-Request-Id headers are replaced.
# ---------------------------------------------------------------------------


async def test_replaces_any_app_emitted_request_id_header() -> None:
    middleware = RequestIdMiddleware(_ok_app(headers=[(b"x-request-id", b"app-set-value-ignored")]))
    send = _RecordingSend()

    await middleware(_make_scope(), _empty_receive(), send)

    request_id_values = [
        value for name, value in send.response_start["headers"] if name.lower() == b"x-request-id"
    ]
    assert len(request_id_values) == 1
    assert request_id_values[0] != b"app-set-value-ignored"


# ---------------------------------------------------------------------------
# Non-HTTP scopes (lifespan, websocket) pass through untouched.
# ---------------------------------------------------------------------------


async def test_lifespan_scope_is_passed_through_untouched() -> None:
    received: list[Message] = []

    async def lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "lifespan.startup.complete"})

    middleware = RequestIdMiddleware(lifespan_app)

    async def send(message: Message) -> None:
        received.append(message)

    await middleware(_make_scope(scope_type="lifespan"), _empty_receive(), send)

    assert received == [{"type": "lifespan.startup.complete"}]
    assert structlog.contextvars.get_contextvars() == {}


# ---------------------------------------------------------------------------
# Requirement 4.4 — access-log line includes status and latency_ms.
# ---------------------------------------------------------------------------


async def test_emits_access_log_with_status_and_latency_ms() -> None:
    middleware = RequestIdMiddleware(_ok_app(status=204))

    # ``capture_logs`` is structlog's blessed testing helper. It swaps
    # the active processor chain for the duration of the ``with`` block
    # and yields a list of every event dict the chain saw — exactly the
    # shape this test wants to assert against. Note: ``capture_logs``
    # replaces the configured processor chain wholesale, so
    # contextvar-merged keys (``request_id``/``route``/``method``) are
    # NOT present in the captured events. Those are asserted separately
    # in ``test_binds_request_id_route_method_to_structlog_context`` —
    # this test focuses on the per-event payload (status, latency_ms)
    # that Requirement 4.4 mandates on the access-log line itself.
    with structlog.testing.capture_logs() as captured:
        await middleware(_make_scope(method="DELETE"), _empty_receive(), _RecordingSend())

    access_events = [event for event in captured if event.get("event") == "request_completed"]
    assert len(access_events) == 1
    event = access_events[0]
    assert event["status"] == 204
    assert isinstance(event["latency_ms"], float)
    assert event["latency_ms"] >= 0.0
    # ``capture_logs`` annotates each event with the log method that
    # produced it; assert the 2xx path lands on ``info`` so noisy
    # warnings stay reserved for 4xx responses.
    assert event["log_level"] == "info"


async def test_access_log_level_matches_response_class() -> None:
    """4xx → warning, 5xx → error."""
    cases: list[tuple[int, str]] = [(404, "warning"), (503, "error")]
    for status, expected_level in cases:
        middleware = RequestIdMiddleware(_ok_app(status=status))
        with structlog.testing.capture_logs() as captured:
            await middleware(_make_scope(), _empty_receive(), _RecordingSend())
        access_events = [event for event in captured if event.get("event") == "request_completed"]
        assert len(access_events) == 1, f"status={status}: {captured!r}"
        assert access_events[0]["log_level"] == expected_level, (
            f"status={status} should log at {expected_level}, got {access_events[0]['log_level']}"
        )
