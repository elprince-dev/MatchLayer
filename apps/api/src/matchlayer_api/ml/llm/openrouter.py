"""The single OpenRouter adapter implementing the ``LLMClient`` protocol.

This module is the **only** source file in the API allowed to reference
OpenRouter (Requirement 1.1): its OpenAI-compatible ``/chat/completions``
endpoint, its ``/key`` credential-metadata endpoint, its SSE chunk format,
and its usage-accounting payload all live here and nowhere else. A Phase 6
provider swap is a new sibling adapter module plus configuration — nothing
above the :class:`~matchlayer_api.ml.llm.client.LLMClient` protocol changes.

Contracts implemented (design "LLM_Client protocol and OpenRouter adapter"):

* ``POST {base_url}/chat/completions`` with ``stream: true``,
  ``response_format: {"type": "json_schema", ...}`` mapped from the
  request's ``output_schema`` (Requirement 8.1), ``max_tokens`` carrying the
  settings-derived cap on every call (Requirement 1.4), and
  ``usage: {"include": true}`` so the final chunk reports token counts and
  OpenRouter's own cost figure.
* The full call — connect, stream, final chunk — runs inside
  ``asyncio.timeout(settings.llm_timeout_seconds)``; on expiry the HTTP
  stream is closed and ``LLMError(category="timeout")`` raised
  (Requirement 1.6).
* Exactly one attempt per request — no retries anywhere in this adapter
  (Requirement 1.12).
* Cost resolution (Requirement 12.2): provider-reported when the usage
  payload carries a cost, otherwise computed from token counts and the
  configured per-mtok pricing, otherwise unavailable (``None`` — distinct
  from zero).
* ``validate_credentials()`` issues the authenticated ``GET /key`` request
  bounded by the same timeout, raising typed categories distinguishing
  invalid-key / unreachable / timeout for the startup check
  (Requirement 1.10).
* The API key is held as ``SecretStr``, attached only as the
  ``Authorization: Bearer`` header, and never included in exceptions, log
  lines, or reprs (Requirement 1.9). This module logs nothing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
from pydantic import SecretStr

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)

__all__ = ["OpenRouterClient"]

_SSE_DATA_PREFIX = "data:"
_SSE_DONE_SENTINEL = "[DONE]"
_TOKENS_PER_MILLION = Decimal(1_000_000)


def _unavailable_usage() -> LLMUsage:
    """Usage for a call that never received a provider usage payload.

    ``None`` token counts and cost are *unavailable*, deliberately distinct
    from zero (Requirement 12.2).
    """
    return LLMUsage(
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
        cost_basis="unavailable",
    )


def _read_token_count(payload: dict[str, Any], key: str) -> int | None:
    """Extract a non-negative integer token count, or ``None`` if absent.

    ``bool`` is excluded explicitly because it subclasses ``int`` and a
    provider bug sending ``true`` must not become a token count of 1.
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class OpenRouterClient:
    """OpenRouter implementation of the provider-neutral ``LLMClient``.

    One instance serves one logical call sequence: ``stream()`` consumes the
    provider response and ``result()`` returns the completion accumulated by
    the most recent stream. Instances are cheap — callers construct one per
    request rather than sharing across concurrent requests.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        resolved = settings if settings is not None else get_settings()
        api_key = resolved.llm_api_key
        if api_key is None or not api_key.get_secret_value():
            # Availability wiring (Requirement 1.8) never constructs the
            # adapter without a key; reaching this is a programming error.
            # The message carries no secret — there is none to carry.
            raise ValueError("OpenRouterClient requires a configured LLM API key")
        self._api_key: SecretStr = api_key
        self._base_url: str = resolved.llm_base_url.rstrip("/")
        self._model: str = resolved.llm_model
        self._timeout_seconds: int = resolved.llm_timeout_seconds
        self._price_input_usd_per_mtok: Decimal = resolved.llm_price_input_usd_per_mtok
        self._price_output_usd_per_mtok: Decimal = resolved.llm_price_output_usd_per_mtok
        self._completion: LLMCompletion | None = None

    def __repr__(self) -> str:
        """Repr excludes the API key by construction (Requirement 1.9)."""
        return f"OpenRouterClient(base_url={self._base_url!r}, model={self._model!r})"

    # ---- LLMClient protocol ------------------------------------------------

    async def validate_credentials(self) -> None:
        """Validate the configured key via OpenRouter's ``GET /key``.

        Bounded by the same ``MATCHLAYER_LLM_TIMEOUT_SECONDS`` wall-clock
        timeout as feature calls (Requirement 1.10). Raises
        :class:`LLMError` with exactly one of the three startup-check
        categories — ``invalid_key``, ``unreachable``, ``timeout`` — and
        never the key value.
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                # httpx's own timeouts are deliberately disabled: the
                # asyncio.timeout above is the single authoritative
                # wall-clock bound (Requirement 1.10), so a second timeout
                # layer would only blur which bound fired.
                async with httpx.AsyncClient(timeout=None) as client:  # noqa: S113
                    response = await client.get(
                        f"{self._base_url}/key",
                        headers=self._auth_headers(),
                    )
        except TimeoutError as exc:
            raise LLMError(
                category="timeout",
                detail="credential validation timed out",
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                category="unreachable",
                detail="provider unreachable during credential validation",
            ) from exc
        if response.status_code in (401, 403):
            raise LLMError(
                category="invalid_key",
                detail="provider reported the configured API key invalid",
            )
        if response.status_code != 200:
            # A provider-side failure (5xx, unexpected status) is not proof
            # the key is bad; classify it as unreachable so the startup
            # error names the right cause category.
            raise LLMError(
                category="unreachable",
                detail=(
                    "provider returned an unexpected status "
                    f"{response.status_code} during credential validation"
                ),
            )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Execute ``request`` once, yielding raw output fragments.

        The entire provider interaction — connect, streamed body, final
        usage chunk — is bounded by one ``asyncio.timeout`` (Requirement
        1.6). Exactly one attempt is made; every failure raises
        :class:`LLMError` and the caller's fallback path applies
        (Requirement 1.12). Whatever happened, the accumulated completion
        (text, usage, latency) is recorded for :meth:`result`.
        """
        text_parts: list[str] = []
        usage_payload: dict[str, Any] | None = None
        started = time.monotonic()
        try:
            async with (
                asyncio.timeout(self._timeout_seconds),
                # httpx timeouts deliberately disabled: asyncio.timeout is
                # the single authoritative bound covering the full call
                # (Req 1.6); a second layer would misreport timeout
                # expiries as transport failures.
                httpx.AsyncClient(timeout=None) as client,  # noqa: S113
                client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._auth_headers(),
                    json=self._build_payload(request),
                ) as response,
            ):
                if response.status_code in (401, 403):
                    raise LLMError(
                        category="invalid_key",
                        detail="provider rejected the configured API key",
                    )
                if response.status_code != 200:
                    raise LLMError(
                        category="provider_error",
                        detail=f"provider returned HTTP {response.status_code}",
                    )
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped.startswith(_SSE_DATA_PREFIX):
                        # Blank keep-alive lines and ": comment" processing
                        # notices carry no payload.
                        continue
                    data = stripped[len(_SSE_DATA_PREFIX) :].strip()
                    if data == _SSE_DONE_SENTINEL:
                        break
                    delta, chunk_usage = _parse_sse_chunk(data)
                    if chunk_usage is not None:
                        usage_payload = chunk_usage
                    if delta:
                        text_parts.append(delta)
                        yield LLMStreamChunk(delta=delta)
        except TimeoutError as exc:
            # The wall clock elapsed mid-call: the context managers above
            # have already closed the HTTP stream on unwind (Req 1.6).
            raise LLMError(category="timeout", detail=None) from exc
        except httpx.HTTPError as exc:
            # Transport-level failure (connect refused, DNS, reset...).
            # httpx exceptions never carry request headers, so no key can
            # leak through the exception chain (Requirement 1.9).
            raise LLMError(
                category="unreachable",
                detail="provider unreachable",
            ) from exc
        finally:
            # Record the accumulated completion whether the stream finished,
            # timed out, failed, or was aborted by a client disconnect —
            # invocation logging needs text/usage/latency in every case
            # (Requirement 12.2).
            latency_ms = int((time.monotonic() - started) * 1000)
            self._completion = LLMCompletion(
                text="".join(text_parts),
                usage=self._resolve_usage(usage_payload),
                latency_ms=latency_ms,
            )

    async def result(self) -> LLMCompletion:
        """Return the completion accumulated by the most recent stream."""
        if self._completion is None:
            raise RuntimeError("result() called before any stream ran")
        return self._completion

    # ---- internals -----------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Build the only place the key ever leaves its ``SecretStr``.

        The materialized value goes straight into the request headers and
        is never stored, logged, or interpolated anywhere else
        (Requirement 1.9).
        """
        return {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        """Map the provider-neutral request onto OpenRouter's wire shape.

        ``max_tokens`` carries the request's settings-derived cap
        (Requirement 1.4); ``response_format`` constrains output to the
        request's JSON Schema (Requirement 8.1); ``usage.include`` asks for
        the final accounting chunk (Requirement 12.2). No ``tools`` or
        function-calling field is ever sent (Requirement 4.3).
        """
        return {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "stream": True,
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": request.output_schema,
                },
            },
            "usage": {"include": True},
        }

    def _resolve_usage(self, usage_payload: dict[str, Any] | None) -> LLMUsage:
        """Resolve usage per the Requirement 12.2 precedence.

        Provider-reported cost when the usage payload carries one →
        computed from token counts x configured per-mtok pricing →
        unavailable (``None``, distinct from zero).
        """
        if usage_payload is None:
            return _unavailable_usage()
        input_tokens = _read_token_count(usage_payload, "prompt_tokens")
        output_tokens = _read_token_count(usage_payload, "completion_tokens")
        reported_cost = usage_payload.get("cost")
        if isinstance(reported_cost, int | float) and not isinstance(reported_cost, bool):
            return LLMUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                # Through str() so binary-float artifacts don't leak into
                # the Decimal (0.1 stays 0.1, not 0.1000000000000000055...).
                cost_usd=Decimal(str(reported_cost)),
                cost_basis="provider_reported",
            )
        if input_tokens is not None and output_tokens is not None:
            computed = (
                Decimal(input_tokens) * self._price_input_usd_per_mtok
                + Decimal(output_tokens) * self._price_output_usd_per_mtok
            ) / _TOKENS_PER_MILLION
            return LLMUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=computed,
                cost_basis="computed",
            )
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,
            cost_basis="unavailable",
        )


def _parse_sse_chunk(data: str) -> tuple[str | None, dict[str, Any] | None]:
    """Parse one SSE ``data:`` payload into ``(delta, usage_payload)``.

    Returns the chunk's content fragment (if any) and its usage object (if
    any — OpenRouter sends usage on the final chunk when
    ``usage.include`` is set). Malformed payloads and provider-reported
    mid-stream errors raise :class:`LLMError` with an operator-safe detail
    that never embeds the payload body (Requirement 1.9).
    """
    try:
        # Any: raw provider JSON is inherently untyped; every field read
        # below is isinstance-narrowed before use.
        payload: Any = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMError(
            category="provider_error",
            detail="malformed streaming payload from provider",
        ) from exc
    if not isinstance(payload, dict):
        raise LLMError(
            category="provider_error",
            detail="unexpected streaming payload shape from provider",
        )
    if "error" in payload:
        # OpenRouter reports mid-stream failures as an error object; its
        # body is never propagated into the exception (Requirement 1.9).
        raise LLMError(
            category="provider_error",
            detail="provider reported a mid-stream error",
        )
    delta: str | None = None
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            delta_obj = first.get("delta")
            if isinstance(delta_obj, dict):
                content = delta_obj.get("content")
                if isinstance(content, str):
                    delta = content
    usage = payload.get("usage")
    usage_payload = usage if isinstance(usage, dict) else None
    return delta, usage_payload
