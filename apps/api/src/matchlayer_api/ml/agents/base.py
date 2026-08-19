"""Abstract agent root: the shared invocation lifecycle (phase-4-agentic).

:class:`BaseAgent` is the root of the deliberate three-level agent
hierarchy (design section "2. Agent class hierarchy", decision D2). It owns
everything cross-cutting — per-node timeout, graceful degradation,
Agent_Run persistence, span emission, latency accounting — as a **final
template method** (:meth:`BaseAgent.__call__`), so all five graph nodes
behave identically (Requirements 8, 12, 13). Concrete agents implement
only :meth:`BaseAgent.run`, :meth:`BaseAgent.build_degraded`, and
:meth:`BaseAgent.build_minimal`.

Contract rules encoded here:

* **One lifecycle.** ``__call__`` is the only entry point the graph sees
  and is never overridden by a concrete agent (verified by a hierarchy
  unit test). Its signature — ``(AgentState) -> dict`` — is exactly the
  LangGraph node signature, so instances register as nodes directly.
* **Typed I/O.** Each subclass binds ``TOut`` to its Pydantic output model
  and declares the single :class:`~matchlayer_api.ml.agents.state.AgentState`
  field it writes via :attr:`BaseAgent.output_field` — the one-writer-per-
  field discipline that makes LangGraph's parallel merge safe
  (Requirement 1.2).
* **Error handling.** Any exception from ``run`` (including timeout
  cancellation and output ``ValidationError``) is classified by
  :func:`classify_failure` into the closed
  :data:`~matchlayer_api.ml.agents.state.FailureTrigger` vocabulary and
  routed to :meth:`BaseAgent._build_degraded_safely` (Requirements 8.1,
  8.4). If degraded construction itself raises, the agent falls back to
  its minimal schema-valid output and records
  ``degraded_construction_error`` (Requirement 8.6).
* **Timeout.** ``asyncio.wait_for`` bounds every ``run`` (design D7):
  cancellation abandons in-flight work and discards any post-timeout
  result (Requirement 8.3). The timeout value arrives via
  :class:`AgentDeps` — never read from configuration here.
* **Latency.** Measured node-invocation-start → output-return on the
  injected monotonic clock — the same boundary as the timeout and the
  span, so ``agent_runs.latency_ms``, span duration, and timeout
  accounting agree (Requirements 8.3, 12.2, 13.1).
* **Tracing.** One span per invocation named ``agent.{name}``
  (Requirement 13.1). :meth:`BaseAgent.on_span_start` /
  :meth:`BaseAgent.on_span_end` are the sanctioned extension points for
  span attributes; defaults record status and duration only — identifiers
  and hashes at most, never Restricted PII (`security.md`).
* **Dependency injection.** Agents receive everything through
  :class:`AgentDeps` at construction: no global state, no direct
  configuration reads, trivially testable instances.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol

from opentelemetry.trace import Span, Tracer
from pydantic import BaseModel, ValidationError

from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    FailureDetail,
)

__all__ = [
    "AgentDeps",
    "BaseAgent",
    "Clock",
    "EmptyInputError",
    "PersistAgentRun",
    "classify_failure",
]


class Clock(Protocol):
    """Injected monotonic time source (the ``time`` module satisfies it)."""

    def monotonic(self) -> float:
        """Monotonic seconds; differences measure elapsed wall-clock time."""
        ...


type PersistAgentRun = Callable[
    [str, AgentState, BaseModel, AgentCompletion, FailureDetail | None, int],
    Awaitable[None],
]
"""Callback persisting one Agent_Run row per node invocation (Req 12.2).

Arguments: ``(agent_name, input_state, output, status, failure_reason,
latency_ms)``. Provided by ``services/agent_jobs/runs.py`` in production
and by fakes in tests — the agent layer never touches the database
directly.
"""


@dataclass(frozen=True, slots=True)
class AgentDeps:
    """Everything a :class:`BaseAgent` needs, injected at construction.

    No agent reads configuration or holds global state: the per-node
    timeout comes from ``Settings`` (``MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS``)
    at composition time, persistence is a callback, tracing is an injected
    :class:`~opentelemetry.trace.Tracer` (a no-op tracer when no exporter
    is configured, Requirement 13.5), and time is an injected clock so
    latency accounting is testable.
    """

    node_timeout_s: float
    persist_agent_run: PersistAgentRun
    tracer: Tracer
    clock: Clock


class EmptyInputError(Exception):
    """A required agent input is empty or unavailable (Requirement 3.6).

    Raised by an LLM agent's ``build_prompt_input`` (e.g. empty/None
    redacted resume text). :func:`classify_failure` maps it to the
    ``empty_input`` trigger; the raising agent takes its degraded path with
    zero LLM_Provider calls and zero Daily_Quota consumption, because the
    exception fires before any orchestrator invocation.
    """


def classify_failure(exc: BaseException) -> FailureDetail:
    """Map an exception from ``run`` onto the closed FailureTrigger vocabulary.

    The mapping (Requirement 8.4's structured failure reason):

    * :class:`EmptyInputError` → ``empty_input``
    * Phase 3 ``DailyQuotaExceededError`` → ``quota_exhausted`` (Req 9.9)
    * Phase 3 ``SpendLimitExceededError`` → ``breaker_open`` (Req 9.5)
    * :class:`TimeoutError` (what ``asyncio.wait_for`` raises) → ``timeout``
    * :class:`pydantic.ValidationError` → ``schema_validation``
    * anything else → ``error``

    ``detail`` is operator-safe display text: fixed strings or the
    exception *class name* only — never ``str(exc)``, which could carry
    resume content, provider response bodies, or secrets (`security.md`).
    """
    # Imported lazily so the agent hierarchy's module import graph stays free
    # of the LLM machinery: `services.llm.orchestrator` pulls in `ml/llm/`
    # at module level, and DeterministicAgent subclasses must not acquire
    # that dependency through their base module (Requirement 1.6, design
    # "DeterministicAgent" notes).
    from matchlayer_api.services.llm.orchestrator import (
        DailyQuotaExceededError,
        SpendLimitExceededError,
    )

    if isinstance(exc, EmptyInputError):
        return FailureDetail(trigger="empty_input", detail="required input empty or unavailable")
    if isinstance(exc, DailyQuotaExceededError):
        return FailureDetail(
            trigger="quota_exhausted",
            detail="Daily_Quota atomic reserve failed at call initiation",
        )
    if isinstance(exc, SpendLimitExceededError):
        return FailureDetail(trigger="breaker_open", detail="Spend_Circuit_Breaker is open")
    if isinstance(exc, TimeoutError):
        return FailureDetail(trigger="timeout", detail="node exceeded the per-node timeout")
    if isinstance(exc, ValidationError):
        return FailureDetail(
            trigger="schema_validation",
            detail="output failed Pydantic schema validation",
        )
    return FailureDetail(trigger="error", detail=type(exc).__name__)


class BaseAgent[TOut: BaseModel](ABC):
    """Abstract root: subclasses supply agent identity and pure logic;
    the base class owns the invocation lifecycle (template method).
    """

    #: agent_name used for agent_runs rows, span names, and cache keys.
    name: ClassVar[str]
    #: The single AgentState field this agent writes (safe parallel merge).
    output_field: ClassVar[str]

    def __init__(self, deps: AgentDeps) -> None:
        self._deps = deps

    # ---- the methods every concrete agent implements ---------------------

    @abstractmethod
    async def run(self, state: AgentState) -> TOut:
        """Pure agent logic over state. Raises on any failure."""

    @abstractmethod
    def build_degraded(self, state: AgentState) -> TOut:
        """Schema-conformant non-LLM fallback (Degraded_Output, Req 8.2)."""

    @abstractmethod
    def build_minimal(self) -> TOut:
        """Minimal schema-valid output: empty/null optional content only.

        The last-resort Degraded_Output used when :meth:`build_degraded`
        itself raises (Requirement 8.6). Must not depend on state or
        persisted data — it exists precisely because those may be
        unavailable — so it can never fail.
        """

    # ---- tracing hooks (defaults: statuses/ids only, never PII) ----------

    def on_span_start(self, span: Span, state: AgentState) -> None:  # noqa: B027
        """Attach pre-invocation span attributes. Default: none (intentional no-op hook).

        The LLM branch overrides this pair to attach prompt version, model
        id, and input hash — mirroring the LLM_Invocation_Log values
        (Requirement 13.2). Overrides must attach identifiers and hashes
        only, never Restricted PII.
        """

    def on_span_end(self, span: Span, status: AgentCompletion, latency_ms: int) -> None:
        """Attach post-invocation span attributes. Default: status + latency."""
        span.set_attribute("agent.status", status.value)
        span.set_attribute("agent.latency_ms", latency_ms)

    # ---- degradation ------------------------------------------------------

    def _build_degraded_safely(
        self, state: AgentState, reason: FailureDetail
    ) -> tuple[TOut, FailureDetail]:
        """Build the Degraded_Output, surviving degraded-construction failure.

        Returns ``(output, reason)``: on success the caller's *reason*
        passes through unchanged; if :meth:`build_degraded` raises, the
        agent's minimal schema-valid output is returned instead and the
        reason becomes ``degraded_construction_error`` recording both the
        construction failure and the original trigger (Requirement 8.6).

        Always called from within the ``except`` block of
        :meth:`__call__`, so an override may re-raise the original
        exception with a bare ``raise`` — the Synthesizer does exactly
        that, making its failure the only single-node failure that fails
        the job (Requirement 7.6).
        """
        try:
            return self.build_degraded(state), reason
        except Exception as construction_exc:  # Req 8.6: never fail the node
            return self.build_minimal(), FailureDetail(
                trigger="degraded_construction_error",
                detail=(
                    f"degraded construction raised {type(construction_exc).__name__} "
                    f"after trigger '{reason.trigger}'"
                ),
            )

    # ---- the lifecycle: final template method, the LangGraph node ---------

    async def __call__(self, state: AgentState) -> dict[str, object]:
        """The invocation lifecycle. Never overridden by concrete agents.

        Span emission → timeout-bounded ``run`` → (on failure) classify +
        degrade → latency accounting → Agent_Run persistence → partial
        state update ``{output_field: output, "agent_status": {...}}``.
        """
        with self._deps.tracer.start_as_current_span(f"agent.{self.name}") as span:
            self.on_span_start(span, state)
            start = self._deps.clock.monotonic()
            reason: FailureDetail | None = None
            output: TOut
            try:
                output = await asyncio.wait_for(self.run(state), self._deps.node_timeout_s)
                status = AgentCompletion.COMPLETED
            except Exception as exc:  # error | TimeoutError | ValidationError | ...
                output, reason = self._build_degraded_safely(state, classify_failure(exc))
                status = AgentCompletion.DEGRADED
            latency_ms = int((self._deps.clock.monotonic() - start) * 1000)
            await self._deps.persist_agent_run(self.name, state, output, status, reason, latency_ms)
            self.on_span_end(span, status, latency_ms)
            return {
                self.output_field: output,
                "agent_status": {
                    self.name: AgentStatusFlag(
                        status=status, failure_reason=reason, latency_ms=latency_ms
                    )
                },
            }
