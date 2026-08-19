"""Feature: phase-4-agentic — Property 4.

Property 4: Graceful degradation under any non-Synthesizer failure
combination.

    *For any* valid initial ``AgentState`` and any subset of the four
    non-Synthesizer agents each injected with any failure mode, the
    compiled Agent_Graph still completes end-to-end with a schema-valid
    ``AnalysisResult``; each injected agent contributes a schema-valid
    Degraded_Output marked degraded (matching its specified degraded
    shape); non-injected agents complete normally; each agent's status
    flag matches its outcome with a structured failure reason identifying
    the injected trigger; and exactly one Agent_Run persistence callback
    fires per node.

**Validates: Requirements 1.7, 3.4, 4.4, 5.5, 6.4, 8.1, 8.2, 8.4, 8.5,
8.6, 12.2**

Instrumentation: the **real five agents** are wired through the real
``build_agents`` / ``compile_graph`` composition layer and driven via
``ainvoke``. Failures are injected at the collaborator seams:

* the two LLM agents fail through a scripted ``AgentLLMOrchestrator``
  fake — provider exception, per-node timeout (``prepare`` outlasting
  ``node_timeout_s``), an orchestrator fallback envelope
  (→ ``LLMFallbackError``), a Daily_Quota gate rejection
  (→ ``quota_exhausted``), or an open Spend_Circuit_Breaker
  (→ ``breaker_open``);
* the ATS agent fails through a scripted ``ScorerAdapter`` fake —
  scoring exception or timeout (snapshots always carry a stale
  Scorer_Version so ``score()`` is genuinely invoked);
* the Skill_Gap agent — which holds no injectable collaborator — fails
  through a patched ``gap_rules`` entry point (exception) or a sleeping
  ``run`` wrapper that delegates to the real logic after the timeout
  window (timeout).

The both-LLM-agents-degraded case (breaker open / provider outage) is a
pinned example: the completed job assembles entirely from deterministic
outputs (Requirement 8.5).

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 4: Graceful degradation under any
# non-Synthesizer failure combination

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import Any
from unittest import mock

from hypothesis import example, given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    AnalysisResult,
    FailureDetail,
    MatchSnapshot,
)
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
# Failure-injection vocabulary.
# ---------------------------------------------------------------------------

# Modes injectable per agent. LLM agents fail through the orchestrator
# seam; ATS through the scorer seam; Skill_Gap through its gap_rules /
# run seam (it has no injectable collaborator).
_LLM_MODES = ("exception", "timeout", "fallback", "quota", "breaker")
_ATS_MODES = ("exception", "timeout")
_SKILL_GAP_MODES = ("exception", "timeout")

_INJECTABLE: dict[str, tuple[str, ...]] = {
    "resume_analysis": _LLM_MODES,
    "ats": _ATS_MODES,
    "skill_gap": _SKILL_GAP_MODES,
    "improvement": _LLM_MODES,
}

# The structured failure trigger each injected mode must produce on the
# agent's status flag (Requirement 8.4 / classify_failure mapping). An
# orchestrator fallback envelope surfaces as LLMFallbackError → "error".
_EXPECTED_TRIGGER: dict[str, str] = {
    "exception": "error",
    "timeout": "timeout",
    "fallback": "error",
    "quota": "quota_exhausted",
    "breaker": "breaker_open",
}

# Node timeout and the injected sleep. Non-failed agents complete without
# ever yielding to the event loop (their awaits resolve immediately), so
# they cannot spuriously hit this window; injected sleeps vastly exceed it.
_NODE_TIMEOUT_S = 0.05
_SLEEP_S = 5.0

_ACTIVE_SCORER_VERSION = "2.0.0+active"
_STALE_SCORER_VERSION = "1.0.0+stale"

_AGENT_NAMES = ("resume_analysis", "ats", "skill_gap", "improvement", "synthesizer")
_UPSTREAM = ("resume_analysis", "ats", "skill_gap", "improvement")


# ---------------------------------------------------------------------------
# Fakes: deps, scripted orchestrator, scripted scorer, run recorder.
# ---------------------------------------------------------------------------


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


class _RunRecorder:
    """Records every persist_agent_run callback (Requirement 12.2)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentCompletion, FailureDetail | None]] = []

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        del state, output, latency_ms
        self.calls.append((agent_name, status, reason))


class _ScriptedOrchestrator:
    """AgentLLMOrchestrator fake failing on command, per feature namespace."""

    def __init__(self, modes: dict[LLMFeature, str]) -> None:
        self._modes = modes

    @property
    def model(self) -> str:
        return "test-model"

    async def prepare(
        self,
        spec: LLMFeatureSpec[str, Any],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[Any] | ProviderCallPlan[str, Any]:
        del user_id, feature_input
        mode = self._modes.get(spec.feature, "ok")
        if mode == "exception":
            raise RuntimeError("injected orchestrator failure")
        if mode == "timeout":
            await asyncio.sleep(_SLEEP_S)
            raise AssertionError("timeout injection outlived the node timeout")
        if mode == "quota":
            raise DailyQuotaExceededError(
                limit=25, remaining=0, resets_at=datetime(2025, 1, 2, tzinfo=UTC)
            )
        if mode == "breaker":
            raise SpendLimitExceededError("spend limit reached")
        # "ok" and "fallback" both resolve without a provider call, mirroring
        # the orchestrator's cache-hit / pre-call-fallback shapes.
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=(mode == "fallback"),
            fallback_reason=FailureReason.PROVIDER_ERROR if mode == "fallback" else None,
            result=spec.result_schema(),
        )
        return LLMOutcome(envelope=envelope, quota_remaining=10)

    async def execute(self, plan: ProviderCallPlan[str, Any]) -> LLMOutcome[Any]:
        raise AssertionError("this fake resolves every request in prepare")


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
        if self._mode == "timeout":
            await asyncio.sleep(_SLEEP_S)
            raise AssertionError("timeout injection outlived the node timeout")
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version=_ACTIVE_SCORER_VERSION,
            semantic=True,
        )


# Skill_Gap timeout injection: a sleeping wrapper that then delegates to
# the real run — asyncio.wait_for cancels during the sleep, so the real
# logic is never bypassed on any path that completes.
_ORIGINAL_SKILL_GAP_RUN = SkillGapAgent.run


async def _slow_skill_gap_run(self: SkillGapAgent, state: AgentState) -> Any:
    await asyncio.sleep(_SLEEP_S)
    return await _ORIGINAL_SKILL_GAP_RUN(self, state)


def _raise_gap_rules(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("injected gap-rules failure")


# ---------------------------------------------------------------------------
# Generators.
# ---------------------------------------------------------------------------

_skill = st.one_of(
    st.sampled_from(["python", "Python", "aws", "sql", "react", "kubernetes", "go"]),
    st.text(alphabet="abcABC+# ", min_size=1, max_size=8),
)

# Snapshots always carry a stale Scorer_Version so the ATS agent invokes
# score() — the seam its failure injection lives behind.
_snapshots = st.builds(
    MatchSnapshot,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.dictionaries(
        st.sampled_from(["similarity", "keyword"]),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        max_size=2,
    ),
    scorer_version=st.just(_STALE_SCORER_VERSION),
    matched_skills=st.lists(_skill, max_size=6),
    missing_skills=st.lists(_skill, max_size=6),
    suggestions=st.lists(st.text(max_size=20), max_size=3),
)


@st.composite
def _initial_states(draw: st.DrawFn) -> AgentState:
    """A valid initial AgentState as the worker builds it (Requirement 1.7).

    Non-empty redacted text and a present MatchSnapshot, so no agent
    degrades for any reason other than the injected failure.
    """
    return AgentState(
        job_id=draw(st.uuids(version=4).map(str)),
        match_id=draw(st.uuids(version=4).map(str)),
        user_id=draw(st.uuids(version=4).map(str)),
        redacted_resume_text=draw(
            st.text(min_size=1, max_size=100).filter(lambda s: bool(s.strip()))
        ),
        job_description_skills=draw(st.lists(_skill, max_size=10)),
        match_snapshot=draw(_snapshots),
    )


@st.composite
def _failure_plans(draw: st.DrawFn) -> dict[str, str]:
    """Any subset of the four non-Synthesizer agents, each with a mode."""
    plan: dict[str, str] = {}
    for agent, modes in _INJECTABLE.items():
        if draw(st.booleans()):
            plan[agent] = draw(st.sampled_from(modes))
    return plan


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
# Driving one graph execution with the injected failures.
# ---------------------------------------------------------------------------


async def _run_graph(
    state: AgentState, failures: dict[str, str]
) -> tuple[dict[str, Any], _RunRecorder]:
    recorder = _RunRecorder()
    deps = AgentDeps(
        node_timeout_s=_NODE_TIMEOUT_S,
        persist_agent_run=recorder,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )
    orchestrator = _ScriptedOrchestrator(
        {
            LLMFeature.AGENT_RESUME_ANALYSIS: failures.get("resume_analysis", "ok"),
            LLMFeature.AGENT_IMPROVEMENT: failures.get("improvement", "ok"),
        }
    )
    scorer = _ScriptedScorer(failures.get("ats", "ok"))
    agents = build_agents(deps, orchestrator, scorer)
    compiled = compile_graph(agents)
    result: dict[str, Any] = await compiled.ainvoke(state)
    return result, recorder


def _run_sync(state: AgentState, failures: dict[str, str]) -> tuple[dict[str, Any], _RunRecorder]:
    with ExitStack() as stack:
        skill_gap_mode = failures.get("skill_gap")
        if skill_gap_mode == "exception":
            stack.enter_context(
                mock.patch(
                    "matchlayer_api.ml.agents.skill_gap_agent.build_skill_gap_entries",
                    _raise_gap_rules,
                )
            )
        elif skill_gap_mode == "timeout":
            stack.enter_context(mock.patch.object(SkillGapAgent, "run", _slow_skill_gap_run))
        with asyncio.Runner() as runner:
            return runner.run(_run_graph(state, failures))


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 4: Graceful degradation under any
# non-Synthesizer failure combination
@settings(max_examples=100, deadline=None)
@example(state=_base_state(), failures={})  # no injection: all complete normally
@example(  # both LLM agents degraded (breaker open): deterministic assembly (Req 8.5)
    state=_base_state(),
    failures={"resume_analysis": "breaker", "improvement": "breaker"},
)
@example(  # all four non-Synthesizer agents fail at once (Req 1.7 worst case)
    state=_base_state(),
    failures={
        "resume_analysis": "timeout",
        "ats": "exception",
        "skill_gap": "exception",
        "improvement": "fallback",
    },
)
@given(state=_initial_states(), failures=_failure_plans())
def test_graph_completes_with_degraded_outputs_under_any_failure_combination(
    state: AgentState, failures: dict[str, str]
) -> None:
    """Any non-Synthesizer failure subset still yields a completed run.

    Requirement 1.7: a valid invocation produces a schema-valid
    AnalysisResult even when non-Synthesizer nodes contributed only
    Degraded_Outputs. Requirements 3.4/4.4/5.5/6.4: each agent's degraded
    output matches its specified shape. Requirements 8.1/8.2/8.4: every
    failure degrades in place with a structured reason and the shared
    output schema. Requirement 12.2: exactly one Agent_Run callback per
    node invocation.
    """
    result, recorder = _run_sync(state, failures)

    # --- the run completed end-to-end with a schema-valid AnalysisResult ---
    analysis = result["analysis_result"]
    assert analysis is not None, "the graph must reach the Synthesizer (Requirement 1.7)"
    revalidated = AnalysisResult.model_validate(analysis.model_dump())
    assert revalidated == analysis

    flags: dict[str, AgentStatusFlag] = result["agent_status"]
    assert set(flags) == set(_AGENT_NAMES)

    snapshot = state.match_snapshot
    assert snapshot is not None  # generated states always carry one

    outputs: dict[str, Any] = {
        "resume_analysis": result["candidate_profile"],
        "ats": result["ats_output"],
        "skill_gap": result["skill_gap_report"],
        "improvement": result["improvement_report"],
    }

    for agent in _UPSTREAM:
        output = outputs[agent]
        flag = flags[agent]
        assert output is not None, f"{agent} wrote no output (Requirement 8.1)"
        # Degraded and normal outputs share one schema (Requirement 8.2).
        assert type(output).model_validate(output.model_dump()) == output
        if agent in failures:
            # --- injected agents: Degraded_Output marked degraded ---------
            assert output.degraded is True, f"{agent} output not marked degraded"
            assert flag.status is AgentCompletion.DEGRADED
            assert flag.failure_reason is not None, f"{agent} degraded without a reason (Req 8.4)"
            expected = _EXPECTED_TRIGGER[failures[agent]]
            assert flag.failure_reason.trigger == expected, (
                f"{agent} injected mode {failures[agent]!r} produced trigger "
                f"{flag.failure_reason.trigger!r}, expected {expected!r}"
            )
        else:
            # --- non-injected agents: normal completion --------------------
            assert output.degraded is False, f"{agent} degraded without an injected failure"
            assert flag.status is AgentCompletion.COMPLETED
            assert flag.failure_reason is None

    # --- degraded shapes match the per-agent specifications ----------------
    if "resume_analysis" in failures:
        profile = outputs["resume_analysis"]
        assert profile.sections == [] and profile.experiences == [] and profile.gaps == []
    if "ats" in failures:
        ats = outputs["ats"]
        assert ats.confidence == "low"  # Requirement 4.4
        assert ats.score == snapshot.score
        assert ats.scorer_version == snapshot.scorer_version
    if "skill_gap" in failures:
        report = outputs["skill_gap"]
        assert [gap.skill for gap in report.gaps] == snapshot.missing_skills  # persisted order
        assert [gap.rank for gap in report.gaps] == list(range(1, len(report.gaps) + 1))
        assert all(gap.classification == "missing" for gap in report.gaps)
    if "improvement" in failures:
        assert outputs["improvement"].rewrites == []  # Requirement 6.4: nothing to quote

    # --- the Synthesizer completed and its traces mirror the flags ---------
    assert flags["synthesizer"].status is AgentCompletion.COMPLETED
    assert {trace.agent_name: trace.status for trace in analysis.agent_traces} == {
        agent: flags[agent].status for agent in _UPSTREAM
    }

    # --- exactly one Agent_Run persistence callback per node (Req 12.2) ----
    persisted_names = sorted(name for name, _, _ in recorder.calls)
    assert persisted_names == sorted(_AGENT_NAMES)
    for name, status, reason in recorder.calls:
        assert status is flags[name].status
        assert reason == flags[name].failure_reason
