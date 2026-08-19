"""Agent_Graph wiring and the best-effort checkpointer (phase-4-agentic).

Design reference: "4. Graph wiring" in the phase-4-agentic design.
Requirements covered: 1.1, 1.5, 2.1, 2.2, 2.3, 2.4, 2.6.

This module is the composition layer of the agent package: it constructs
the five agents with their injected dependencies (:func:`build_agents`),
declares the graph topology (:func:`build_graph`), and compiles it with an
optional checkpointer (:func:`compile_graph`). Agent instances are
LangGraph nodes *directly*: :meth:`BaseAgent.__call__` has exactly the
``(AgentState) -> dict`` node signature, so no adapter layer exists
(Requirement 1.1) — and no agent logic executes outside this graph in the
production analyze path.

Topology (Requirement 1.5, two levels of parallelism)::

    START → resume_analysis          START → ats
    resume_analysis → skill_gap      resume_analysis → improvement
    [ats, skill_gap, improvement] → synthesizer → END

* **ATS ∥ Resume Analysis** — the ATS_Agent's inputs come entirely from
  the persisted Match_Result, so it starts immediately at graph entry.
* **Skill Gap ∥ Improvement** — both consume the Candidate_Profile and
  neither consumes the other's output, so they fan out from Resume
  Analysis. The Synthesizer is the sole join point and terminal node.

Parallel-merge safety: each agent writes only its own output field
(one-writer-per-field, Requirement 1.2); the one field every node writes —
``agent_status`` — carries the
:func:`~matchlayer_api.ml.agents.state.merge_agent_status` reducer.

Checkpointing (Requirement 2, design D4): the production path compiles
with a :class:`BestEffortSaver` wrapping the ``AsyncPostgresSaver`` whose
schema an Alembic migration created (Requirement 2.5 — no runtime DDL
here). Checkpoints are keyed by ``thread_id = job_id`` — the graph is
invoked with ``config={"configurable": {"thread_id": job_id}}`` — so every
checkpoint row is attributable to exactly one Agent_Job (Requirement 2.2)
and reads for inspection use the same key (Requirement 2.6). Serialized
state snapshots contain no raw resume text because
:class:`~matchlayer_api.ml.agents.state.AgentState` structurally cannot
carry it (Requirements 1.3, 2.3).

Best-effort semantics (Requirement 2.4, design D4): a checkpoint *write*
failure must never affect a run's outcome — the run continues in memory
and the terminal Job_Status is determined solely by graph execution. The
:class:`BestEffortSaver` absorbs write failures (``aput``,
``aput_writes``) with exactly one structured warning each, carrying the
job id and the failure reason (exception class name only — never PII,
never provider/database payloads, per `security.md`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
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
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScorerAdapter
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.improvement_agent import ImprovementAgent
from matchlayer_api.ml.agents.llm_agent import AgentLLMOrchestrator
from matchlayer_api.ml.agents.resume_analysis_agent import ResumeAnalysisAgent
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import AgentState
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent

__all__ = [
    "AgentGraph",
    "BestEffortSaver",
    "CompiledAgentGraph",
    "build_agents",
    "build_graph",
    "compile_graph",
]

_log = structlog.get_logger(__name__)

# The structured event name for every absorbed checkpoint write failure
# (Requirement 2.4's "one structured warning" per failed write).
CHECKPOINT_WRITE_FAILED_EVENT = "agent_checkpoint_write_failed"

# PEP 695 aliases pinning the four StateGraph type parameters: state,
# runtime context (none), input, and output are all AgentState.
type AgentGraph = StateGraph[AgentState, None, AgentState, AgentState]
type CompiledAgentGraph = CompiledStateGraph[AgentState, None, AgentState, AgentState]


def build_agents(
    deps: AgentDeps,
    orchestrator: AgentLLMOrchestrator,
    scorer: ScorerAdapter,
) -> dict[str, BaseAgent[Any]]:
    """Construct the five agents with their dependencies, keyed by name.

    The LLM agents receive the job-scoped orchestrator handle (design D1);
    the ATS agent receives the job-scoped Phase 2 scorer adapter; the
    purely state-driven agents receive only :class:`AgentDeps`. Keys are
    the agents' ``name`` ClassVars — the node names of
    :func:`build_graph`.
    """
    # `Any` abstracts over each agent's concrete Pydantic output model
    # (CandidateProfile, ATSOutput, ...): the graph layer treats the five
    # heterogeneously-typed agents uniformly, per the design sketch.
    agents: tuple[BaseAgent[Any], ...] = (
        ResumeAnalysisAgent(deps, orchestrator),
        ATSAgent(deps, scorer),
        SkillGapAgent(deps),
        ImprovementAgent(deps, orchestrator),
        SynthesizerAgent(deps),
    )
    return {agent.name: agent for agent in agents}


def build_graph(agents: Mapping[str, BaseAgent[Any]]) -> AgentGraph:
    """Declare the StateGraph over :class:`AgentState` (Requirements 1.1, 1.5).

    Each agent instance registers directly as its node (the ``__call__``
    template method *is* the LangGraph node signature). Edges encode the
    dependency analysis from the design: ATS ∥ Resume-Analysis at graph
    start, Skill-Gap ∥ Improvement fanning out from Resume Analysis, and
    the Synthesizer as the sole join point and terminal node.
    """
    graph: AgentGraph = StateGraph(AgentState)
    for name, agent in agents.items():
        graph.add_node(name, agent)  # instances are the nodes
    graph.add_edge(START, ResumeAnalysisAgent.name)
    graph.add_edge(START, ATSAgent.name)
    graph.add_edge(ResumeAnalysisAgent.name, SkillGapAgent.name)
    graph.add_edge(ResumeAnalysisAgent.name, ImprovementAgent.name)
    graph.add_edge(
        [ATSAgent.name, SkillGapAgent.name, ImprovementAgent.name],
        SynthesizerAgent.name,
    )
    graph.add_edge(SynthesizerAgent.name, END)
    return graph


def compile_graph(
    agents: Mapping[str, BaseAgent[Any]],
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> CompiledAgentGraph:
    """Compile the graph, optionally with a checkpointer (Requirement 2.1).

    The Agent_Worker's production path passes a :class:`BestEffortSaver`
    wrapping the Alembic-provisioned ``AsyncPostgresSaver``; tests may
    pass ``None`` (no checkpointing) or any
    :class:`~langgraph.checkpoint.base.BaseCheckpointSaver`.
    """
    return build_graph(agents).compile(checkpointer=checkpointer)


class BestEffortSaver(BaseCheckpointSaver[str]):
    """Best-effort wrapper over a Postgres checkpointer (Requirement 2.4, D4).

    Read methods (``aget_tuple``, ``alist``, ``adelete_thread``,
    ``get_next_version``) delegate to the wrapped saver unchanged, so
    inspection by ``thread_id = job_id`` behaves exactly like the wrapped
    saver's (Requirements 2.2, 2.6). Write methods (``aput``,
    ``aput_writes``) absorb any failure: the run continues in memory, the
    terminal Job_Status is determined solely by graph execution, and each
    failed write produces exactly one structured warning carrying the job
    id (the thread id) and the failure reason as the exception class name
    — never PII, statement text, or connection details (`security.md`).

    The wrapped saver is typed as ``BaseCheckpointSaver[str]`` — in
    production an ``AsyncPostgresSaver`` (whose schema an Alembic
    migration created, Requirement 2.5); tests inject fakes. Only the
    async surface is wrapped: the Agent_Worker invokes the graph
    exclusively via ``ainvoke``, so the sync checkpoint methods are never
    reached in the production analyze path.
    """

    def __init__(self, inner: BaseCheckpointSaver[str]) -> None:
        # Share the wrapped saver's serializer so LangGraph serializes
        # channel values exactly as the inner saver expects to store them.
        super().__init__(serde=inner.serde)
        self._inner = inner

    # ---- reads: delegate unchanged (Requirements 2.2, 2.6) ----------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Fetch a checkpoint tuple for the job's thread from the wrapped saver."""
        return await self._inner.aget_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List the job's persisted snapshots from the wrapped saver (Req 2.6)."""
        async for item in self._inner.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        """Delegate thread deletion (maintenance surface; not a run write)."""
        await self._inner.adelete_thread(thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        """Delegate channel versioning so versions match the inner saver's scheme."""
        return self._inner.get_next_version(current, channel)

    # ---- writes: absorb failures with one structured warning each (2.4) ---

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist a snapshot; on failure warn once and continue in memory.

        The fallback returns the input config unchanged so LangGraph's
        run loop proceeds exactly as it would have — a checkpoint write
        failure alone never alters graph execution or Job_Status
        (Requirement 2.4).
        """
        try:
            return await self._inner.aput(config, checkpoint, metadata, new_versions)
        except Exception as exc:  # absorb: observability never fails a run
            self._warn_write_failure("aput", config, exc)
            return config

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist intermediate writes; on failure warn once and continue."""
        try:
            await self._inner.aput_writes(config, writes, task_id, task_path)
        except Exception as exc:  # absorb: observability never fails a run
            self._warn_write_failure("aput_writes", config, exc)

    # ---- the one structured warning per failed write -----------------------

    @staticmethod
    def _warn_write_failure(method: str, config: RunnableConfig, exc: Exception) -> None:
        """Log Requirement 2.4's structured warning: job id + reason, no PII.

        ``reason`` is the exception *class name* only — never ``str(exc)``,
        which could carry SQL statement text, connection strings, or
        serialized state (`security.md`).
        """
        configurable = config.get("configurable") or {}
        thread_id: object = configurable.get("thread_id")
        _log.warning(
            CHECKPOINT_WRITE_FAILED_EVENT,
            job_id=str(thread_id) if thread_id is not None else None,
            reason=type(exc).__name__,
            method=method,
        )
