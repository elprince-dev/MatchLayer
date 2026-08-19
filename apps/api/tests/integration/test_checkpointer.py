"""Checkpointer integration tests (phase-4-agentic task 17.1).

Validates Requirements 2.1, 2.2, 2.4, 2.5, and 2.6 against the **real**
``AsyncPostgresSaver`` wrapped in :class:`BestEffortSaver`, driving the
real compiled Agent_Graph (the production five agents composed by
``build_agents`` / ``compile_graph``) with fakes only at the two seams
the design injects — the LLM orchestrator and the Phase 2 scorer adapter.

Coverage map:

* **2.1 / 2.5** — the docker-gated tests run against the checkpointer
  schema exactly as migration ``0006_checkpointer_schema`` created it
  (``PostgresSaver.setup()`` invoked from Alembic): no test here calls
  ``setup()`` and no runtime DDL happens, so a passing round-trip proves
  the Alembic-provisioned schema is the one the runtime writes to. The
  connection string derives from ``Settings.database_url`` — never a
  hard-coded value. (The migration's own apply/rollback cycle lives in
  ``test_migration_0006_checkpointer.py``.)
* **2.2 / 2.6** — snapshots are written per graph transition keyed by
  ``thread_id = job_id`` and are readable back by that identifier, for
  **completed** runs (first test) and **in-flight** runs (second test,
  which reads through a separate connection while a node is blocked
  mid-graph — the "inspect a running job from another process" shape).
* **2.4** — a saver whose writes fail degrades to one structured warning
  per failed write while the graph run completes with an outcome
  identical to an uncheckpointed run (Job_Status is determined solely by
  graph execution). This test needs no docker: the failing inner saver
  is a fake, but the graph and the ``BestEffortSaver`` wrapper are real.
  (The saver-method-level unit tests live in ``test_agent_graph.py``;
  this one proves the property across a full compiled-graph execution.)

Gating follows the repo's docker-gating pattern: the two Postgres-backed
tests skip cleanly when the docker-compose stack is down; the failing-
saver test always runs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import (
    CHECKPOINT_WRITE_FAILED_EVENT,
    BestEffortSaver,
    build_agents,
    compile_graph,
)
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AnalysisResult,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import LLMResultEnvelope

from .conftest import postgres_available

requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)

_SCORER_VERSION = "2.0.0+checkpointer-test"


# ---------------------------------------------------------------------------
# Seam fakes (orchestrator + scorer) — everything else is production code.
# ---------------------------------------------------------------------------


class _InstantOrchestrator:
    """AgentLLMOrchestrator fake resolving every request in ``prepare``.

    Optionally blocks every prepare call on ``release`` (set from the
    test) and signals ``first_call_started`` when the first LLM node
    reaches the seam — the hook the in-flight test uses to freeze the
    graph mid-execution deterministically.
    """

    def __init__(self, *, block_until_released: bool = False) -> None:
        self._block = block_until_released
        self.first_call_started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

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
        self.calls += 1
        self.first_call_started.set()
        if self._block:
            await self.release.wait()
        envelope: LLMResultEnvelope[Any] = LLMResultEnvelope(
            is_fallback=False, result=spec.result_schema()
        )
        return LLMOutcome(envelope=envelope, quota_remaining=10)

    async def execute(self, plan: ProviderCallPlan[Any, Any]) -> LLMOutcome[Any]:
        raise AssertionError("this fake resolves every request in prepare")


class _Scorer:
    """ScorerAdapter fake; the snapshot's version matches, so ATS reuses."""

    active_scorer_version = _SCORER_VERSION
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    async def score(self) -> ScoredMatch:
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version=_SCORER_VERSION,
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


def _deps(node_timeout_s: float = 15.0) -> AgentDeps:
    return AgentDeps(
        node_timeout_s=node_timeout_s,
        persist_agent_run=_noop_persist,
        tracer=NoOpTracer(),
        clock=time,
    )


def _initial_state(job_id: str) -> AgentState:
    return AgentState(
        job_id=job_id,
        match_id="00000000-0000-4000-8000-000000000002",
        user_id="00000000-0000-4000-8000-000000000003",
        redacted_resume_text="[NAME_1] built services in Python and Go.",
        job_description_skills=["python", "kubernetes"],
        match_snapshot=MatchSnapshot(
            score=71.5,
            breakdown={"similarity": 0.6},
            scorer_version=_SCORER_VERSION,
            matched_skills=["python"],
            missing_skills=["kubernetes"],
            suggestions=["Add a Kubernetes project."],
        ),
    )


def _thread_config(job_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": job_id}}


def _libpq_url() -> str:
    """The database URL in libpq form (same swap migration 0006 performs)."""
    return str(get_settings().database_url).replace("+asyncpg", "", 1)


async def _snapshots(saver: BaseCheckpointSaver[str], job_id: str) -> list[CheckpointTuple]:
    return [item async for item in saver.alist(_thread_config(job_id))]


# ---------------------------------------------------------------------------
# Completed-run round-trip against the Alembic-created schema (2.1/2.2/2.5/2.6)
# ---------------------------------------------------------------------------


@requires_postgres
async def test_completed_run_snapshots_keyed_by_job_id_and_readable_back() -> None:
    """Per-transition snapshots land keyed by the job id and read back.

    The real compiled graph runs with the real ``AsyncPostgresSaver``
    (schema from migration 0006 — this test performs no ``setup()`` and
    no DDL) wrapped in :class:`BestEffortSaver`; afterwards the saver is
    queried by the same ``thread_id = job_id`` and must return the run's
    snapshots, with the latest one carrying the final AnalysisResult.
    Covers Requirements 2.1, 2.2, 2.5 (schema pre-provisioned), 2.6.
    """
    job_id = str(uuid7())
    agents = build_agents(_deps(), _InstantOrchestrator(), _Scorer())

    async with AsyncPostgresSaver.from_conn_string(_libpq_url()) as inner:
        saver = BestEffortSaver(inner)
        graph = compile_graph(agents, saver)
        try:
            raw = await graph.ainvoke(_initial_state(job_id), _thread_config(job_id))
            final = AgentState.model_validate(raw)
            assert final.analysis_result is not None

            # One snapshot per graph transition: input + one per superstep
            # (resume_analysis ∥ ats, skill_gap ∥ improvement, synthesizer).
            snapshots = await _snapshots(saver, job_id)
            assert len(snapshots) >= 3

            # Every checkpoint row is attributable to exactly this job (2.2).
            for item in snapshots:
                assert item.config["configurable"]["thread_id"] == job_id

            # A different job id sees none of them.
            assert await _snapshots(saver, str(uuid7())) == []

            # The latest snapshot is readable and carries the final state's
            # AnalysisResult channel (2.6 — completed runs are inspectable).
            latest = await saver.aget_tuple(_thread_config(job_id))
            assert latest is not None
            value = latest.checkpoint["channel_values"].get("analysis_result")
            assert value is not None
            result = (
                value if isinstance(value, AnalysisResult) else AnalysisResult.model_validate(value)
            )
            assert len(result.agent_traces) == 4  # one per upstream agent
        finally:
            await saver.adelete_thread(job_id)


# ---------------------------------------------------------------------------
# In-flight readability (2.2/2.6) — read through a second connection while a
# node is deterministically blocked mid-graph.
# ---------------------------------------------------------------------------


@requires_postgres
async def test_inflight_run_snapshots_are_readable_while_running() -> None:
    """Snapshots for an in-flight run are queryable by the job id (2.6).

    The orchestrator seam blocks the Resume_Analysis node until released,
    freezing the graph mid-execution; a *separate* saver connection (the
    other-process inspection shape) must already see snapshots keyed by
    the job id. After release the run completes and more snapshots exist.
    """
    job_id = str(uuid7())
    orchestrator = _InstantOrchestrator(block_until_released=True)
    # Generous node timeout: the block must never trip the degradation path.
    agents = build_agents(_deps(node_timeout_s=60.0), orchestrator, _Scorer())

    async with (
        AsyncPostgresSaver.from_conn_string(_libpq_url()) as writer_inner,
        AsyncPostgresSaver.from_conn_string(_libpq_url()) as reader_inner,
    ):
        writer = BestEffortSaver(writer_inner)
        reader = BestEffortSaver(reader_inner)
        graph = compile_graph(agents, writer)
        task = asyncio.create_task(graph.ainvoke(_initial_state(job_id), _thread_config(job_id)))
        inflight: list[CheckpointTuple] = []
        try:
            try:
                await asyncio.wait_for(orchestrator.first_call_started.wait(), timeout=30.0)
                # Poll: the input checkpoint is written at run start, so at
                # least one snapshot must appear while the node is blocked.
                deadline = time.monotonic() + 30.0
                while not inflight:
                    inflight = await _snapshots(reader, job_id)
                    if inflight or time.monotonic() > deadline:
                        break
                    await asyncio.sleep(0.1)
            finally:
                orchestrator.release.set()
                raw = await asyncio.wait_for(task, timeout=60.0)

            assert inflight, "no snapshot was readable for the in-flight run"
            for item in inflight:
                assert item.config["configurable"]["thread_id"] == job_id

            final = AgentState.model_validate(raw)
            assert final.analysis_result is not None

            completed = await _snapshots(reader, job_id)
            assert len(completed) > len(inflight)
        finally:
            await reader.adelete_thread(job_id)


# ---------------------------------------------------------------------------
# Failing saver → warnings only, outcome unchanged (2.4). No docker needed.
# ---------------------------------------------------------------------------


class _FailingWritesSaver(BaseCheckpointSaver[str]):
    """Inner saver whose every write fails; reads behave like an empty store.

    ``get_next_version`` mirrors the Postgres saver's monotonic zero-padded
    string scheme so the Pregel loop's in-memory channel versioning stays
    correct while every persistence attempt raises.
    """

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        return
        yield  # pragma: no cover — makes this an async generator

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise ConnectionError("postgres unavailable")

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise ConnectionError("postgres unavailable")

    async def adelete_thread(self, thread_id: str) -> None:
        return None

    def get_next_version(self, current: str | None, channel: None) -> str:
        current_v = 0 if current is None else int(current.split(".")[0])
        return f"{current_v + 1:032}"


async def test_failing_saver_degrades_to_warnings_without_changing_the_outcome() -> None:
    """Requirement 2.4 across a full compiled-graph run.

    Every checkpoint write fails; the run must continue in memory to
    completion with one structured warning per failed write (job id +
    exception class name, no PII), and the graph outcome must equal an
    uncheckpointed run's — a checkpoint failure alone never yields a
    failed job.
    """
    job_id = str(uuid7())
    agents = build_agents(_deps(), _InstantOrchestrator(), _Scorer())
    graph = compile_graph(agents, BestEffortSaver(_FailingWritesSaver()))

    with structlog.testing.capture_logs() as captured:
        raw = await graph.ainvoke(_initial_state(job_id), _thread_config(job_id))
    final = AgentState.model_validate(raw)

    # The run completed: full AnalysisResult, no agent degraded.
    assert final.analysis_result is not None
    assert all(flag.status is AgentCompletion.COMPLETED for flag in final.agent_status.values())

    # One structured warning per failed write, carrying the job id and the
    # exception class name only (never the message — it could carry SQL
    # text or connection strings).
    warnings = [e for e in captured if e["event"] == CHECKPOINT_WRITE_FAILED_EVENT]
    assert warnings, "failed checkpoint writes must produce structured warnings"
    for warning in warnings:
        assert warning["log_level"] == "warning"
        assert warning["job_id"] == job_id
        assert warning["reason"] == "ConnectionError"
        assert "postgres unavailable" not in str(warning)

    # Outcome equivalence with an uncheckpointed run: same per-agent
    # statuses and a schema-valid AnalysisResult either way.
    baseline_agents = build_agents(_deps(), _InstantOrchestrator(), _Scorer())
    baseline_raw = await compile_graph(baseline_agents).ainvoke(_initial_state(job_id))
    baseline = AgentState.model_validate(baseline_raw)
    assert baseline.analysis_result is not None
    assert {name: flag.status for name, flag in baseline.agent_status.items()} == {
        name: flag.status for name, flag in final.agent_status.items()
    }
