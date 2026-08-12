"""Unit tests for the OpenRouter adapter against a mocked provider endpoint.

Task 3.4 of phase-3-llm-layer. The adapter under test is
``matchlayer_api.ml.llm.openrouter.OpenRouterClient`` — the single module
allowed to speak OpenRouter's wire format. Every test here mocks the
provider endpoint with ``httpx.MockTransport`` injected through a
monkeypatched ``httpx.AsyncClient`` factory, so real httpx request
construction, header handling, and streaming code paths run without any
network access.

Coverage (task 3.4):

* Request payload shape — ``max_tokens`` from the request's settings-derived
  cap, ``response_format`` json_schema mapped from ``output_schema``,
  ``stream: true``, ``usage.include``, and **no** ``tools`` /
  function-calling fields (Requirements 1.2, 1.4, 4.3).
* Timeout abort — the wall-clock bound covers the full call including
  streaming; expiry raises ``LLMError(category="timeout")`` and the stream
  is closed (Requirement 1.6).
* Single-attempt behavior — any failure results in exactly one provider
  request, never a retry (Requirement 1.12).
* The three startup-validation failure categories — ``invalid_key`` /
  ``unreachable`` / ``timeout`` from ``validate_credentials()``
  (Requirement 1.10).
* The API key never appears in logs, exception strings, or reprs
  (Requirement 1.9).

_Requirements: 1.2, 1.4, 1.6, 1.9, 1.10, 1.12, 4.3_
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from matchlayer_api.config import Settings
from matchlayer_api.ml.llm.client import LLMError, LLMMessage, LLMRequest, LLMStreamChunk
from matchlayer_api.ml.llm.openrouter import OpenRouterClient

# ---------------------------------------------------------------------------
# Hermetic Settings (mirrors tests/property/test_llm_config_validation.py)
# ---------------------------------------------------------------------------

# 33 bytes UTF-8 — clears the 32-byte floor in Settings._jwt_secret_min_length.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

# Leak-detection sentinel for Requirement 1.9 assertions. Deliberately does
# NOT match any real provider key format (no ``sk-`` prefix) so secret
# scanners never flag it; distinctive enough that a substring search over
# exception chains, reprs, and log records is conclusive.
_TEST_API_KEY = "SENTINEL-openrouter-test-key-not-a-real-secret"

_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "environment": "development",
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    "jwt_secret": _TEST_SECRET,
}


def _settings(**overrides: Any) -> Settings:
    """A valid Settings with the test API key and any per-test overrides."""
    kwargs: dict[str, Any] = {
        **_BASE_SETTINGS_KWARGS,
        "llm_api_key": SecretStr(_TEST_API_KEY),
        **overrides,
    }
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# Mocked-endpoint plumbing
# ---------------------------------------------------------------------------

# The real class, captured before any monkeypatching, so the injected
# factory can still construct genuine AsyncClient instances.
_REAL_ASYNC_CLIENT = httpx.AsyncClient

Handler = Callable[[httpx.Request], httpx.Response]


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[..., Any],
) -> None:
    """Route every ``httpx.AsyncClient`` the adapter builds through a mock.

    The adapter constructs its client internally (``httpx.AsyncClient(
    timeout=None)``), so the injection point is the ``AsyncClient``
    attribute itself: the replacement factory forwards all arguments and
    adds a ``MockTransport`` wrapping ``handler``. ``handler`` may be sync
    or async — MockTransport supports both under an async client.
    """

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _sse_body(*payloads: str) -> bytes:
    """Assemble an SSE response body from raw ``data:`` payload strings."""
    return "".join(f"data: {payload}\n\n" for payload in payloads).encode()


def _ok_sse_response() -> httpx.Response:
    """A minimal successful streaming completion with a usage chunk."""
    body = _sse_body(
        json.dumps({"choices": [{"delta": {"content": "Hel"}}]}),
        json.dumps(
            {
                "choices": [{"delta": {"content": "lo"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
            }
        ),
        "[DONE]",
    )
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def _request() -> LLMRequest:
    return LLMRequest(
        messages=[
            LLMMessage(role="system", content="You are a coach."),
            LLMMessage(role="user", content="<user_content>resume text</user_content>"),
        ],
        output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        max_output_tokens=512,
    )


async def _consume(stream: AsyncIterator[LLMStreamChunk]) -> list[str]:
    return [chunk.delta async for chunk in stream]


def _assert_key_absent_from_chain(exc: BaseException) -> None:
    """Walk the full exception chain asserting the key appears nowhere.

    Requirement 1.9: the key must not leak through ``str()`` or ``repr()``
    of the raised ``LLMError`` nor through any ``__cause__``/``__context__``
    link (httpx transport errors, TimeoutError, JSON errors...).
    """
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert _TEST_API_KEY not in str(current)
        assert _TEST_API_KEY not in repr(current)
        for link in (current.__cause__, current.__context__):
            if link is not None:
                stack.append(link)


class _StallingByteStream(httpx.AsyncByteStream):
    """A response body that emits one chunk then stalls past any timeout.

    Used to prove the Requirement 1.6 wall-clock bound covers the streaming
    phase of the call, not just the connect.
    """

    def __init__(self, first_chunk: bytes, stall_seconds: float) -> None:
        self._first_chunk = first_chunk
        self._stall_seconds = stall_seconds

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._first_chunk
        await asyncio.sleep(self._stall_seconds)

    async def aclose(self) -> None:  # pragma: no cover - httpx cleanup hook
        return None


# ---------------------------------------------------------------------------
# Request payload shape (Requirements 1.2, 1.4, 4.3)
# ---------------------------------------------------------------------------


class TestRequestPayloadShape:
    """The adapter maps the provider-neutral request onto the wire exactly."""

    async def test_payload_carries_caps_schema_and_no_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One POST to ``{base_url}/chat/completions`` carrying ``max_tokens``
        from the request, ``response_format`` json_schema from
        ``output_schema``, ``stream: true``, ``usage.include`` — and no
        ``tools``/function-calling fields (Requirements 1.2, 1.4, 4.3)."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return _ok_sse_response()

        _install_transport(monkeypatch, handler)
        settings = _settings()
        client = OpenRouterClient(settings=settings)
        request = _request()

        deltas = await _consume(client.stream(request))

        assert deltas == ["Hel", "lo"]
        assert len(captured) == 1
        wire = captured[0]
        assert wire.method == "POST"
        assert str(wire.url) == f"{settings.llm_base_url}/chat/completions"

        payload = json.loads(wire.content)
        # Requirement 1.4: the settings-derived output-token cap is on
        # every request.
        assert payload["max_tokens"] == request.max_output_tokens
        # Requirement 8.1 mapping: structured output via json_schema.
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["schema"] == request.output_schema
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["stream"] is True
        assert payload["usage"] == {"include": True}
        assert payload["model"] == settings.llm_model
        assert payload["messages"] == [
            {"role": "system", "content": "You are a coach."},
            {"role": "user", "content": "<user_content>resume text</user_content>"},
        ]
        # Requirement 4.3: no tool or function calling, ever.
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "functions" not in payload
        assert "function_call" not in payload

    async def test_key_travels_only_as_authorization_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key appears exactly once on the wire: the ``Authorization:
        Bearer`` header — never in the URL or body (Requirements 1.2, 1.9)."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return _ok_sse_response()

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings())

        await _consume(client.stream(_request()))

        wire = captured[0]
        assert wire.headers["authorization"] == f"Bearer {_TEST_API_KEY}"
        assert _TEST_API_KEY not in str(wire.url)
        assert _TEST_API_KEY not in wire.content.decode()

    async def test_completion_accumulates_text_and_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``result()`` returns the assembled text plus the provider-reported
        usage from the final chunk (adapter contract behind Requirement 1.2)."""
        _install_transport(monkeypatch, lambda request: _ok_sse_response())
        client = OpenRouterClient(settings=_settings())

        await _consume(client.stream(_request()))
        completion = await client.result()

        assert completion.text == "Hello"
        assert completion.usage.input_tokens == 10
        assert completion.usage.output_tokens == 5
        assert completion.usage.cost_basis == "provider_reported"


# ---------------------------------------------------------------------------
# Timeout abort (Requirement 1.6)
# ---------------------------------------------------------------------------


class TestTimeoutAbort:
    """The wall-clock timeout covers the full call and aborts cleanly."""

    async def test_timeout_before_response_raises_timeout_category(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider that never responds within the bound aborts with
        ``LLMError(category="timeout")`` (Requirement 1.6)."""

        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return _ok_sse_response()  # pragma: no cover - timeout fires first

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings(llm_timeout_seconds=1))

        with pytest.raises(LLMError) as excinfo:
            await _consume(client.stream(_request()))

        assert excinfo.value.category == "timeout"

    async def test_timeout_mid_stream_aborts_and_keeps_partial_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bound covers the streaming phase too: a stall after the first
        chunk aborts the call, and the accumulated partial completion stays
        available for invocation logging (Requirement 1.6)."""
        first = _sse_body(json.dumps({"choices": [{"delta": {"content": "partial"}}]}))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_StallingByteStream(first, stall_seconds=5),
                headers={"content-type": "text/event-stream"},
            )

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings(llm_timeout_seconds=1))

        received: list[str] = []
        with pytest.raises(LLMError) as excinfo:
            async for chunk in client.stream(_request()):
                received.append(chunk.delta)

        assert excinfo.value.category == "timeout"
        assert received == ["partial"]
        completion = await client.result()
        assert completion.text == "partial"
        # No usage chunk ever arrived: unavailable, not zero.
        assert completion.usage.cost_basis == "unavailable"
        assert completion.usage.cost_usd is None


# ---------------------------------------------------------------------------
# Single-attempt behavior (Requirement 1.12)
# ---------------------------------------------------------------------------


class TestSingleAttempt:
    """Exactly one provider request per call — no retries on any failure."""

    async def test_transport_failure_makes_exactly_one_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A connect failure raises ``unreachable`` after exactly one
        request (Requirement 1.12)."""
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectError("connection refused")

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings())

        with pytest.raises(LLMError) as excinfo:
            await _consume(client.stream(_request()))

        assert excinfo.value.category == "unreachable"
        assert len(calls) == 1

    async def test_provider_5xx_makes_exactly_one_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 500 raises ``provider_error`` after exactly one request —
        no retry on server errors either (Requirement 1.12)."""
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(500, json={"error": "boom"})

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings())

        with pytest.raises(LLMError) as excinfo:
            await _consume(client.stream(_request()))

        assert excinfo.value.category == "provider_error"
        assert len(calls) == 1

    async def test_success_makes_exactly_one_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The happy path is also exactly one request (Requirement 1.12)."""
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _ok_sse_response()

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings())

        await _consume(client.stream(_request()))

        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Startup-validation failure categories (Requirement 1.10)
# ---------------------------------------------------------------------------


class TestValidateCredentials:
    """``validate_credentials()`` distinguishes the three failure causes."""

    async def test_success_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 200 from ``GET /key`` validates silently, with the key sent
        only as the auth header (Requirement 1.10)."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"data": {"label": "ok"}})

        _install_transport(monkeypatch, handler)
        settings = _settings()
        client = OpenRouterClient(settings=settings)

        await client.validate_credentials()

        assert len(captured) == 1
        wire = captured[0]
        assert wire.method == "GET"
        assert str(wire.url) == f"{settings.llm_base_url}/key"
        assert wire.headers["authorization"] == f"Bearer {_TEST_API_KEY}"

    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_auth_rejection_is_invalid_key(
        self, monkeypatch: pytest.MonkeyPatch, status_code: int
    ) -> None:
        """401/403 → ``invalid_key`` — the provider judged the key bad
        (Requirement 1.10)."""
        _install_transport(
            monkeypatch, lambda request: httpx.Response(status_code, json={"error": "bad key"})
        )
        client = OpenRouterClient(settings=_settings())

        with pytest.raises(LLMError) as excinfo:
            await client.validate_credentials()

        assert excinfo.value.category == "invalid_key"

    async def test_transport_failure_is_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connect error → ``unreachable`` (Requirement 1.10)."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings())

        with pytest.raises(LLMError) as excinfo:
            await client.validate_credentials()

        assert excinfo.value.category == "unreachable"

    async def test_provider_5xx_is_unreachable_not_invalid_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 5xx is not proof the key is bad: classified ``unreachable`` so
        startup names the right cause (Requirement 1.10)."""
        _install_transport(monkeypatch, lambda request: httpx.Response(503))
        client = OpenRouterClient(settings=_settings())

        with pytest.raises(LLMError) as excinfo:
            await client.validate_credentials()

        assert excinfo.value.category == "unreachable"

    async def test_hang_is_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A provider that never answers within the configured bound →
        ``timeout`` (Requirement 1.10)."""

        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200)  # pragma: no cover - timeout fires first

        _install_transport(monkeypatch, handler)
        client = OpenRouterClient(settings=_settings(llm_timeout_seconds=1))

        with pytest.raises(LLMError) as excinfo:
            await client.validate_credentials()

        assert excinfo.value.category == "timeout"


# ---------------------------------------------------------------------------
# Key never leaks (Requirement 1.9)
# ---------------------------------------------------------------------------


class TestKeyNeverLeaks:
    """The API key appears in no repr, exception chain, or log record."""

    def test_repr_excludes_key(self) -> None:
        """``repr()`` of the adapter never contains the key (Requirement 1.9)."""
        client = OpenRouterClient(settings=_settings())

        assert _TEST_API_KEY not in repr(client)
        assert _TEST_API_KEY not in str(client)

    async def test_no_failure_leaks_key_into_exception_chain_or_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Across every failure mode — invalid key, transport failure,
        provider 5xx, malformed SSE payload, timeout — the key appears
        nowhere in the raised error, its full cause/context chain, or any
        emitted log record (Requirement 1.9)."""

        def raise_connect(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"refused connecting to {request.url}")

        async def hang(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200)  # pragma: no cover - timeout fires first

        # A malformed SSE body rides a 200, so ``validate_credentials`` (which
        # only reads the status) succeeds for that scenario — the second bool
        # says whether the validation call is expected to raise too.
        scenarios: list[tuple[Callable[..., Any], bool]] = [
            (lambda request: httpx.Response(401, json={"error": "invalid key"}), True),
            (raise_connect, True),
            (lambda request: httpx.Response(500, text="internal error"), True),
            (
                lambda request: httpx.Response(
                    200,
                    content=b"data: {not-json}\n\n",
                    headers={"content-type": "text/event-stream"},
                ),
                False,
            ),
            (hang, True),
        ]

        with caplog.at_level(logging.DEBUG):
            for handler, validate_raises in scenarios:
                _install_transport(monkeypatch, handler)
                client = OpenRouterClient(settings=_settings(llm_timeout_seconds=1))

                with pytest.raises(LLMError) as excinfo:
                    await _consume(client.stream(_request()))

                _assert_key_absent_from_chain(excinfo.value)

                if validate_raises:
                    with pytest.raises(LLMError) as validate_excinfo:
                        await client.validate_credentials()

                    _assert_key_absent_from_chain(validate_excinfo.value)

        for record in caplog.records:
            assert _TEST_API_KEY not in record.getMessage()

    async def test_mid_stream_error_detail_excludes_provider_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider mid-stream error object is never propagated into the
        exception — its body could echo prompt content (Requirement 1.9)."""
        secret_body = "provider-side message quoting the prompt verbatim"
        body = _sse_body(json.dumps({"error": {"message": secret_body}}))
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"}
            ),
        )
        client = OpenRouterClient(settings=_settings())

        with pytest.raises(LLMError) as excinfo:
            await _consume(client.stream(_request()))

        assert excinfo.value.category == "provider_error"
        assert secret_body not in str(excinfo.value)
        assert secret_body not in repr(excinfo.value)
