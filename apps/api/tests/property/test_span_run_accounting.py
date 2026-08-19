"""Feature: phase-4-agentic — Property 18.

Property 18: Span accounting mirrors run accounting.

    *For any* run shape (any degradation/cache combination), the captured
    span tree contains exactly one job span carrying the Agent_Job id and
    exactly one child span per agent invocation whose status attribute
    equals the corresponding ``agent_runs`` row's status and whose
    duration covers the same boundary; and every child span for an
    invocation that made an LLM call carries prompt version, model
    identifier, and input hash equal to the corresponding
    LLM_Invocation_Log values.

**Validates: Requirements 13.1, 13.2**

Instrumentation: the **real five agents** are wired through the real
``build_agents`` / ``compile_graph`` composition layer and driven via
``ainvoke`` under a locally constructed ``TracerProvider`` +
``InMemorySpanExporter`` injected through ``AgentDeps`` — the global
tracer provider is never mutated (the ``test_tracing.py`` discipline).
The graph invocation runs inside a mirror of the Agent_Worker's job span
(``agent.job`` carrying ``matchlayer.job_id``), so every agent child span
nests under it exactly as in production (Requirement 13.1).

Run shapes are generated at the collaborator seams:

* each LLM agent draws one of ``ok`` (resolved in ``prepare`` with no
  provider call — the cache-hit / reuse shape), ``call`` (a
  ``ProviderCallPlan`` is produced and ``execute`` succeeds),
  ``call_fallback`` (a call occurs but resolves to a fallback envelope —
  degraded *with* the LLM attributes, the Requirement 13.2 edge),
  ``exception`` / ``quota`` / ``breaker`` (degraded with **no** call);
* the ATS agent draws ``ok`` or ``exception`` through its scorer seam.

The scripted orchestrator records the values it would have written to the
LLM_Invocation_Log for every initiated call (prompt version, model,
input hash over the provider-bound text), and the property asserts the
span attributes equal those recorded values exactly — present iff a call
occurred (Requirement 13.2).

Latency accounting rides an injected ticking clock, so the
``agent.latency_ms`` span attribute and the ``latency_ms`` handed to the
``persist_agent_run`` callback must agree over the same node-invocation
boundary (Requirement 13.1).

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 18: Span accounting mirrors run accounting

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, cast
from uuid import UUID

from hypothesis import example, given, settings
from hypothesis import strategies as st
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.llm.client import LLMRequest
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    DailyQuotaExceededError,
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
    SpendLimitExceededError,
)
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope

# ---------------------------------------------------------------------------
# Run-shape vocabulary.
# ---------------------------------------------------------------------------

# LLM agents: no-call completions, a genuine provider call, a call that
# resolves to a fallback (degraded WITH the LLM attributes), and no-call
# degradations. ATS: normal or scorer failure. Timeout shapes are covered
# by Property 4; the statuses here already span completed/degraded with
# and without provider calls — the accounting axes of Requirement 13.
_LLM_MODES = ("ok", "call", "call_fallback", "exception", "quota", "breaker")
_ATS_MODES = ("ok", "exception")

# The mode combinations under which a provider call occurred (Req 13.2).
_CALL_MODES = frozenset({"call", "call_fallback"})

_LLM_AGENT_FEATURE: dict[str, LLMFeature] = {
    "resume_analysis": LLMFeature.AGENT_RESUME_ANALYSIS,
    "improvement": LLMFeature.AGENT_IMPROVEMENT,
}

# Distinct per-feature prompt versions so the cross-reference assertion is
# meaningful (a swapped value would be caught).
_PROMPT_VERSIONS: dict[LLMFeature, int] = {
    LLMFeature.AGENT_RESUME_ANALYSIS: 3,
    LLMFeature.AGENT_IMPROVEMENT: 7,
}

_MODEL = "test-model"
_LLM_ATTRS = frozenset({"llm.prompt_version", "llm.model", "llm.input_hash"})

_AGENT_NAMES = ("resume_analysis", "ats", "skill_gap", "improvement", "synthesizer")

_STALE_SCORER_VERSION = "1.0.0+stale"
_ACTIVE_SCORER_VERSION = "2.0.0+active"

_NODE_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Fakes: ticking clock, run recorder, scripted orchestrator, scripted scorer.
# ---------------------------------------------------------------------------


class _TickingClock:
    """Monotonic fake advancing on every read, so latencies are non-trivial."""

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        self._now += 0.007
        return self._now


class _RunRecorder:
    """Records every persist_agent_run callback — the agent_runs rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentCompletion, FailureDetail | None, int]] = []

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        del state, output
        self.calls.append((agent_name, status, reason, latency_ms))


class _LoggedCall(BaseModel):
    """The invocation-log values recorded for one initiated provider call."""

    prompt_version: int
    model: str
    input_hash: str


class _ScriptedOrchestrator:
    """AgentLLMOrchestrator fake scripting call/no-call/failure per feature.

    For every initiated call (``call`` / ``call_fallback``) it records the
    exact values the Phase 3 pipeline would write to that call's
    LLM_Invocation_Log entry — prompt version, model identifier, and the
    input hash over the provider-bound text — which Requirement 13.2
    obliges the child span to mirror.
    """

    def __init__(self, modes: dict[LLMFeature, str]) -> None:
        self._modes = modes
        self.logged: dict[LLMFeature, _LoggedCall] = {}

    @property
    def model(self) -> str:
        return _MODEL

    async def prepare(
        self,
        spec: LLMFeatureSpec[str, Any],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[Any] | ProviderCallPlan[str, Any]:
        mode = self._modes.get(spec.feature, "ok")
        if mode == "exception":
            raise RuntimeError("injected orchestrator failure")
        if mode == "quota":
            from datetime import UTC, datetime

            raise DailyQuotaExceededError(
                limit=25, remaining=0, resets_at=datetime(2025, 1, 2, tzinfo=UTC)
            )
        if mode == "breaker":
            raise SpendLimitExceededError("spend limit reached")
        if mode in _CALL_MODES:
            version = _PROMPT_VERSIONS[spec.feature]
            input_hash = hashlib.sha256(feature_input.encode("utf-8")).hexdigest()
            self.logged[spec.feature] = _LoggedCall(
                prompt_version=version, model=_MODEL, input_hash=input_hash
            )
            return ProviderCallPlan(
                spec=spec,
                envelope_cls=cast("type[LLMResultEnvelope[Any]]", LLMResultEnvelope),
                user_id=UUID(user_id),
                match=cast("MatchResult", object()),
                feature_input=feature_input,
                request=cast("LLMRequest", None),
                template_version=version,
                input_hash=input_hash,
                quota_remaining=1,
            )
        # "ok": resolved in prepare with no provider call (cache-hit shape).
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=False,
            fallback_reason=None,
            result=spec.result_schema(),
        )
        return LLMOutcome(envelope=envelope, quota_remaining=10)

    async def execute(self, plan: ProviderCallPlan[str, Any]) -> LLMOutcome[Any]:
        mode = self._modes.get(plan.spec.feature, "ok")
        assert mode in _CALL_MODES, "execute reached without an initiated call"
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=(mode == "call_fallback"),
            fallback_reason=FailureReason.PROVIDER_ERROR if mode == "call_fallback" else None,
            result=plan.spec.result_schema(),
        )
        return LLMOutcome(envelope=envelope, quota_remaining=9)


class _ScriptedScorer:
    """ScorerAdapter fake failing on command."""

    active_scorer_version = _ACTIVE_SCORER_VERSION
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    def __init__(self, mode: str = "ok") -> None:
        self._mode = mode

    async def score(self) -> ScoredMatch:
        if self._mode == "exception":
            raise RuntimeError("injected total scoring failure")
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version=_ACTIVE_SCORER_VERSION,
            semantic=True,
        )


# ---------------------------------------------------------------------------
# Generators.
# ---------------------------------------------------------------------------

_skill = st.sampled_from(["python", "aws", "sql", "react", "kubernetes", "go"])

# Snapshots carry a stale Scorer_Version so the ATS agent invokes score()
# — the seam its failure injection lives behind.
_snapshots = st.builds(
    MatchSnapshot,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.dictionaries(
        st.sampled_from(["similarity", "keyword"]),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        max_size=2,
    ),
    scorer_version=st.just(_STALE_SCORER_VERSION),
    matched_skills=st.lists(_skill, max_size=4),
    missing_skills=st.lists(_skill, max_size=4),
    suggestions=st.lists(st.text(max_size=20), max_size=2),
)


@st.composite
def _initial_states(draw: st.DrawFn) -> AgentState:
    """A valid initial AgentState as the worker builds it."""
    return AgentState(
        job_id=draw(st.uuids(version=4).map(str)),
        match_id=draw(st.uuids(version=4).map(str)),
        user_id=draw(st.uuids(version=4).map(str)),
        redacted_resume_text=draw(
            st.text(min_size=1, max_size=80).filter(lambda s: bool(s.strip()))
        ),
        job_description_skills=draw(st.lists(_skill, max_size=6)),
        match_snapshot=draw(_snapshots),
    )


@st.composite
def _run_shapes(draw: st.DrawFn) -> dict[str, str]:
    """One mode per injectable agent — any degradation/call combination."""
    return {
        "resume_analysis": draw(st.sampled_from(_LLM_MODES)),
        "improvement": draw(st.sampled_from(_LLM_MODES)),
        "ats": draw(st.sampled_from(_ATS_MODES)),
    }


def _base_state() -> AgentState:
    return AgentState(
        job_id="00000000-0000-4000-8000-000000000001",
        match_id="00000000-0000-4000-8000-000000000002",
        user_id="00000000-0000-4000-8000-000000000003",
        redacted_resume_text="[NAME_1] built services in Python.",
        job_description_skills=["python", "kubernetes"],
        match_snapshot=MatchSnapshot(
            score=71.5,
            breakdown={"similarity": 0.6},
            scorer_version=_STALE_SCORER_VERSION,
            matched_skills=["python"],
            missing_skills=["kubernetes"],
            suggestions=["Add a Kubernetes project."],
        ),
    )


# ---------------------------------------------------------------------------
# Driving one traced graph execution.
# ---------------------------------------------------------------------------


async def _run_graph(
    state: AgentState,
    modes: dict[str, str],
    tracer_provider: TracerProvider,
) -> tuple[_RunRecorder, _ScriptedOrchestrator]:
    recorder = _RunRecorder()
    tracer = tracer_provider.get_tracer("matchlayer.agent_worker.test")
    deps = AgentDeps(
        node_timeout_s=_NODE_TIMEOUT_S,
        persist_agent_run=recorder,
        tracer=tracer,
        clock=_TickingClock(),
    )
    orchestrator = _ScriptedOrchestrator(
        {
            LLMFeature.AGENT_RESUME_ANALYSIS: modes["resume_analysis"],
            LLMFeature.AGENT_IMPROVEMENT: modes["improvement"],
        }
    )
    scorer = _ScriptedScorer(modes["ats"])
    compiled = compile_graph(build_agents(deps, orchestrator, scorer))
    # Mirror the Agent_Worker's job-span framing (agent_worker.py): one
    # job span named "agent.job" carrying the Agent_Job id, opened around
    # the graph invocation so every agent child span nests under it
    # (Requirement 13.1).
    with tracer.start_as_current_span("agent.job") as span:
        span.set_attribute("matchlayer.job_id", state.job_id)
        await compiled.ainvoke(state)
    return recorder, orchestrator


def _run_sync(
    state: AgentState, modes: dict[str, str]
) -> tuple[_RunRecorder, _ScriptedOrchestrator, list[ReadableSpan]]:
    # A locally constructed provider + in-memory exporter: the global
    # tracer provider is never touched (the test_tracing.py discipline).
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        with asyncio.Runner() as runner:
            recorder, orchestrator = runner.run(_run_graph(state, modes, provider))
        spans = list(exporter.get_finished_spans())
    finally:
        provider.shutdown()
    return recorder, orchestrator, spans


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 18: Span accounting mirrors run accounting
@settings(max_examples=100, deadline=None)
@example(  # all-normal run with both LLM agents making genuine calls
    state=_base_state(),
    modes={"resume_analysis": "call", "improvement": "call", "ats": "ok"},
)
@example(  # a call that degrades: LLM attributes present on a degraded span
    state=_base_state(),
    modes={"resume_analysis": "call_fallback", "improvement": "breaker", "ats": "exception"},
)
@example(  # no provider call anywhere: LLM attributes absent everywhere
    state=_base_state(),
    modes={"resume_analysis": "ok", "improvement": "quota", "ats": "ok"},
)
@given(state=_initial_states(), modes=_run_shapes())
def test_span_accounting_mirrors_run_accounting(state: AgentState, modes: dict[str, str]) -> None:
    """One job span + exactly one agent span per persisted run, with status
    and latency attributes equal to the run-row values (Requirement 13.1),
    and the LLM attributes present — mirroring the invocation-log values —
    iff the invocation made a provider call (Requirement 13.2)."""
    recorder, orchestrator, spans = _run_sync(state, modes)

    # ---- exactly one job span carrying the Agent_Job id (Req 13.1) --------
    job_spans = [span for span in spans if span.name == "agent.job"]
    assert len(job_spans) == 1, f"expected one job span, got {len(job_spans)}"
    job_span = job_spans[0]
    assert job_span.attributes is not None
    assert job_span.attributes["matchlayer.job_id"] == state.job_id
    assert job_span.context is not None

    # ---- the persisted run accounting: one callback per node --------------
    persisted: dict[str, tuple[AgentCompletion, FailureDetail | None, int]] = {}
    for name, status, reason, latency_ms in recorder.calls:
        assert name not in persisted, f"duplicate persist callback for {name}"
        persisted[name] = (status, reason, latency_ms)
    assert set(persisted) == set(_AGENT_NAMES)

    # ---- exactly one child span per agent invocation (Req 13.1) -----------
    agent_spans: dict[str, ReadableSpan] = {}
    for span in spans:
        if span.name == "agent.job" or not span.name.startswith("agent."):
            continue
        name = span.name.removeprefix("agent.")
        assert name not in agent_spans, f"duplicate span for {span.name}"
        agent_spans[name] = span
    assert set(agent_spans) == set(persisted), (
        "the set of agent spans must equal the set of persisted runs"
    )

    for name, (status, _reason, latency_ms) in persisted.items():
        span = agent_spans[name]
        attributes = span.attributes
        assert attributes is not None

        # Child of the job span: one end-to-end trace per job (Req 13.1).
        assert span.parent is not None, f"agent span {span.name} has no parent"
        assert span.parent.span_id == job_span.context.span_id

        # Status and duration attributes equal the run-row values, over
        # the same node-invocation boundary (Req 13.1 / 12.2).
        assert attributes["agent.status"] == status.value
        assert attributes["agent.latency_ms"] == latency_ms

        # LLM attributes present iff this invocation initiated a provider
        # call, each equal to the invocation-log value (Req 13.2).
        feature = _LLM_AGENT_FEATURE.get(name)
        call_occurred = feature is not None and modes[name] in _CALL_MODES
        if call_occurred:
            assert feature is not None
            logged = orchestrator.logged[feature]
            assert attributes["llm.prompt_version"] == logged.prompt_version
            assert attributes["llm.model"] == logged.model
            assert attributes["llm.input_hash"] == logged.input_hash
        else:
            present = _LLM_ATTRS.intersection(attributes.keys())
            assert not present, (
                f"{span.name} carries LLM attributes {sorted(present)} without a provider call"
            )
