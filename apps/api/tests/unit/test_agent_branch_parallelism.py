"""Branch-overlap parallelism test (phase-4-agentic task 12.1).

Requirement 14.2 mandates an automated test demonstrating that the
Skill_Gap and Improvement branch nodes of the Agent_Graph execute
concurrently: a fixed delay is injected into each branch node and the
combined branch phase — wall-clock from the Resume_Analysis_Agent's
output return to the Synthesizer's invocation start — must complete in
less than the sum of the two injected delays.

Mechanics: a real compiled graph (real five agents, fakes only at the
orchestrator/scorer seams, mirroring ``test_agent_graph_timeout.py``)
runs with instrumented nodes. The two branch nodes each sleep a fixed
``_DELAY_S`` before executing; every instrumented node records its
``(start, end)`` monotonic window. Were the branches sequential, the
branch phase would be at least ``2 * _DELAY_S``; running concurrently
it is ~``_DELAY_S``, leaving a full ``_DELAY_S`` of headroom against CI
jitter. A wall-clock-free overlap assertion on the two branch windows
complements the timing bound.

**Validates: Requirements 14.2**
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

# The fixed delay injected into EACH branch node. Concurrent execution
# yields a branch phase of ~one delay; sequential execution would take at
# least two. The assertion bound is the sum (2 * _DELAY_S), so a full
# _DELAY_S of headroom absorbs CI scheduling jitter.
_DELAY_S = 0.5

# Generous per-node timeout: the injected delay must degrade nothing —
# this test exercises the normal (non-degraded) path.
_NODE_TIMEOUT_S = 30.0

_STALE_SCORER_VERSION = "1.0.0+stale"
_ACTIVE_SCORER_VERSION = "2.0.0+active"


# ---------------------------------------------------------------------------
# Fakes at the orchestrator/scorer seams (fast — they add no latency).
# ---------------------------------------------------------------------------


class _Orchestrator:
    """AgentLLMOrchestrator fake resolving every request instantly."""

    @property
    def model(self) -> str:
        return "test-model"

    async def prepare(
        self,
        spec: LLMFeatureSpec[Any, Any],
        *,
        user_id: str,
        feature_input: Any,
    ) -> LLMOutcome[Any] | ProviderCallPlan[Any, Any]:
        del user_id, feature_input
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=False, result=spec.result_schema()
        )
        return LLMOutcome(envelope=envelope, quota_remaining=10)

    async def execute(self, plan: ProviderCallPlan[Any, Any]) -> LLMOutcome[Any]:
        raise AssertionError("this fake resolves every request in prepare")


class _Scorer:
    """ScorerAdapter fake returning a fresh score instantly."""

    active_scorer_version = _ACTIVE_SCORER_VERSION
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    async def score(self) -> ScoredMatch:
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version=_ACTIVE_SCORER_VERSION,
            semantic=True,
        )


async def _noop_persist(
    agent_name: str,
    state: AgentState,
    output: BaseModel,
    status: AgentCompletion,
    reason: FailureDetail | None,
    latency_ms: int,
) -> None:
    del agent_name, state, output, status, reason, latency_ms


# ---------------------------------------------------------------------------
# Node instrumentation: fixed-delay injection + (start, end) windows.
# ---------------------------------------------------------------------------


class _InstrumentedNode:
    """Wraps one agent node, recording its execution window on the real
    monotonic clock and optionally injecting a fixed delay before the
    agent runs (the Requirement 14.2 delay injection).
    """

    def __init__(
        self,
        agent: BaseAgent[Any],
        windows: dict[str, tuple[float, float]],
        *,
        delay_s: float = 0.0,
    ) -> None:
        self._agent = agent
        self._windows = windows
        self._delay_s = delay_s
        self._name = agent.name

    async def __call__(self, state: AgentState) -> dict[str, object]:
        start = time.monotonic()
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        result = await self._agent(state)
        self._windows[self._name] = (start, time.monotonic())
        return result


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


class TestBranchOverlap:
    async def test_branch_phase_completes_in_less_than_the_sum_of_injected_delays(
        self,
    ) -> None:
        """Skill_Gap ∥ Improvement run concurrently (Requirement 14.2).

        With a fixed ``_DELAY_S`` injected into each branch node, the
        combined branch phase — Resume_Analysis output return to
        Synthesizer invocation start — must be shorter than the sum of
        the two injected delays. Sequential execution could never satisfy
        this; concurrent execution passes with ~``_DELAY_S`` to spare.
        """
        windows: dict[str, tuple[float, float]] = {}
        deps = AgentDeps(
            node_timeout_s=_NODE_TIMEOUT_S,
            persist_agent_run=_noop_persist,
            tracer=NoOpTracer(),
            clock=time,
        )
        agents = build_agents(deps, _Orchestrator(), _Scorer())
        nodes: dict[str, Any] = {
            name: _InstrumentedNode(agent, windows) for name, agent in agents.items()
        }
        nodes["skill_gap"] = _InstrumentedNode(agents["skill_gap"], windows, delay_s=_DELAY_S)
        nodes["improvement"] = _InstrumentedNode(agents["improvement"], windows, delay_s=_DELAY_S)

        compiled = compile_graph(nodes)
        result: dict[str, Any] = await compiled.ainvoke(_state())

        # The run itself is the normal path: nothing degraded, and the
        # graph produced a complete AnalysisResult.
        flags = result["agent_status"]
        assert all(flag.status is AgentCompletion.COMPLETED for flag in flags.values())
        assert result["analysis_result"] is not None

        # The Requirement 14.2 bound: the branch phase (Resume_Analysis
        # output return → Synthesizer invocation start) beats the sum of
        # the two injected delays.
        resume_analysis_end = windows["resume_analysis"][1]
        synthesizer_start = windows["synthesizer"][0]
        branch_phase_s = synthesizer_start - resume_analysis_end
        assert branch_phase_s > 0  # windows were recorded in graph order
        assert branch_phase_s < 2 * _DELAY_S, (
            f"branch phase took {branch_phase_s:.3f}s — not less than the "
            f"{2 * _DELAY_S:.1f}s sum of the injected delays, so the "
            "Skill_Gap and Improvement branches did not overlap"
        )

        # Wall-clock-free complement: the two branch windows genuinely
        # overlap (each starts before the other ends).
        sg_start, sg_end = windows["skill_gap"]
        imp_start, imp_end = windows["improvement"]
        assert sg_start < imp_end
        assert imp_start < sg_end
