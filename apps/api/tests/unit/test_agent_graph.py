"""Unit tests for graph construction and the best-effort checkpointer (task 7.1).

Covers ``ml/agents/graph.py`` with fakes only — no live Postgres, no LLM:

* ``build_agents`` — five agents, keyed by their ``name`` ClassVars, each
  the expected concrete class with its injected collaborators
  (Requirement 1.1).
* ``build_graph`` / ``compile_graph`` — the compiled graph carries exactly
  the five agent nodes and the design's edge set: START→resume_analysis,
  START→ats, resume_analysis→skill_gap, resume_analysis→improvement,
  [ats, skill_gap, improvement]→synthesizer→END (Requirements 1.1, 1.5).
* ``compile_graph`` accepts an optional checkpointer (Requirement 2.1).
* Parallel ``agent_status`` merge — a full stub-agent execution proves the
  ``merge_agent_status`` reducer lets parallel supersteps each write their
  own status key (Requirement 1.2 / design "Research notes").
* ``BestEffortSaver`` — reads delegate to the wrapped saver; ``aput`` /
  ``aput_writes`` absorb failures with exactly one structured warning each
  carrying the job id (thread id) and the exception class name, and the
  run-facing return value keeps execution going (Requirements 2.2, 2.4,
  2.6).

Deeper graph-structure and timeout mechanics live in task 7.3; graceful
degradation combinations live in task 7.2 (Property 4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.graph import (
    CHECKPOINT_WRITE_FAILED_EVENT,
    BestEffortSaver,
    build_agents,
    build_graph,
    compile_graph,
)
from matchlayer_api.ml.agents.improvement_agent import ImprovementAgent
from matchlayer_api.ml.agents.resume_analysis_agent import ResumeAnalysisAgent
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AnalysisResult,
    ATSOutput,
    CandidateProfile,
    FailureDetail,
    ImprovementReport,
    SkillGapReport,
)
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent

_AGENT_NAMES = {"resume_analysis", "ats", "skill_gap", "improvement", "synthesizer"}

_EXPECTED_EDGES = {
    ("__start__", "resume_analysis"),
    ("__start__", "ats"),
    ("resume_analysis", "skill_gap"),
    ("resume_analysis", "improvement"),
    ("ats", "synthesizer"),
    ("skill_gap", "synthesizer"),
    ("improvement", "synthesizer"),
    ("synthesizer", "__end__"),
}

_JOB_ID = "job-0193b6d1-0000-7000-8000-000000000001"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


async def _persist_noop(
    agent_name: str,
    state: AgentState,
    output: BaseModel,
    status: AgentCompletion,
    reason: FailureDetail | None,
    latency_ms: int,
) -> None:
    return None


def _deps() -> AgentDeps:
    return AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=_persist_noop,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )


class _FakeOrchestrator:
    """AgentLLMOrchestrator fake; construction-only tests never invoke it."""

    @property
    def model(self) -> str:
        return "test-model"

    async def prepare(self, spec: Any, *, user_id: str, feature_input: str) -> Any:
        raise AssertionError("construction tests must not reach the orchestrator")

    async def execute(self, plan: Any) -> Any:
        raise AssertionError("construction tests must not reach the orchestrator")


class _FakeScorer:
    """ScorerAdapter fake; construction-only tests never invoke it."""

    active_scorer_version = "2.0.0+test"
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    async def score(self) -> ScoredMatch:
        raise AssertionError("construction tests must not reach the scorer")


def _config(thread_id: str | None = _JOB_ID) -> RunnableConfig:
    configurable: dict[str, Any] = {}
    if thread_id is not None:
        configurable["thread_id"] = thread_id
    return {"configurable": configurable}


def _checkpoint() -> Checkpoint:
    return Checkpoint(
        v=4,
        id="ckpt-1",
        ts="2025-01-01T00:00:00+00:00",
        channel_values={},
        channel_versions={},
        versions_seen={},
    )


_METADATA: CheckpointMetadata = {"source": "loop", "step": 0}
_VERSIONS: ChannelVersions = {}


class _FakeSaver(BaseCheckpointSaver[str]):
    """Recording inner saver; each write surface can be told to fail."""

    def __init__(self, *, fail_writes: bool = False) -> None:
        super().__init__()
        self.fail_writes = fail_writes
        self.aput_calls: list[RunnableConfig] = []
        self.aput_writes_calls: list[tuple[str, str]] = []
        self.aget_tuple_calls: list[RunnableConfig] = []
        self.adelete_thread_calls: list[str] = []

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self.aget_tuple_calls.append(config)
        return None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is not None:
            yield CheckpointTuple(
                config=config,
                checkpoint=_checkpoint(),
                metadata=_METADATA,
                parent_config=None,
                pending_writes=None,
            )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        if self.fail_writes:
            raise ConnectionError("postgres unavailable")
        self.aput_calls.append(config)
        return {"configurable": {"thread_id": _JOB_ID, "checkpoint_id": "ckpt-1"}}

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        if self.fail_writes:
            raise ConnectionError("postgres unavailable")
        self.aput_writes_calls.append((task_id, task_path))

    async def adelete_thread(self, thread_id: str) -> None:
        self.adelete_thread_calls.append(thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        return "fake-next-version"


# ---------------------------------------------------------------------------
# build_agents
# ---------------------------------------------------------------------------


class TestBuildAgents:
    def test_returns_five_agents_keyed_by_name(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        assert set(agents) == _AGENT_NAMES
        assert all(agent.name == name for name, agent in agents.items())

    def test_each_key_maps_to_the_expected_concrete_class(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        assert isinstance(agents["resume_analysis"], ResumeAnalysisAgent)
        assert isinstance(agents["ats"], ATSAgent)
        assert isinstance(agents["skill_gap"], SkillGapAgent)
        assert isinstance(agents["improvement"], ImprovementAgent)
        assert isinstance(agents["synthesizer"], SynthesizerAgent)


# ---------------------------------------------------------------------------
# build_graph / compile_graph structure
# ---------------------------------------------------------------------------


class TestGraphStructure:
    def test_build_graph_registers_the_agent_instances_as_nodes(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        graph = build_graph(agents)
        assert set(graph.nodes) == _AGENT_NAMES

    def test_compiled_graph_has_exactly_the_five_agent_nodes(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        compiled = compile_graph(agents)
        drawable = compiled.get_graph()
        node_names = set(drawable.nodes) - {"__start__", "__end__"}
        assert node_names == _AGENT_NAMES

    def test_compiled_graph_edges_match_the_design_topology(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        compiled = compile_graph(agents)
        edges = {(edge.source, edge.target) for edge in compiled.get_graph().edges}
        assert edges == _EXPECTED_EDGES

    def test_compile_graph_without_checkpointer(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        compiled = compile_graph(agents)
        assert compiled.checkpointer is None

    def test_compile_graph_with_checkpointer(self) -> None:
        agents = build_agents(_deps(), _FakeOrchestrator(), _FakeScorer())
        saver = BestEffortSaver(_FakeSaver())
        compiled = compile_graph(agents, saver)
        assert compiled.checkpointer is saver


# ---------------------------------------------------------------------------
# Parallel agent_status merge (the merge_agent_status reducer)
# ---------------------------------------------------------------------------


def _min_ats() -> ATSOutput:
    return ATSOutput(score=0.0, confidence="low", scorer_version="test")


class _StubResume(DeterministicAgent[CandidateProfile]):
    name = "resume_analysis"
    output_field = "candidate_profile"

    async def run(self, state: AgentState) -> CandidateProfile:
        return CandidateProfile()

    def build_degraded(self, state: AgentState) -> CandidateProfile:
        return CandidateProfile(degraded=True)

    def build_minimal(self) -> CandidateProfile:
        return CandidateProfile(degraded=True)


class _StubATS(DeterministicAgent[ATSOutput]):
    name = "ats"
    output_field = "ats_output"

    async def run(self, state: AgentState) -> ATSOutput:
        return _min_ats()

    def build_degraded(self, state: AgentState) -> ATSOutput:
        return _min_ats()

    def build_minimal(self) -> ATSOutput:
        return _min_ats()


class _StubSkillGap(DeterministicAgent[SkillGapReport]):
    name = "skill_gap"
    output_field = "skill_gap_report"

    async def run(self, state: AgentState) -> SkillGapReport:
        return SkillGapReport()

    def build_degraded(self, state: AgentState) -> SkillGapReport:
        return SkillGapReport(degraded=True)

    def build_minimal(self) -> SkillGapReport:
        return SkillGapReport(degraded=True)


class _StubImprovement(DeterministicAgent[ImprovementReport]):
    name = "improvement"
    output_field = "improvement_report"

    async def run(self, state: AgentState) -> ImprovementReport:
        return ImprovementReport()

    def build_degraded(self, state: AgentState) -> ImprovementReport:
        return ImprovementReport(degraded=True)

    def build_minimal(self) -> ImprovementReport:
        return ImprovementReport(degraded=True)


class _StubSynthesizer(DeterministicAgent[AnalysisResult]):
    name = "synthesizer"
    output_field = "analysis_result"

    async def run(self, state: AgentState) -> AnalysisResult:
        return AnalysisResult(
            ats=state.ats_output or _min_ats(),
            skill_gaps=state.skill_gap_report or SkillGapReport(),
            improvements=state.improvement_report or ImprovementReport(),
            profile=state.candidate_profile or CandidateProfile(),
        )

    def build_degraded(self, state: AgentState) -> AnalysisResult:
        raise NotImplementedError

    def build_minimal(self) -> AnalysisResult:
        raise NotImplementedError


class TestParallelStatusMerge:
    async def test_parallel_supersteps_merge_agent_status_for_all_five_agents(self) -> None:
        """ATS ∥ Resume-Analysis and Skill-Gap ∥ Improvement each write
        ``agent_status`` within one superstep; the reducer must merge them
        instead of raising LangGraph's concurrent-update error.
        """
        deps = _deps()
        stubs: dict[str, BaseAgent[Any]] = {
            agent.name: agent
            for agent in (
                _StubResume(deps),
                _StubATS(deps),
                _StubSkillGap(deps),
                _StubImprovement(deps),
                _StubSynthesizer(deps),
            )
        }
        compiled = compile_graph(stubs)
        state = AgentState(job_id=_JOB_ID, match_id="match-1", user_id="user-1")
        result = await compiled.ainvoke(state)
        assert set(result["agent_status"]) == _AGENT_NAMES
        assert result["analysis_result"] is not None


# ---------------------------------------------------------------------------
# BestEffortSaver
# ---------------------------------------------------------------------------


class TestBestEffortSaver:
    async def test_aput_delegates_and_returns_the_inner_config_on_success(self) -> None:
        inner = _FakeSaver()
        saver = BestEffortSaver(inner)
        returned = await saver.aput(_config(), _checkpoint(), _METADATA, _VERSIONS)
        assert inner.aput_calls == [_config()]
        assert returned["configurable"]["checkpoint_id"] == "ckpt-1"

    async def test_aput_failure_is_absorbed_with_one_structured_warning(self) -> None:
        saver = BestEffortSaver(_FakeSaver(fail_writes=True))
        config = _config()
        with structlog.testing.capture_logs() as captured:
            returned = await saver.aput(config, _checkpoint(), _METADATA, _VERSIONS)
        # The run continues: the input config passes through unchanged.
        assert returned is config
        warnings = [e for e in captured if e["event"] == CHECKPOINT_WRITE_FAILED_EVENT]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["job_id"] == _JOB_ID
        assert warnings[0]["reason"] == "ConnectionError"
        assert warnings[0]["method"] == "aput"

    async def test_aput_writes_failure_is_absorbed_with_one_structured_warning(self) -> None:
        saver = BestEffortSaver(_FakeSaver(fail_writes=True))
        with structlog.testing.capture_logs() as captured:
            await saver.aput_writes(_config(), [("channel", "value")], "task-1")
        warnings = [e for e in captured if e["event"] == CHECKPOINT_WRITE_FAILED_EVENT]
        assert len(warnings) == 1
        assert warnings[0]["job_id"] == _JOB_ID
        assert warnings[0]["reason"] == "ConnectionError"
        assert warnings[0]["method"] == "aput_writes"

    async def test_write_failure_warning_never_carries_the_exception_message(self) -> None:
        """Reason is the exception class name only — never str(exc), which
        could carry SQL text, connection strings, or serialized state.
        """
        saver = BestEffortSaver(_FakeSaver(fail_writes=True))
        with structlog.testing.capture_logs() as captured:
            await saver.aput(_config(), _checkpoint(), _METADATA, _VERSIONS)
        (warning,) = [e for e in captured if e["event"] == CHECKPOINT_WRITE_FAILED_EVENT]
        assert "postgres unavailable" not in str(warning)

    async def test_aput_writes_success_delegates(self) -> None:
        inner = _FakeSaver()
        saver = BestEffortSaver(inner)
        await saver.aput_writes(_config(), [("channel", "value")], "task-1", "path")
        assert inner.aput_writes_calls == [("task-1", "path")]

    async def test_reads_delegate_to_the_wrapped_saver(self) -> None:
        inner = _FakeSaver()
        saver = BestEffortSaver(inner)
        assert await saver.aget_tuple(_config()) is None
        assert inner.aget_tuple_calls == [_config()]
        listed = [item async for item in saver.alist(_config())]
        assert len(listed) == 1
        await saver.adelete_thread(_JOB_ID)
        assert inner.adelete_thread_calls == [_JOB_ID]
        assert saver.get_next_version(None, None) == "fake-next-version"

    def test_serde_is_shared_with_the_wrapped_saver(self) -> None:
        inner = _FakeSaver()
        saver = BestEffortSaver(inner)
        assert saver.serde is inner.serde

    async def test_missing_thread_id_logs_null_job_id_instead_of_raising(self) -> None:
        saver = BestEffortSaver(_FakeSaver(fail_writes=True))
        with structlog.testing.capture_logs() as captured:
            await saver.aput(_config(thread_id=None), _checkpoint(), _METADATA, _VERSIONS)
        (warning,) = [e for e in captured if e["event"] == CHECKPOINT_WRITE_FAILED_EVENT]
        assert warning["job_id"] is None
