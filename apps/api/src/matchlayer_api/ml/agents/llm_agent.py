"""LLM branch of the agent hierarchy (phase-4-agentic).

:class:`LLMAgent` is the intermediate abstract class for the two LLM
agents (Resume_Analysis, Improvement). It makes :meth:`LLMAgent.run`
**final** and delegates every provider interaction to the Phase 3
``LLMOrchestrator`` (design decision D1), so no concrete LLM agent can
bypass quota, redaction, caching, invocation logging, or fallback
plumbing (Requirements 9.1, 9.2, 9.3, 9.6, 9.7). Concrete subclasses
implement only :meth:`LLMAgent.feature_spec` and
:meth:`LLMAgent.build_prompt_input` (plus the ``build_degraded`` /
``build_minimal`` pair inherited from
:class:`~matchlayer_api.ml.agents.base.BaseAgent`).

Adaptation of the design sketch to the real Phase 3 API
-------------------------------------------------------

The design sketches ``orchestrator.invoke(spec, user_content, user_id)``
returning the output model directly. The shipped Phase 3 orchestrator
(``services/llm/orchestrator.py``) differs in three ways, and this module
adapts to each while preserving the design intent (all cost/privacy
plumbing flows through the one orchestrator):

1. **Two-phase API instead of ``invoke``.** The orchestrator exposes
   ``prepare`` (all pre-call gates: quota gate, key check, redaction,
   prompt assembly, input hash, cache lookup, breaker, atomic quota
   reserve) and ``execute`` (the single provider call). ``prepare``
   returning an :class:`~matchlayer_api.services.llm.orchestrator.LLMOutcome`
   means the request resolved **without** a provider call (cache hit,
   persisted-result reuse, or pre-call fallback); returning a
   :class:`~matchlayer_api.services.llm.orchestrator.ProviderCallPlan`
   means a call is initiated (the quota unit is already reserved,
   Requirement 9.4). That distinction is exactly the "did a provider call
   occur" signal Requirement 13.2 needs, so :meth:`LLMAgent.run` uses the
   two-phase form and the span hooks attach prompt version, model id, and
   input hash **only** when a plan was produced — mirroring the values
   recorded in that call's LLM_Invocation_Log entry.
2. **The orchestrator needs the persisted ``MatchResult``.** Its
   persistence and invocation-log writes are keyed by the ORM row, which
   :class:`~matchlayer_api.ml.agents.state.AgentState` deliberately does
   not carry (identifiers only, Requirement 1.3). The agent therefore
   receives an :class:`AgentLLMOrchestrator` — a job-scoped handle. The
   production implementation, :class:`MatchScopedOrchestrator`, binds the
   Phase 3 ``LLMOrchestrator`` to the job's ``MatchResult`` (loaded once
   by the Agent_Worker before graph invocation) and the configured model
   id; the graph composition layer (``ml/agents/graph.py``) constructs it
   per job. Tests inject fakes satisfying the same protocol.
3. **LLM failures surface as fallback envelopes, not exceptions.** The
   orchestrator maps provider errors, timeouts, and schema-validation
   failures onto a schema-conformant Fallback_Response
   (``is_fallback=True``) instead of raising. The *agent's* degradation
   policy (Requirement 8) wants those routed through its own
   ``build_degraded`` so the Agent_Run row records status ``degraded``
   with a structured failure reason — so :meth:`LLMAgent.run` raises
   :class:`LLMFallbackError` on any fallback envelope, letting
   ``BaseAgent.__call__`` classify and degrade. The two gate rejections
   the orchestrator *does* raise — ``DailyQuotaExceededError`` and
   ``SpendLimitExceededError`` — propagate unchanged and are already
   mapped by :func:`~matchlayer_api.ml.agents.base.classify_failure` onto
   the ``quota_exhausted`` (Requirement 9.9) and ``breaker_open``
   triggers.

Ordering guarantee for empty input (Requirement 3.6 via 9.4):
:meth:`LLMAgent.build_prompt_input` is invoked **before** any orchestrator
interaction, so an :class:`~matchlayer_api.ml.agents.base.EmptyInputError`
degrades the node with **zero** provider calls and **zero** Daily_Quota
consumption — the orchestrator is never reached.

Instance-state note: the provider-call info stash (``_provider_call``)
makes an :class:`LLMAgent` instance single-invocation-at-a-time. That
holds by construction: agents are built per job by the composition layer
and each graph node runs at most once per job execution.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, final
from uuid import UUID

from opentelemetry.trace import Span
from pydantic import BaseModel

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.state import AgentCompletion, AgentState
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOrchestrator,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import FailureReason

__all__ = [
    "AgentLLMOrchestrator",
    "LLMAgent",
    "LLMFallbackError",
    "MatchScopedOrchestrator",
    "ProviderCallInfo",
]


class LLMFallbackError(Exception):
    """The orchestrator resolved the request to a Fallback_Response.

    Raised by :meth:`LLMAgent.run` when the outcome envelope carries
    ``is_fallback=True`` (provider error, timeout, schema-validation
    failure, key absent, redaction failure, ...), so the agent takes its
    own Degraded_Output path per Requirement 8 and the Agent_Run row
    records status ``degraded``. The message is the closed
    :class:`~matchlayer_api.services.llm.schemas.FailureReason` value only
    — never provider response bodies or prompt content — and
    ``classify_failure`` records just the class name as the operator-safe
    detail.
    """

    def __init__(self, reason: FailureReason | None) -> None:
        self.reason = reason
        super().__init__(reason.value if reason is not None else "unknown")


@dataclass(frozen=True, slots=True)
class ProviderCallInfo:
    """Span attributes for one initiated provider call (Requirement 13.2).

    Each value equals the corresponding value recorded in the call's
    LLM_Invocation_Log entry (``prompt_template_version``, ``llm_model``,
    ``input_hash`` — the hash is computed over redacted text), so span
    data and invocation logs cross-reference exactly. Identifiers and
    hashes only — never Restricted PII (`security.md`).
    """

    prompt_version: int
    model: str
    input_hash: str


class AgentLLMOrchestrator(Protocol):
    """Job-scoped view of the Phase 3 ``LLMOrchestrator`` (module docstring §2).

    ``prepare`` / ``execute`` carry the exact Phase 3 two-phase semantics;
    the job's ``MatchResult`` is bound behind the handle rather than
    threaded through agent state. ``model`` is the configured model
    identifier the orchestrator sends every call to — surfaced here so the
    span hooks can mirror the invocation log (Requirement 13.2).
    """

    @property
    def model(self) -> str:
        """The configured LLM model identifier."""
        ...

    async def prepare[TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[str, TResult],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[TResult] | ProviderCallPlan[str, TResult]:
        """Run every pre-call gate; plan iff a provider call is initiated."""
        ...

    async def execute[TResult: BaseModel](
        self, plan: ProviderCallPlan[str, TResult]
    ) -> LLMOutcome[TResult]:
        """Perform the single provider call for a prepared plan."""
        ...


@dataclass(frozen=True, slots=True)
class MatchScopedOrchestrator:
    """The production :class:`AgentLLMOrchestrator`.

    Binds the Phase 3 :class:`LLMOrchestrator` to the job's persisted
    ``MatchResult`` (loaded once by the Agent_Worker) and the configured
    model id (``Settings.llm_model`` — the same source the orchestrator
    reads, so span attributes and invocation-log rows agree). Constructed
    per job by the graph composition layer; agents never touch the
    database or configuration themselves.
    """

    orchestrator: LLMOrchestrator
    match: MatchResult
    model: str

    async def prepare[TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[str, TResult],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[TResult] | ProviderCallPlan[str, TResult]:
        """Delegate to ``LLMOrchestrator.prepare`` with the bound match."""
        return await self.orchestrator.prepare(
            spec, user_id=UUID(user_id), match=self.match, feature_input=feature_input
        )

    async def execute[TResult: BaseModel](
        self, plan: ProviderCallPlan[str, TResult]
    ) -> LLMOutcome[TResult]:
        """Delegate to ``LLMOrchestrator.execute`` (the one provider call)."""
        return await self.orchestrator.execute(plan)


class LLMAgent[TOut: BaseModel](BaseAgent[TOut]):
    """Intermediate abstract class for LLM-backed agents.

    ``run`` is final: every LLM call flows through the injected
    :class:`AgentLLMOrchestrator` and therefore through the Phase 3
    pipeline — atomic Daily_Quota reserve at call initiation, PII
    redaction, prompt assembly from the registry-resolved versioned
    template, input hashing over redacted text, per-user cache,
    Spend_Circuit_Breaker, invocation logging, and output schema
    validation (Requirements 9.1-9.7). Concrete subclasses supply the
    feature parameterization and the prompt-input builder only.
    """

    def __init__(self, deps: AgentDeps, orchestrator: AgentLLMOrchestrator) -> None:
        super().__init__(deps)
        self._orchestrator = orchestrator
        self._provider_call: ProviderCallInfo | None = None

    # ---- what a concrete LLM agent implements -----------------------------

    @abstractmethod
    def feature_spec(self) -> LLMFeatureSpec[str, TOut]:
        """The feature's pipeline parameterization (design D1).

        Prompt identity resolved through the Phase 3 active-version
        registry (never a string literal, Requirement 9.6), output schema
        bound to ``TOut``, fallback builder, and the agent-specific cache
        namespace via the feature value (Requirement 9.7).
        """

    @abstractmethod
    def build_prompt_input(self, state: AgentState) -> str:
        """Assemble the delimited user-content region from state.

        May raise :class:`~matchlayer_api.ml.agents.base.EmptyInputError`
        (e.g. empty/None redacted resume text): the node degrades with the
        ``empty_input`` reason, zero provider calls, and zero Daily_Quota
        consumption, because this method runs before any orchestrator
        interaction (Requirements 3.6, 9.4).
        """

    def finalize_output(self, state: AgentState, output: TOut) -> TOut:
        """Deterministic post-validation adjustment of the validated output.

        Called by the final :meth:`run` on every successful outcome —
        fresh provider call, cache hit, or persisted-result reuse alike —
        so state-derived markers land uniformly on every path. The
        default is the identity. Overrides must stay pure functions of
        ``(state, output)``: no I/O, no clock, and never a second
        provider interaction — the orchestrator remains the only LLM
        gateway (Requirement 9.1). The Improvement_Agent overrides this
        to mark reports derived from a degraded Candidate_Profile
        (Requirement 6.5, design "3. The five concrete agents").
        """
        del state  # identity default; overrides read it
        return output

    # ---- the final LLM lifecycle: delegate everything to Phase 3 ----------

    @final
    async def run(self, state: AgentState) -> TOut:
        """Delegate to the Phase 3 orchestrator; never call a provider directly.

        Raises on every non-success so ``BaseAgent.__call__`` classifies
        and degrades: ``EmptyInputError`` (before any orchestrator work),
        the orchestrator's two gate rejections (→ ``quota_exhausted`` /
        ``breaker_open``), and :class:`LLMFallbackError` for any fallback
        envelope. A cache or reuse hit returns the stored validated output
        with no provider call and no quota consumption (Requirement 9.7).
        """
        user_content = self.build_prompt_input(state)  # EmptyInputError → degraded, zero calls
        spec = self.feature_spec()
        prepared = await self._orchestrator.prepare(
            spec, user_id=state.user_id, feature_input=user_content
        )
        outcome: LLMOutcome[TOut]
        if isinstance(prepared, ProviderCallPlan):
            # A provider call is initiated (quota already reserved): stash
            # the invocation-log-mirroring span attributes (Req 13.2).
            self._provider_call = ProviderCallInfo(
                prompt_version=prepared.template_version,
                model=self._orchestrator.model,
                input_hash=prepared.input_hash,
            )
            outcome = await self._orchestrator.execute(prepared)
        else:
            outcome = prepared
        envelope = outcome.envelope
        if envelope.is_fallback:
            raise LLMFallbackError(envelope.fallback_reason)
        return self.finalize_output(state, envelope.result)

    # ---- span hooks: LLM attributes only when a provider call occurred ----

    def on_span_start(self, span: Span, state: AgentState) -> None:
        """Reset the per-invocation provider-call stash.

        Runs synchronously before ``run`` inside ``BaseAgent.__call__``,
        so a previous invocation's call info can never leak onto this
        invocation's span.
        """
        self._provider_call = None

    def on_span_end(self, span: Span, status: AgentCompletion, latency_ms: int) -> None:
        """Attach the LLM attributes iff this invocation initiated a call.

        Cache hits, reuse hits, and no-call degraded paths (empty input,
        quota exhausted, breaker open, pre-call fallback) leave the three
        attributes absent, per Requirement 13.2. Values mirror the
        LLM_Invocation_Log entry; identifiers and hashes only, never PII.
        """
        super().on_span_end(span, status, latency_ms)
        call = self._provider_call
        if call is not None:
            span.set_attribute("llm.prompt_version", call.prompt_version)
            span.set_attribute("llm.model", call.model)
            span.set_attribute("llm.input_hash", call.input_hash)
