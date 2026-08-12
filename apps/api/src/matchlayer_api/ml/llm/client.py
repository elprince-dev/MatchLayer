"""Provider-neutral ``LLMClient`` protocol and request/response types.

The abstraction every LLM call in the API flows through (Requirement 1.1).
These types are deliberately provider-neutral: no provider-specific
identifiers, types, or parameters appear in any public signature here, so a
Phase 6 move to another provider is a new adapter module implementing
:class:`LLMClient` plus configuration — nothing above this interface changes.

Design reference: the "LLM_Client protocol and provider adapter" section of
the phase-3-llm-layer design. Requirements covered: 1.1.

Key contracts encoded in these types:

* :class:`LLMRequest` always carries an ``output_schema`` (structured outputs
  only — Requirement 8.1) and a mandatory ``max_output_tokens`` cap, set from
  settings by the caller (Requirement 1.4).
* :class:`LLMUsage` distinguishes *unavailable* (``None``) token counts and
  cost from a genuine zero (Requirement 12.2), and records how the cost was
  obtained via ``cost_basis``.
* :class:`LLMError` carries a failure category only — never a provider
  response body, prompt content, or the API key (Requirement 1.9).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel

__all__ = [
    "LLMClient",
    "LLMCompletion",
    "LLMError",
    "LLMMessage",
    "LLMRequest",
    "LLMStreamChunk",
    "LLMUsage",
]


class LLMMessage(BaseModel):
    """A single chat message in a provider-neutral request.

    Only ``system`` and ``user`` roles exist by design: the rendered prompt
    template is the sole system-role message, and all user-derived content is
    carried in the user-role message (design "Prompt assembly"; Requirement
    4.1). No assistant/tool roles — no tool or function calling is ever
    requested (Requirement 4.3).
    """

    role: Literal["system", "user"]
    content: str


class LLMRequest(BaseModel):
    """A provider-neutral chat-completion request with structured output.

    ``max_output_tokens`` is mandatory: every request carries the cap read
    from ``MATCHLAYER_LLM_MAX_OUTPUT_TOKENS`` by the caller (Requirement 1.4).
    """

    messages: list[LLMMessage]
    # dict[str, Any]: a JSON Schema document the provider must constrain its
    # output to (Requirement 8.1). JSON Schema is inherently heterogeneous —
    # nested objects, arrays, bools, strings — so Any is the honest value type.
    output_schema: dict[str, Any]
    max_output_tokens: int


class LLMUsage(BaseModel):
    """Token and cost accounting for a completed (or failed) call.

    ``None`` means *unavailable*, which is distinct from ``0`` (Requirement
    12.2): a failed call that never received a usage payload reports ``None``
    with ``cost_basis="unavailable"``, never zeros. ``cost_basis`` records how
    ``cost_usd`` was obtained: reported by the provider, computed locally from
    token counts and configured per-token pricing, or unavailable.
    """

    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Decimal | None
    cost_basis: Literal["provider_reported", "computed", "unavailable"]


class LLMStreamChunk(BaseModel):
    """One incremental fragment of raw output text from a streaming call."""

    delta: str


class LLMCompletion(BaseModel):
    """The final, fully-accumulated result of a streaming call.

    ``text`` is the complete raw output assembled from every streamed delta;
    downstream validation (Requirement 8.2) always operates on this assembled
    text, never on individual chunks. ``latency_ms`` measures transmission
    start to final token (or termination).
    """

    text: str
    usage: LLMUsage
    latency_ms: int


class LLMError(Exception):
    """Provider error, timeout, or abort raised by an :class:`LLMClient`.

    Carries a failure ``category`` (for example ``"timeout"``,
    ``"invalid_key"``, ``"unreachable"``, ``"provider_error"``) and an
    optional operator-safe detail string. It never carries provider response
    bodies, prompt content, or the API key (Requirement 1.9); the category is
    what the orchestrator maps onto the closed ``FailureReason`` enum for the
    fallback path of Requirement 9.
    """

    def __init__(self, category: str, detail: str | None = None) -> None:
        self.category = category
        self.detail = detail
        super().__init__(f"LLM call failed: {category}" if detail is None else detail)


class LLMClient(Protocol):
    """The provider-neutral client interface (Requirement 1.1).

    Implemented by exactly one adapter module per provider. Callers depend
    only on this protocol; nothing above it may construct provider HTTP
    requests directly (Requirement 1.2).
    """

    async def validate_credentials(self) -> None:
        """Validate the configured API key against the provider.

        Used by the startup check (Requirement 1.10). Raises
        :class:`LLMError` with a category distinguishing an invalid key, an
        unreachable provider, or a validation timeout — never exposing the
        key value.
        """
        ...

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        """Execute ``request`` and yield raw output fragments as they arrive.

        Exactly one provider attempt, bounded by the configured wall-clock
        timeout covering the full call including streaming (Requirements 1.6,
        1.12). On failure or timeout the iterator raises :class:`LLMError`.
        """
        ...

    async def result(self) -> LLMCompletion:
        """Return the accumulated completion after stream exhaustion/abort.

        Only meaningful once the iterator returned by :meth:`stream` has been
        exhausted (or aborted); carries the assembled text plus usage and
        latency for invocation logging (Requirement 12.2).
        """
        ...
