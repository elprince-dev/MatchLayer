"""The 30-second latency-budget CI test (phase-4-agentic task 12.2).

Requirement 14.4 mandates an automated, CI-runnable test that executes
the full Agent_Graph for one Agent_Job with a mocked LLM_Client whose
injected per-call latency equals the 10-second normal-latency bound of
Requirement 14.1, asserting the elapsed time between the job's recorded
``started_at`` and ``completed_at`` timestamps is less than 30 seconds.

The test drives the worker's execution path: :class:`AgentWorker`
consumes one message through its real ``process_message`` flow —
``mark_running`` (which stamps ``started_at`` in production, per
Requirement 11.4) → executor → ``mark_completed`` (which stamps
``completed_at``) → delete. The executor runs the real compiled
Agent_Graph with the real five agents; the LLM provider seam is mocked
so every provider call sleeps exactly 10 seconds before returning a
structured result. The Job_Store fake records real UTC timestamps on the
two transitions, mirroring the production ``DbJobStore`` columns.

Why the budget holds: the graph's longest sequential path carries the
two LLM calls (Resume_Analysis → Improvement), so the worst case under
normal provider latency is ~20 s of provider time plus deterministic
overhead — under the 30 s bound with ~10 s of headroom. The default
per-node timeout (``MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS`` = 20)
comfortably exceeds the 10 s per-call latency, so no node degrades:
this is the Requirement 14.1 *typical* path, not a degradation path.

Marked ``@pytest.mark.timing`` per the repo convention for
wall-clock-sensitive tests (~20 s of real sleeping): CI's plain
``uv run pytest`` includes it (Requirement 14.4 — runnable in CI),
while the local fast loop can exclude it with ``-m "not timing"``.

**Validates: Requirements 14.1, 14.4**
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.services.agent_jobs.queue import JobMessage, ReceivedMessage
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOutcome,
    ProviderCallPlan,
)
from matchlayer_api.services.llm.schemas import LLMResultEnvelope
from matchlayer_api.workers.agent_worker import AgentWorker, JobSnapshot

pytestmark = pytest.mark.timing

# Requirement 14.1's normal-latency bound: every LLM provider call made
# during the run completes within 10 seconds — injected here as exactly
# 10 seconds of sleep per call at the provider seam.
_LLM_CALL_LATENCY_S = 10.0

# The Requirement 14.1 budget on started_at → completed_at.
_BUDGET_S = 30.0

# The documented default of MATCHLAYER_AGENT_NODE_TIMEOUT_SECONDS: it
# exceeds the 10 s per-call latency, so the injected latency degrades
# nothing — this measures the typical path.
_NODE_TIMEOUT_S = 20.0

_JOB_ID = UUID("00000000-0000-4000-8000-000000000001")
_MATCH_ID = UUID("00000000-0000-4000-8000-000000000002")
_USER_ID = UUID("00000000-0000-4000-8000-000000000003")
_MAX_ATTEMPTS = 2

_STALE_SCORER_VERSION = "1.0.0+stale"
_ACTIVE_SCORER_VERSION = "2.0.0+active"


# ---------------------------------------------------------------------------
# The mocked LLM provider seam: 10 s injected per-call latency.
# ---------------------------------------------------------------------------


class _SlowOrchestrator:
    """AgentLLMOrchestrator fake sleeping 10 s per provider call.

    The sleep sits at the seam through which every Agent LLM_Provider
    call flows (design D1: LLM agents call the orchestrator, which owns
    the LLM_Client), so each of the run's provider calls costs exactly
    the Requirement 14.1 normal-latency bound.
    """

    def __init__(self) -> None:
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
        # The sleep sits inside the awaited call, exactly where the
        # provider round-trip would block — per-call latency, not a
        # single lump added to the run.
        await asyncio.sleep(_LLM_CALL_LATENCY_S)
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
# Worker collaborators: timestamping store, recording queue, real-graph
# executor.
# ---------------------------------------------------------------------------


class _TimestampingStore:
    """JobStore fake recording real UTC transition timestamps.

    Mirrors the production ``DbJobStore`` semantics the measurement
    depends on: ``mark_running`` stamps ``started_at`` (the ``running``
    transition, Requirement 11.4) and ``mark_completed`` stamps
    ``completed_at`` — the two persisted timestamps Requirement 14.1
    defines the elapsed time over.
    """

    def __init__(self, job: JobSnapshot) -> None:
        self._job = job
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.result: dict[str, Any] | None = None
        self.failed_errors: list[dict[str, Any]] = []

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        return self._job

    async def mark_running(self, job_id: UUID) -> None:
        self.started_at = datetime.now(UTC)

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        self.completed_at = datetime.now(UTC)
        self.result = result

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        self.failed_errors.append(error)


class _Queue:
    """QueueClient fake recording acknowledgements."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def receive(self) -> list[ReceivedMessage]:  # pragma: no cover — loop-only
        return []

    async def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)


class _GraphExecutor:
    """JobExecutor running the real compiled Agent_Graph for one job.

    The graph is the production topology over the real five agents;
    only the orchestrator (provider seam) and scorer are fakes. The
    final state is retained so the test can assert the run was the
    non-degraded typical path.
    """

    def __init__(self, orchestrator: _SlowOrchestrator) -> None:
        self._orchestrator = orchestrator
        self.final_state: AgentState | None = None

    async def __call__(self, message: JobMessage) -> dict[str, Any]:
        deps = AgentDeps(
            node_timeout_s=_NODE_TIMEOUT_S,
            persist_agent_run=_noop_persist,
            tracer=NoOpTracer(),
            clock=time,
        )
        compiled = compile_graph(build_agents(deps, self._orchestrator, _Scorer()))
        raw_state: dict[str, Any] = await compiled.ainvoke(_initial_state(message))
        state = AgentState.model_validate(raw_state)
        self.final_state = state
        assert state.analysis_result is not None
        return state.analysis_result.model_dump(mode="json")


def _initial_state(message: JobMessage) -> AgentState:
    """A typical initial state; the stale scorer version forces a fresh score."""
    return AgentState(
        job_id=message.job_id,
        match_id=message.match_id,
        user_id=message.user_id,
        redacted_resume_text="[NAME_1] built services in Python and Go.",
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


def _received_message() -> ReceivedMessage:
    body = JobMessage(
        job_id=str(_JOB_ID), match_id=str(_MATCH_ID), user_id=str(_USER_ID)
    ).model_dump_json()
    return ReceivedMessage(body=body, receipt_handle="rh-latency-budget")


class TestThirtySecondLatencyBudget:
    async def test_full_graph_job_completes_within_30_seconds_at_10s_per_llm_call(
        self,
    ) -> None:
        """started_at → completed_at < 30 s (Requirements 14.1, 14.4).

        One Agent_Job runs end to end through the worker's message flow
        with 10 s injected into every LLM provider call. The two calls
        sit on the longest sequential path (Resume_Analysis →
        Improvement), so the run costs ~20 s of provider time plus
        deterministic overhead — the elapsed time between the recorded
        transition timestamps must stay under the 30 s budget.
        """
        orchestrator = _SlowOrchestrator()
        executor = _GraphExecutor(orchestrator)
        store = _TimestampingStore(
            JobSnapshot(
                id=_JOB_ID,
                status="queued",
                attempts=0,
                match_id=_MATCH_ID,
                user_id=_USER_ID,
            )
        )
        queue = _Queue()
        worker = AgentWorker(
            queue=queue,
            store=store,
            execute_job=executor,
            max_attempts=_MAX_ATTEMPTS,
            tracer=NoOpTracer(),
        )

        await worker.process_message(_received_message())

        # The job completed (never failed) and the message was acked.
        assert store.failed_errors == []
        assert store.result is not None
        assert queue.deleted == ["rh-latency-budget"]

        # Exactly the run's two LLM provider calls went through the slow
        # seam — both paid the injected 10 s latency.
        assert orchestrator.calls == 2

        # The typical path: no node degraded under normal provider latency.
        final_state = executor.final_state
        assert final_state is not None
        assert all(
            flag.status is AgentCompletion.COMPLETED for flag in final_state.agent_status.values()
        )

        # Requirement 14.1: elapsed started_at → completed_at < 30 s.
        assert store.started_at is not None
        assert store.completed_at is not None
        elapsed_s = (store.completed_at - store.started_at).total_seconds()
        assert elapsed_s >= 2 * _LLM_CALL_LATENCY_S  # both calls really slept
        assert elapsed_s < _BUDGET_S, (
            f"Agent_Job took {elapsed_s:.2f}s from started_at to completed_at — "
            f"over the {_BUDGET_S:.0f}s latency budget of Requirement 14.1"
        )
