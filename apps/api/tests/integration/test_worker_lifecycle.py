"""Worker lifecycle integration tests against LocalStack (task 17.2).

Validates Requirements 11.2, 11.3, 11.4, 12.7, 13.4, and 13.5 of
``phase-4-agentic`` end to end over the docker-compose infrastructure:
the real :class:`JobQueue` (aioboto3 → LocalStack SQS, connection values
from ``Settings`` only — Requirement 11.3), the real :class:`AgentWorker`
consume flow, the real :class:`DbJobStore` guarded transitions, and the
real :class:`GraphJobExecutor` (full compiled Agent_Graph, Postgres
checkpointer, Redis quota/cache) against committed Postgres rows.

The LLM provider seam is neutralized deterministically: the process-wide
key-present flag is pinned ``False`` (an autouse fixture), so the Phase 3
orchestrator resolves every LLM request via its key-absent fallback path
— zero provider calls, and both LLM agents take their deterministic
Degraded_Output paths. Everything else is production code.

Coverage map:

* **enqueue → consume → running → completed with timestamps** (11.2,
  11.4) — one Agent_Job travels the full path; the terminal row carries
  ``started_at``/``completed_at`` and the serialized AnalysisResult.
* **delete-after-persist ordering** (11.4) — a queue proxy reads the
  job's *committed* status from a fresh session at the moment ``delete``
  is called; it must already be terminal.
* **Synthesizer-failure persistence ordering** (12.7) — with the
  Synthesizer forced to fail, a store proxy observes the Synthesizer's
  committed ``failed`` agent_runs row *before* the terminal ``failed``
  transition is recorded.
* **trace id continuity across the queue** (13.4) — a span opened at
  enqueue time is injected as SQS message attributes by the real client,
  and the worker's ``agent.job`` span (captured via an
  ``InMemorySpanExporter``) continues the same trace id.
* **no-exporter no-op equivalence** (13.5) — the same job processed under
  the no-op tracer and under a configured tracer yields identical
  outcomes (statuses and results, latencies normalized).

Gating: skips cleanly unless Postgres, Redis, AND the configured
LocalStack SQS endpoint are all reachable (short-timeout socket probe
against ``Settings.sqs_endpoint_url``; a blank endpoint means real AWS
and is never probed from tests).
"""

from __future__ import annotations

import copy
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracer, Tracer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from matchlayer_api.config import get_settings
from matchlayer_api.db.models import AgentJob, AgentRun, MatchResult
from matchlayer_api.ml.agents.state import AgentState
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent
from matchlayer_api.services.agent_jobs.queue import (
    JobMessage,
    JobQueue,
    ReceivedMessage,
    _build_sqs_client_factory,
)
from matchlayer_api.services.agent_jobs.service import create_job
from matchlayer_api.workers.agent_worker import (
    AgentWorker,
    DbJobStore,
    GraphJobExecutor,
    JobSnapshot,
)

from .conftest import _service_available, postgres_available, redis_available

_AGENT_NAMES = {"resume_analysis", "ats", "skill_gap", "improvement", "synthesizer"}


def sqs_available() -> bool:
    """Short-timeout reachability probe against the configured SQS endpoint.

    A blank/unset ``MATCHLAYER_SQS_ENDPOINT_URL`` means production (real
    AWS SQS) — tests never probe or touch it, so this returns ``False``
    and the module skips. Any failure resolving Settings also skips.
    """
    try:
        endpoint = get_settings().sqs_endpoint_url
    except Exception:
        return False
    if not endpoint:
        return False
    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    return _service_available(host, port)


pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available() and sqs_available()),
    reason=(
        "Postgres, Redis, and LocalStack SQS must all be reachable (docker-compose not running)"
    ),
)


# ---------------------------------------------------------------------------
# Determinism: pin the LLM key-present flag False so the orchestrator's
# key-absent fallback path is taken — zero provider calls regardless of any
# local .env key, and both LLM agents degrade deterministically.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_llm_key_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("matchlayer_api.ml.llm.availability._key_present", False)


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A per-test NullPool session factory for the worker collaborators.

    ``NullPool`` per the repo's integration-suite discipline: pooled
    asyncpg connections bind their sockets to the per-test event loop and
    leak ``ResourceWarning``s at teardown under
    ``filterwarnings = ["error"]``.
    """
    engine = create_async_engine(
        str(get_settings().database_url),
        echo=False,
        poolclass=NullPool,
    )
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def job_queue() -> AsyncIterator[JobQueue]:
    """The real :class:`JobQueue` over a dedicated, per-test LocalStack queue.

    A uniquely named queue is created for the test and deleted afterwards
    so a locally running worker container can never race the test for its
    messages. The client factory is the production one — every connection
    parameter comes from ``Settings`` (Requirement 11.3).
    """
    settings = get_settings()
    factory = _build_sqs_client_factory(settings)
    queue_name = f"matchlayer-test-{uuid.uuid4().hex[:12]}"
    async with factory() as client:
        response = await client.create_queue(QueueName=queue_name)
        queue_url = response["QueueUrl"]
    queue = JobQueue(factory, queue_url=queue_url, wait_time_seconds=2)
    try:
        yield queue
    finally:
        async with factory() as client:
            await client.delete_queue(QueueUrl=queue_url)


@pytest_asyncio.fixture
async def committed_match(
    db_session: AsyncSession, factory_user: Any, factory_match: Any
) -> MatchResult:
    """A committed user + resume + match the worker's own sessions can see.

    The worker collaborators run in independent transactions, so the rows
    must be committed (the autouse ``_truncate_auth_tables`` fixture wipes
    them CASCADE before the next test).
    """
    user = await factory_user()
    match = await factory_match(user_id=user.id)
    await db_session.commit()
    return match


# ---------------------------------------------------------------------------
# Helpers and probes
# ---------------------------------------------------------------------------


async def _create_queued_job(
    session_factory: async_sessionmaker[AsyncSession], *, user_id: UUID, match_id: UUID
) -> UUID:
    async with session_factory() as session:
        creation = await create_job(session, user_id=user_id, match_id=match_id)
        await session.commit()
        return creation.job.id


async def _receive_one(queue: JobQueue, timeout_s: float = 30.0) -> ReceivedMessage:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        messages = await queue.receive()
        if messages:
            return messages[0]
    raise AssertionError(f"no message received from the test queue within {timeout_s}s")


async def _load_job(session_factory: async_sessionmaker[AsyncSession], job_id: UUID) -> AgentJob:
    async with session_factory() as session:
        result = await session.execute(select(AgentJob).where(AgentJob.id == job_id))
        return result.scalar_one()


class _DeleteProbeQueue:
    """QueueClient proxy recording the job's COMMITTED status at delete time.

    ``delete`` first reads the job's status through a fresh session (its
    own transaction — only committed state is visible), then delegates to
    the real queue. Requirement 11.4's ordering — "delete the message only
    after the terminal transition is committed" — therefore reduces to:
    every recorded status is terminal.
    """

    def __init__(
        self,
        inner: JobQueue,
        session_factory: async_sessionmaker[AsyncSession],
        job_id: UUID,
    ) -> None:
        self._inner = inner
        self._session_factory = session_factory
        self._job_id = job_id
        self.status_at_delete: list[str | None] = []

    async def receive(self) -> list[ReceivedMessage]:
        return await self._inner.receive()

    async def delete(self, receipt_handle: str) -> None:
        async with self._session_factory() as session:
            status = await session.scalar(
                select(AgentJob.status).where(AgentJob.id == self._job_id)
            )
        self.status_at_delete.append(status)
        await self._inner.delete(receipt_handle)


class _SynthesizerRowProbeStore:
    """JobStore proxy over the real :class:`DbJobStore` for the 12.7 ordering.

    At the moment ``mark_failed`` is invoked, the Synthesizer's ``failed``
    agent_runs row must already be COMMITTED (visible from a fresh
    session's transaction) — that is Requirement 12.7's ordering.
    """

    def __init__(
        self, inner: DbJobStore, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self._inner = inner
        self._session_factory = session_factory
        self.synthesizer_status_before_failed: str | None = None

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        return await self._inner.load(job_id)

    async def mark_running(self, job_id: UUID) -> None:
        await self._inner.mark_running(job_id)

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        await self._inner.mark_completed(job_id, result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            self.synthesizer_status_before_failed = await session.scalar(
                select(AgentRun.status).where(
                    AgentRun.job_id == job_id,
                    AgentRun.agent_name == SynthesizerAgent.name,
                )
            )
        await self._inner.mark_failed(job_id, error)


def _build_worker(
    queue: Any,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    store: Any | None = None,
    tracer: Tracer | None = None,
) -> AgentWorker:
    settings = get_settings()
    return AgentWorker(
        queue=queue,
        store=store if store is not None else DbJobStore(session_factory),
        execute_job=GraphJobExecutor(session_factory=session_factory, settings=settings),
        max_attempts=settings.agent_max_attempts,
        tracer=tracer if tracer is not None else NoOpTracer(),
    )


def _normalized_result(result: dict[str, Any]) -> dict[str, Any]:
    """The AnalysisResult document with run-varying latencies zeroed."""
    normalized = copy.deepcopy(result)
    for trace_summary in normalized.get("agent_traces", []):
        trace_summary["latency_ms"] = 0
    return normalized


# ---------------------------------------------------------------------------
# 1. Full lifecycle: enqueue → consume → running → completed (11.2, 11.4)
#    + delete-after-persist ordering.
# ---------------------------------------------------------------------------


async def test_enqueue_consume_running_completed_with_timestamps(
    committed_match: MatchResult,
    session_factory: async_sessionmaker[AsyncSession],
    job_queue: JobQueue,
) -> None:
    job_id = await _create_queued_job(
        session_factory, user_id=committed_match.user_id, match_id=committed_match.id
    )
    probe = _DeleteProbeQueue(job_queue, session_factory, job_id)
    worker = _build_worker(probe, session_factory)

    await job_queue.enqueue(
        JobMessage(
            job_id=str(job_id),
            match_id=str(committed_match.id),
            user_id=str(committed_match.user_id),
        )
    )
    received = await _receive_one(job_queue)
    await worker.process_message(received)

    job = await _load_job(session_factory, job_id)
    assert job.status == "completed"
    assert job.attempts == 1
    assert job.error_json is None

    # Timestamps: running stamped started_at, completed stamped completed_at.
    assert job.started_at is not None
    assert job.completed_at is not None
    assert job.created_at <= job.started_at <= job.completed_at

    # The persisted AnalysisResult document (validates as the real schema).
    assert job.result_json is not None
    assert set(job.result_json) >= {
        "ats",
        "skill_gaps",
        "improvements",
        "profile",
        "agent_traces",
    }

    # One committed agent_runs row per node.
    async with session_factory() as session:
        rows = await session.execute(select(AgentRun.agent_name).where(AgentRun.job_id == job_id))
        assert set(rows.scalars()) == _AGENT_NAMES

    # Delete-after-persist: at the moment the real DeleteMessage went out,
    # the terminal transition was already committed (Requirement 11.4).
    assert probe.status_at_delete == ["completed"]

    # The acknowledged message never comes back.
    assert await job_queue.receive() == []


# ---------------------------------------------------------------------------
# 2. Synthesizer-failure persistence ordering (12.7).
# ---------------------------------------------------------------------------


async def test_synthesizer_failure_persists_failed_run_before_terminal_transition(
    committed_match: MatchResult,
    session_factory: async_sessionmaker[AsyncSession],
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Synthesizer's failed agent_runs row commits before job `failed`.

    The Synthesizer is the one node whose failure fails the job
    (Requirement 7.6); Requirement 12.7 orders its ``failed`` Agent_Run
    row *before* the terminal transition. The store proxy checks the
    committed row at the exact moment ``mark_failed`` is invoked.
    """

    async def _boom(self: SynthesizerAgent, state: AgentState) -> Any:
        raise RuntimeError("forced synthesizer failure")

    monkeypatch.setattr(SynthesizerAgent, "run", _boom)

    job_id = await _create_queued_job(
        session_factory, user_id=committed_match.user_id, match_id=committed_match.id
    )
    store = _SynthesizerRowProbeStore(DbJobStore(session_factory), session_factory)
    queue_probe = _DeleteProbeQueue(job_queue, session_factory, job_id)
    worker = _build_worker(queue_probe, session_factory, store=store)

    await job_queue.enqueue(
        JobMessage(
            job_id=str(job_id),
            match_id=str(committed_match.id),
            user_id=str(committed_match.user_id),
        )
    )
    received = await _receive_one(job_queue)
    await worker.process_message(received)

    job = await _load_job(session_factory, job_id)
    assert job.status == "failed"
    assert job.completed_at is not None
    assert job.result_json is None
    # Structured, PII-free error document (never the exception message).
    assert job.error_json is not None
    assert job.error_json.get("trigger") == "error"
    assert "forced synthesizer failure" not in str(job.error_json)

    # Requirement 12.7's ordering: the Synthesizer's failed run row was
    # already committed when the terminal failed transition was recorded.
    assert store.synthesizer_status_before_failed == "failed"

    # Upstream rows are retained; the terminal commit preceded the ack.
    async with session_factory() as session:
        rows = await session.execute(
            select(AgentRun.agent_name, AgentRun.status).where(AgentRun.job_id == job_id)
        )
        by_agent = dict(rows.all())
    assert set(by_agent) == _AGENT_NAMES
    assert by_agent["synthesizer"] == "failed"
    assert queue_probe.status_at_delete == ["failed"]


# ---------------------------------------------------------------------------
# 3. Trace id continuity across the queue (13.4).
# ---------------------------------------------------------------------------


class _RecordingStore:
    """Minimal in-memory JobStore: the trace test targets queue propagation."""

    def __init__(self, job: JobSnapshot) -> None:
        self._job = job
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        return self._job

    async def mark_running(self, job_id: UUID) -> None:
        return None

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        self.completed.append(result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        self.failed.append(error)


async def test_trace_id_continuity_across_the_queue(job_queue: JobQueue) -> None:
    """The trace opened at enqueue continues into the worker's job span.

    The real client injects W3C ``traceparent`` message attributes at
    ``enqueue`` (Requirement 13.4); after the round trip through
    LocalStack SQS, the worker extracts them and its ``agent.job`` span
    must carry the same trace id as the enqueue-side span — captured via
    an ``InMemorySpanExporter`` on a test-local ``TracerProvider`` (the
    global provider is never touched).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("worker-lifecycle-test")

    job_id = uuid.uuid4()
    message = JobMessage(job_id=str(job_id), match_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
    with tracer.start_as_current_span("test.enqueue") as enqueue_span:
        enqueue_context = enqueue_span.get_span_context()
        await job_queue.enqueue(message)

    received = await _receive_one(job_queue)
    # The trace context really travelled as SQS message attributes.
    assert "traceparent" in {name.lower() for name in received.message_attributes}

    store = _RecordingStore(
        JobSnapshot(
            id=job_id,
            status="queued",
            attempts=0,
            match_id=UUID(message.match_id),
            user_id=UUID(message.user_id),
        )
    )

    async def _stub_executor(msg: JobMessage) -> dict[str, Any]:
        return {"ok": True}

    settings = get_settings()
    worker = AgentWorker(
        queue=job_queue,
        store=store,
        execute_job=_stub_executor,
        max_attempts=settings.agent_max_attempts,
        tracer=tracer,
    )
    await worker.process_message(received)
    provider.shutdown()

    assert store.failed == []
    assert store.completed == [{"ok": True}]

    job_spans = [s for s in exporter.get_finished_spans() if s.name == "agent.job"]
    assert len(job_spans) == 1
    job_span = job_spans[0]
    assert job_span.context is not None
    # Same trace, different span: the worker CONTINUED the enqueue trace.
    assert job_span.context.trace_id == enqueue_context.trace_id
    assert job_span.context.span_id != enqueue_context.span_id
    assert job_span.attributes is not None
    assert job_span.attributes.get("matchlayer.job_id") == str(job_id)


# ---------------------------------------------------------------------------
# 4. No-exporter no-op equivalence (13.5).
# ---------------------------------------------------------------------------


async def test_outcomes_identical_with_and_without_tracing_configured(
    committed_match: MatchResult,
    session_factory: async_sessionmaker[AsyncSession],
    job_queue: JobQueue,
) -> None:
    """Traced and untraced runs produce identical outcomes (13.5).

    The same match is analyzed twice through the full worker path: once
    under the no-op tracer (no exporter configured) and once under a
    configured tracer exporting to memory. Both jobs must complete with
    field-for-field identical AnalysisResults once the run-varying
    latencies are normalized.
    """

    async def _run_once(tracer: Tracer) -> tuple[str, dict[str, Any] | None]:
        job_id = await _create_queued_job(
            session_factory,
            user_id=committed_match.user_id,
            match_id=committed_match.id,
        )
        worker = _build_worker(job_queue, session_factory, tracer=tracer)
        await job_queue.enqueue(
            JobMessage(
                job_id=str(job_id),
                match_id=str(committed_match.id),
                user_id=str(committed_match.user_id),
            )
        )
        received = await _receive_one(job_queue)
        await worker.process_message(received)
        job = await _load_job(session_factory, job_id)
        return job.status, job.result_json

    # Run A: the unconfigured-exporter path — a no-op tracer (13.5).
    status_untraced, result_untraced = await _run_once(NoOpTracer())

    # Run B: tracing configured (test-local provider; global untouched).
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    status_traced, result_traced = await _run_once(provider.get_tracer("worker-lifecycle-test"))
    provider.shutdown()

    # Tracing was genuinely active on run B.
    assert any(s.name == "agent.job" for s in exporter.get_finished_spans())

    # Identical outcomes: same terminal status, same result document
    # (latencies are the only run-varying values).
    assert status_untraced == status_traced == "completed"
    assert result_untraced is not None and result_traced is not None
    assert _normalized_result(result_untraced) == _normalized_result(result_traced)
