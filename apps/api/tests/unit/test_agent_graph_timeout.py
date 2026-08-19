"""Unit tests for graph execution order and per-node timeout mechanics (task 7.3).

Complements ``tests/unit/test_agent_graph.py`` (task 7.1), which already
asserts the *static* structure — five nodes, the design's exact edge set,
and optional-checkpointer compilation. This module covers what those
assertions cannot: the **runtime** consequences of that structure and of
the per-node timeout, driven through real graph runs with the real five
agents (fakes only at the orchestrator/scorer seams):

* **Execution order follows the edges** (Requirements 1.1, 1.5) — the
  Synthesizer runs last as the sole join point, and both fan-out branch
  agents (Skill_Gap, Improvement) run only after Resume_Analysis, observed
  via the order of Agent_Run persistence callbacks.
* **A slow node degrades on the configured timeout and the graph
  completes** (Requirement 8.3) — one agent's ``run`` sleeps far longer
  than a tiny ``node_timeout_s``; the node's status flag records the
  ``timeout`` trigger, its Degraded_Output lands in state, and the run
  still produces a schema-valid AnalysisResult. Covered for both an LLM
  node (Resume_Analysis via a slow orchestrator) and a deterministic node
  (ATS via a slow scorer).
* **Post-timeout results are discarded** (Requirement 8.3) — the slow
  collaborator would return a marker output after its sleep;
  ``asyncio.wait_for`` cancels the in-flight ``run``, so the marker never
  reaches state and the collaborator's completion flag stays unset.

Graceful-degradation *combinations* across the four non-Synthesizer agents
are Property 4 (task 7.2, ``tests/property/test_graceful_degradation.py``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AnalysisResult,
    CandidateProfile,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

# Tiny configured timeout vs. a sleep that vastly outlasts it. Non-slow
# agents resolve their awaits immediately (no event-loop yield), so they
# cannot spuriously hit the window.
_NODE_TIMEOUT_S = 0.05
_SLEEP_S = 5.0

_ACTIVE_SCORER_VERSION = "2.0.0+active"
_STALE_SCORER_VERSION = "1.0.0+stale"

_LATE_MARKER = "__late-result-must-be-discarded__"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


class _RunRecorder:
    """Records persistence callbacks in invocation-completion order."""

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

    @property
    def order(self) -> list[str]:
        return [name for name, _, _ in self.calls]


class _Orchestrator:
    """AgentLLMOrchestrator fake; optionally slow for one feature.

    For the slow feature, ``prepare`` sleeps past the node timeout and —
    were it ever allowed to finish — would return a marker output and set
    ``finished``. Cancellation by ``asyncio.wait_for`` must prevent both.
    """

    def __init__(self, slow_feature: LLMFeature | None = None) -> None:
        self._slow_feature = slow_feature
        self.finished = False

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
        result: BaseModel = spec.result_schema()
        if spec.feature is self._slow_feature:
            await asyncio.sleep(_SLEEP_S)
            self.finished = True  # must never run: wait_for cancels first
            result = CandidateProfile(skills=[_LATE_MARKER])
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(is_fallback=False, result=result)
        return LLMOutcome(envelope=envelope, quota_remaining=10)

    async def execute(self, plan: ProviderCallPlan[str, Any]) -> LLMOutcome[Any]:
        raise AssertionError("this fake resolves every request in prepare")


class _Scorer:
    """ScorerAdapter fake; optionally slow.

    When slow, ``score`` sleeps past the node timeout and — were it ever
    allowed to finish — would return a marker score and set ``finished``.
    """

    active_scorer_version = _ACTIVE_SCORER_VERSION
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    def __init__(self, *, slow: bool = False) -> None:
        self._slow = slow
        self.finished = False

    async def score(self) -> ScoredMatch:
        if self._slow:
            await asyncio.sleep(_SLEEP_S)
            self.finished = True  # must never run: wait_for cancels first
            return ScoredMatch(
                score=99.9,
                breakdown={_LATE_MARKER: 1.0},
                scorer_version=_ACTIVE_SCORER_VERSION,
                semantic=True,
            )
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version=_ACTIVE_SCORER_VERSION,
            semantic=True,
        )


def _state() -> AgentState:
    """A valid initial state; the stale scorer version forces a fresh score."""
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


async def _run(orchestrator: _Orchestrator, scorer: _Scorer) -> tuple[dict[str, Any], _RunRecorder]:
    recorder = _RunRecorder()
    deps = AgentDeps(
        node_timeout_s=_NODE_TIMEOUT_S,
        persist_agent_run=recorder,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )
    compiled = compile_graph(build_agents(deps, orchestrator, scorer))
    result: dict[str, Any] = await compiled.ainvoke(_state())
    return result, recorder


# ---------------------------------------------------------------------------
# Execution order follows the declared edges (Requirements 1.1, 1.5)
# ---------------------------------------------------------------------------


class TestExecutionOrder:
    async def test_synthesizer_runs_last_and_branches_run_after_resume_analysis(self) -> None:
        """The runtime complement of the static edge-set assertions.

        Both branch agents (Skill_Gap, Improvement) complete only after
        Resume_Analysis, and the Synthesizer — the sole join point —
        completes last of all five (Requirement 1.5).
        """
        result, recorder = await _run(_Orchestrator(), _Scorer())
        order = recorder.order
        assert sorted(order) == sorted(
            ["resume_analysis", "ats", "skill_gap", "improvement", "synthesizer"]
        )
        assert order[-1] == "synthesizer"
        assert order.index("resume_analysis") < order.index("skill_gap")
        assert order.index("resume_analysis") < order.index("improvement")
        assert result["analysis_result"] is not None

    async def test_unimpeded_run_completes_every_agent_normally(self) -> None:
        """Baseline for the timeout tests: no agent degrades without cause."""
        result, _ = await _run(_Orchestrator(), _Scorer())
        flags = result["agent_status"]
        assert all(flag.status is AgentCompletion.COMPLETED for flag in flags.values())
        assert result["ats_output"].score == 55.0  # the fresh (non-degraded) score


# ---------------------------------------------------------------------------
# Per-node timeout mechanics (Requirement 8.3)
# ---------------------------------------------------------------------------


class TestNodeTimeout:
    async def test_slow_llm_node_degrades_with_timeout_trigger_and_graph_completes(self) -> None:
        """Resume_Analysis outlasting node_timeout_s degrades; the run finishes."""
        orchestrator = _Orchestrator(slow_feature=LLMFeature.AGENT_RESUME_ANALYSIS)
        result, recorder = await _run(orchestrator, _Scorer())

        flag = result["agent_status"]["resume_analysis"]
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "timeout"

        # The graph still completed end-to-end with a schema-valid result.
        analysis = result["analysis_result"]
        assert analysis is not None
        assert AnalysisResult.model_validate(analysis.model_dump()) == analysis
        # Every other agent completed normally.
        for name in ("ats", "skill_gap", "improvement", "synthesizer"):
            assert result["agent_status"][name].status is AgentCompletion.COMPLETED
        # One Agent_Run callback per node, status matching the outcome.
        assert sorted(recorder.order) == sorted(
            ["resume_analysis", "ats", "skill_gap", "improvement", "synthesizer"]
        )

    async def test_post_timeout_llm_result_is_discarded(self) -> None:
        """Cancellation abandons in-flight work; the late output never lands."""
        orchestrator = _Orchestrator(slow_feature=LLMFeature.AGENT_RESUME_ANALYSIS)
        result, _ = await _run(orchestrator, _Scorer())

        # wait_for cancelled the run mid-sleep: the slow path never finished…
        assert orchestrator.finished is False
        # …and state carries the Degraded_Output, not the late marker.
        profile = result["candidate_profile"]
        assert profile.degraded is True
        assert _LATE_MARKER not in profile.skills
        # The degraded profile is the specified snapshot-derived shape.
        assert profile.skills == ["python", "kubernetes"]

    async def test_slow_deterministic_node_degrades_and_late_score_is_discarded(self) -> None:
        """The same mechanics hold for a deterministic node (ATS via scorer)."""
        scorer = _Scorer(slow=True)
        result, _ = await _run(_Orchestrator(), scorer)

        flag = result["agent_status"]["ats"]
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "timeout"

        # The late score never landed; the degraded output carries the
        # persisted snapshot fields with confidence "low" (Requirement 4.4).
        assert scorer.finished is False
        ats = result["ats_output"]
        assert ats.degraded is True
        assert ats.score == 71.5
        assert ats.confidence == "low"
        assert _LATE_MARKER not in ats.breakdown

        # The graph still completed end-to-end.
        assert result["analysis_result"] is not None
        assert result["agent_status"]["synthesizer"].status is AgentCompletion.COMPLETED
