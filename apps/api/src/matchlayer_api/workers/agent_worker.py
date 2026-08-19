"""Agent_Worker — the long-running SQS consumer executing Agent_Jobs.

The separate-process half of the Phase 4 async path (Requirement 11.2;
own container via ``infra/docker/worker.Dockerfile``, entrypoint
``python -m matchlayer_api.workers.agent_worker``). The API accepts an
analyze request, persists a ``queued`` Agent_Job, and enqueues an
identifiers-only :class:`~matchlayer_api.services.agent_jobs.queue.JobMessage`;
this worker consumes that queue, loads job context from the database by
identifier, executes the compiled Agent_Graph, and records the terminal
Job_Status — deleting the message only after the terminal transition is
committed (Requirement 11.4).

Per-message flow (design §6, in order):

1. **Parse + validate** the :class:`JobMessage` body. A malformed body
   — or, later, a message referencing an Agent_Job that does not exist
   — is a *poison message*: one structured warning (no PII), delete,
   move on. A poison message can never crash-loop the worker
   (Requirement 11.7).
2. **Trace context** is extracted from the SQS message attributes; a
   missing or invalid context yields a fresh trace (Requirement 13.6).
   The job span (``agent.job``, attribute ``matchlayer.job_id``) opens
   here so every agent child span nests under it (Requirement 13.1)
   and the originating request's trace id is continued when present
   (Requirement 13.4).
3. **Redelivery policy** (Requirement 11.5), decided by the pure
   :func:`decide_redelivery` over the *persisted* ``attempts`` counter:
   terminal job → ack without re-execution (11.5a); ``queued``, or
   ``running`` below ``MATCHLAYER_AGENT_MAX_ATTEMPTS`` → transition to
   ``running`` (which atomically increments ``attempts`` and sets
   ``started_at`` on the first attempt) and execute (11.5b); ``running``
   at the cap → ``failed`` with a structured "max attempts exhausted"
   error, then ack (11.5c).
4. **Initial Agent_State** is built here in the worker — the API
   analyze path never reads ``extracted_text`` (Requirement 11.8): load
   the Match_Result and Resume by identifier, run the Phase 3
   ``PII_Redactor`` over ``extracted_text``, run the Phase 2
   ``Skill_Extractor`` over the Job_Description, and project the
   ``MatchSnapshot``. Any failure here is a *pre-invocation validation
   failure*: the job transitions to ``failed`` with a structured
   PII-free error and **no node executes** (Requirement 1.8).
5. **Graph invocation** with ``thread_id = job_id`` (Requirement 2.2)
   under the best-effort Postgres checkpointer. On success the
   ``AnalysisResult`` is persisted with the ``completed`` transition;
   on Synthesizer failure the Synthesizer's ``failed`` Agent_Run row is
   persisted and committed *before* the terminal ``failed`` transition
   (Requirement 12.7) — upstream Agent_Run rows are already committed
   per invocation and are retained.
6. **Delete the message only after** the terminal transition is
   committed. If the terminal transition itself cannot be persisted
   (database outage), the message is deliberately NOT deleted so SQS
   redelivery — bounded by the attempt counter — retries.

Process setup mirrors the API startup exactly (design §6: "the worker
installs the same structlog JSON logging and OTel setup as the API"):
:func:`~matchlayer_api.core.logging.configure_logging`,
:func:`~matchlayer_api.core.tracing.configure_tracing` (service name
``matchlayer-worker``), the fail-fast database probe, the Phase 2
semantic-pipeline load (Degraded_Mode on failure), and the Phase 3 LLM
availability check.

Testability: the consumer (:class:`AgentWorker`) is composed from three
injected collaborators — the :class:`JobQueue`, a :class:`JobStore`, and
a :data:`JobExecutor` — so the message-handling decision logic is unit
testable with fakes and no database, queue, or graph. The production
implementations (:class:`DbJobStore`, :class:`GraphJobExecutor`) live
alongside and are composed by :func:`run_worker`.

Design reference: phase-4-agentic design §6 and the async execution
flow. Requirements covered: 1.8, 11.2, 11.4, 11.5, 11.7, 11.8, 12.7,
13.1, 13.6.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol
from uuid import UUID

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opentelemetry import trace
from opentelemetry.trace import Tracer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.db import SessionLocal, verify_database_connection
from matchlayer_api.core.logging import configure_logging
from matchlayer_api.core.redis import get_redis_client
from matchlayer_api.core.tracing import configure_tracing, extract_trace_context
from matchlayer_api.db.models import AgentJob, MatchResult, Resume
from matchlayer_api.ml.agents.base import AgentDeps, classify_failure
from matchlayer_api.ml.agents.graph import BestEffortSaver, build_agents, compile_graph
from matchlayer_api.ml.agents.llm_agent import MatchScopedOrchestrator
from matchlayer_api.ml.agents.state import AgentState, FailureDetail, MatchSnapshot
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent
from matchlayer_api.ml.llm.availability import build_llm_client, initialize_llm_availability
from matchlayer_api.ml.semantic_adapter import load_semantic_pipeline
from matchlayer_api.services.agent_jobs import service as job_service
from matchlayer_api.services.agent_jobs.queue import (
    JobMessage,
    ReceivedMessage,
    get_job_queue,
)
from matchlayer_api.services.agent_jobs.runs import build_persist_agent_run, persist_agent_run
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.orchestrator import LLMOrchestrator
from matchlayer_api.services.llm.quota import DailyQuota
from matchlayer_api.services.llm.redaction import redact
from matchlayer_api.services.llm.spend import get_spend_circuit_breaker
from matchlayer_api.workers.adapters import WorkerScorerAdapter, extract_job_description_skills

__all__ = [
    "AgentWorker",
    "DbJobStore",
    "GraphJobExecutor",
    "JobExecutionError",
    "JobExecutor",
    "JobSnapshot",
    "JobStore",
    "QueueClient",
    "RedeliveryDecision",
    "decide_redelivery",
    "main",
    "parse_job_message",
    "run_worker",
]

_log = structlog.get_logger(__name__)

# Tracer name for the worker's spans (the job span here; agent child
# spans use the tracer injected through AgentDeps).
_TRACER_NAME: Final[str] = "matchlayer.agent_worker"

# The OTel resource ``service.name`` this process reports (design §9).
# The Settings default is the API's name; when the operator has not
# explicitly configured a worker-specific value, the worker substitutes
# its own so worker spans never masquerade as API spans.
_WORKER_SERVICE_NAME: Final[str] = "matchlayer-worker"
_API_DEFAULT_SERVICE_NAME: Final[str] = "matchlayer-api"

# Backoff between receive attempts after a transport failure, so an
# unreachable queue produces a bounded warning rate instead of a hot loop.
_RECEIVE_BACKOFF_SECONDS: Final[float] = 5.0

# Terminal Job_Status values (redelivery case 11.5a acks these).
_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"completed", "failed"})

# Structured event names (identifiers and reasons only — never PII).
POISON_MESSAGE_EVENT: Final[str] = "agent_worker_poison_message"
TERMINAL_ACK_EVENT: Final[str] = "agent_job_terminal_ack"
TRANSITION_LOST_EVENT: Final[str] = "agent_job_transition_lost"
TERMINAL_PERSIST_FAILED_EVENT: Final[str] = "agent_job_terminal_persist_failed"


# ---------------------------------------------------------------------------
# Message parsing (Requirement 11.7 — poison-message safety).
# ---------------------------------------------------------------------------


def parse_job_message(body: str) -> JobMessage | None:
    """Parse and validate one raw message body, or ``None`` when malformed.

    Validation is strict: the body must be the exact identifiers-only
    :class:`JobMessage` JSON document (``extra="forbid"`` rejects any
    additional field) and every identifier must parse as a UUID. Any
    violation returns ``None`` so the caller can treat the message as
    poison — warn and delete — rather than raising (Requirement 11.7).
    """
    try:
        message = JobMessage.model_validate_json(body)
        UUID(message.job_id)
        UUID(message.match_id)
        UUID(message.user_id)
    except ValueError:  # pydantic.ValidationError subclasses ValueError
        return None
    return message


# ---------------------------------------------------------------------------
# Redelivery policy (Requirement 11.5) — a pure, unit-testable decision.
# ---------------------------------------------------------------------------


class RedeliveryDecision(StrEnum):
    """What to do with a delivered message, per the persisted job state."""

    #: The job is already terminal — acknowledge without re-execution (11.5a).
    ACK_TERMINAL = "ack_terminal"
    #: Execute: first delivery (``queued``) or an in-flight redelivery
    #: below the attempt cap (11.5b).
    EXECUTE = "execute"
    #: The attempt counter reached the cap — fail the job and ack (11.5c).
    FAIL_MAX_ATTEMPTS = "fail_max_attempts"


def decide_redelivery(status: str, attempts: int, max_attempts: int) -> RedeliveryDecision:
    """Map the persisted job state onto a :class:`RedeliveryDecision`.

    Pure function over ``(status, attempts, max_attempts)`` — the
    persisted per-job attempt counter is the sole redelivery arbiter
    (Requirement 11.5), never SQS's own delivery metadata:

    * terminal (``completed``/``failed``) → :attr:`ACK_TERMINAL`;
    * ``queued`` → :attr:`EXECUTE` (first delivery; the ``running``
      transition increments ``attempts`` atomically);
    * ``running`` with ``attempts < max_attempts`` → :attr:`EXECUTE`
      (the previous attempt crashed or outlived the visibility timeout);
    * ``running`` with ``attempts >= max_attempts`` →
      :attr:`FAIL_MAX_ATTEMPTS`.
    """
    if status in _TERMINAL_STATUSES:
        return RedeliveryDecision.ACK_TERMINAL
    if status == "queued":
        return RedeliveryDecision.EXECUTE
    if attempts < max_attempts:
        return RedeliveryDecision.EXECUTE
    return RedeliveryDecision.FAIL_MAX_ATTEMPTS


def _max_attempts_error(attempts: int, max_attempts: int) -> dict[str, Any]:
    """The structured, display-safe error for the 11.5c terminal path."""
    return FailureDetail(
        trigger="error",
        detail=(
            f"analysis was attempted {attempts} time(s) and did not complete; "
            f"the maximum of {max_attempts} delivery attempts is exhausted"
        ),
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Collaborator contracts (injected for unit testability).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """The slice of one ``agent_jobs`` row the consumer decisions need."""

    id: UUID
    status: str
    attempts: int
    match_id: UUID
    user_id: UUID


class QueueClient(Protocol):
    """The slice of :class:`JobQueue` the consumer uses (receive + ack).

    Structural so unit tests inject recording fakes; production passes
    the real :class:`~matchlayer_api.services.agent_jobs.queue.JobQueue`.
    """

    async def receive(self) -> list[ReceivedMessage]:
        """Long-poll the Job_Queue; return raw messages."""
        ...

    async def delete(self, receipt_handle: str) -> None:
        """Acknowledge (delete) one message by receipt handle."""
        ...


class JobStore(Protocol):
    """Persistence surface the consumer drives (production: :class:`DbJobStore`).

    Every ``mark_*`` method COMMITS its transition before returning —
    that commit-before-return contract is what lets the consumer honor
    "delete the message only after the terminal transition is committed"
    (Requirement 11.4) by simply deleting after the call.
    """

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        """The current job snapshot, or ``None`` when no such job exists."""
        ...

    async def mark_running(self, job_id: UUID) -> None:
        """Transition to ``running`` (increments ``attempts``); commit."""
        ...

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        """Record the terminal ``completed`` + AnalysisResult; commit."""
        ...

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        """Record the terminal ``failed`` + structured error; commit."""
        ...


class JobExecutionError(Exception):
    """The executor could not produce an AnalysisResult for the job.

    Carries the structured, PII-free, display-safe ``error`` document
    the job's ``error_json`` records (Requirement 11.4). Raised for both
    pre-invocation validation failures (Requirement 1.8 — no node
    executed) and Synthesizer failures (Requirement 7.6 — the executor
    has already persisted the Synthesizer's ``failed`` Agent_Run row per
    Requirement 12.7 before raising).
    """

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("trigger", "error")))
        self.error = error


type JobExecutor = Callable[[JobMessage], Awaitable[dict[str, Any]]]
"""Executes one Agent_Job end to end; returns the AnalysisResult JSON.

Production implementation: :class:`GraphJobExecutor`. Raises
:class:`JobExecutionError` when the job must fail.
"""


# ---------------------------------------------------------------------------
# The consumer.
# ---------------------------------------------------------------------------


class AgentWorker:
    """The SQS consume loop over injected queue/store/executor collaborators.

    Owns the message-handling decisions only — parsing, trace-context
    handling, the redelivery policy, transition ordering, and the
    delete-after-commit acknowledgement discipline. Everything with I/O
    weight (database, graph, LLM) sits behind the injected collaborators.
    """

    def __init__(
        self,
        *,
        queue: QueueClient,
        store: JobStore,
        execute_job: JobExecutor,
        max_attempts: int,
        tracer: Tracer | None = None,
    ) -> None:
        """Compose the consumer.

        Args:
            queue: The Job_Queue client (long-poll receive / delete).
            store: The Agent_Job persistence surface.
            execute_job: The per-job executor (graph invocation).
            max_attempts: ``MATCHLAYER_AGENT_MAX_ATTEMPTS`` — injected,
                never read from configuration here.
            tracer: Injected OTel tracer; defaults to the module tracer
                (a no-op tracer when no exporter is configured,
                Requirement 13.5).
        """
        self._queue = queue
        self._store = store
        self._execute_job = execute_job
        self._max_attempts = max_attempts
        self._tracer = tracer if tracer is not None else trace.get_tracer(_TRACER_NAME)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Consume the queue until ``stop_event`` is set.

        Receive-transport failures are absorbed with one structured
        warning and a bounded backoff — the queue client deliberately
        propagates them so this loop owns retry policy. A failure while
        *processing* a message is also absorbed (warn, no delete), so
        SQS redelivery — bounded by the persisted attempt counter —
        retries it; nothing here can crash-loop the process.
        """
        stop = stop_event if stop_event is not None else asyncio.Event()
        while not stop.is_set():
            try:
                messages = await self._queue.receive()
            except Exception as exc:
                _log.warning("agent_worker_receive_failed", reason=type(exc).__name__)
                await asyncio.sleep(_RECEIVE_BACKOFF_SECONDS)
                continue
            for received in messages:
                try:
                    await self.process_message(received)
                except Exception as exc:
                    # Defensive: no delete → SQS redelivers; the attempt
                    # counter bounds retries (Requirement 11.5).
                    _log.error(
                        "agent_worker_message_processing_failed",
                        reason=type(exc).__name__,
                    )

    async def process_message(self, received: ReceivedMessage) -> None:
        """Handle one delivered message per the design §6 flow."""
        message = parse_job_message(received.body)
        if message is None:
            # Poison: malformed body. Warn (no body content — it could be
            # anything) and ack so it can never crash-loop (Req 11.7).
            _log.warning(POISON_MESSAGE_EVENT, reason="malformed_body")
            await self._queue.delete(received.receipt_handle)
            return

        # Trace continuity (Req 13.4) or a fresh trace when the context is
        # missing/invalid (Req 13.6) — extract_trace_context never raises.
        otel_context = extract_trace_context(received.message_attributes)
        with self._tracer.start_as_current_span("agent.job", context=otel_context) as span:
            span.set_attribute("matchlayer.job_id", message.job_id)
            job_id = UUID(message.job_id)

            job = await self._store.load(job_id)
            if job is None:
                # Poison: the referenced Agent_Job does not exist (Req 11.7).
                _log.warning(POISON_MESSAGE_EVENT, reason="job_not_found", job_id=message.job_id)
                await self._queue.delete(received.receipt_handle)
                return

            decision = decide_redelivery(job.status, job.attempts, self._max_attempts)
            span.set_attribute("matchlayer.redelivery_decision", decision.value)

            if decision is RedeliveryDecision.ACK_TERMINAL:
                _log.info(TERMINAL_ACK_EVENT, job_id=message.job_id, status=job.status)
                await self._queue.delete(received.receipt_handle)
                return

            if decision is RedeliveryDecision.FAIL_MAX_ATTEMPTS:
                await self._fail_and_ack(
                    job_id,
                    received.receipt_handle,
                    _max_attempts_error(job.attempts, self._max_attempts),
                )
                return

            # EXECUTE: transition to running (atomically counting the
            # attempt and stamping started_at on the first one, Req 11.4).
            try:
                await self._store.mark_running(job_id)
            except job_service.InvalidJobTransitionError:
                # A concurrent worker recorded a terminal state between our
                # read and this guarded UPDATE — ack without re-execution
                # (the 11.5a case surfacing through the transition guard).
                _log.info(TRANSITION_LOST_EVENT, job_id=message.job_id, target="running")
                await self._queue.delete(received.receipt_handle)
                return

            try:
                result = await self._execute_job(message)
            except JobExecutionError as exc:
                await self._fail_and_ack(job_id, received.receipt_handle, exc.error)
                return
            except Exception as exc:
                # Unexpected executor crash: structured, PII-free error —
                # the exception CLASS NAME only, never str(exc)
                # (`security.md`).
                detail = FailureDetail(trigger="error", detail=type(exc).__name__)
                await self._fail_and_ack(
                    job_id, received.receipt_handle, detail.model_dump(mode="json")
                )
                return

            await self._complete_and_ack(job_id, received.receipt_handle, result)

    # ---- terminal transitions: commit first, delete second (Req 11.4) -----

    async def _fail_and_ack(self, job_id: UUID, receipt_handle: str, error: dict[str, Any]) -> None:
        """Commit the ``failed`` transition, then acknowledge the message."""
        try:
            await self._store.mark_failed(job_id, error)
        except job_service.InvalidJobTransitionError:
            # Already terminal (concurrent worker) — the terminal state is
            # committed either way, so acknowledging is safe.
            _log.info(TRANSITION_LOST_EVENT, job_id=str(job_id), target="failed")
        except Exception as exc:
            # The terminal transition is NOT committed: keep the message so
            # SQS redelivery retries once the database recovers (Req 11.4).
            _log.error(
                TERMINAL_PERSIST_FAILED_EVENT,
                job_id=str(job_id),
                target="failed",
                reason=type(exc).__name__,
            )
            return
        await self._queue.delete(receipt_handle)

    async def _complete_and_ack(
        self, job_id: UUID, receipt_handle: str, result: dict[str, Any]
    ) -> None:
        """Commit the ``completed`` transition, then acknowledge the message."""
        try:
            await self._store.mark_completed(job_id, result)
        except job_service.InvalidJobTransitionError:
            _log.info(TRANSITION_LOST_EVENT, job_id=str(job_id), target="completed")
        except Exception as exc:
            _log.error(
                TERMINAL_PERSIST_FAILED_EVENT,
                job_id=str(job_id),
                target="completed",
                reason=type(exc).__name__,
            )
            return
        await self._queue.delete(receipt_handle)


# ---------------------------------------------------------------------------
# Production JobStore over the agent_jobs lifecycle service.
# ---------------------------------------------------------------------------


class DbJobStore:
    """:class:`JobStore` over ``services/agent_jobs/service.py``.

    Each ``mark_*`` opens a fresh short transaction and commits it before
    returning (the ``session.begin()`` context manager commits on clean
    exit) — the commit-before-return contract the consumer's
    delete-after-commit ordering relies on (Requirement 11.4). All
    mutations go through the lifecycle service's guarded transitions;
    only the read here touches the table directly.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def load(self, job_id: UUID) -> JobSnapshot | None:
        """Read one job by id (worker-trusted: not owner-scoped).

        Ownership was verified by the analyze endpoint before the job was
        enqueued; the worker loads context by identifier (Requirement
        11.2). A missing row means a poison message (Requirement 11.7).
        """
        async with self._session_factory() as session:
            result = await session.execute(select(AgentJob).where(AgentJob.id == job_id))
            job = result.scalar_one_or_none()
        if job is None:
            return None
        return JobSnapshot(
            id=job.id,
            status=job.status,
            attempts=job.attempts,
            match_id=job.match_id,
            user_id=job.user_id,
        )

    async def mark_running(self, job_id: UUID) -> None:
        """``queued|running → running`` (+1 attempt); committed on return."""
        async with self._session_factory() as session, session.begin():
            await job_service.mark_running(session, job_id=job_id)

    async def mark_completed(self, job_id: UUID, result: dict[str, Any]) -> None:
        """``running → completed`` with the AnalysisResult; committed."""
        async with self._session_factory() as session, session.begin():
            await job_service.mark_completed(session, job_id=job_id, result=result)

    async def mark_failed(self, job_id: UUID, error: dict[str, Any]) -> None:
        """``queued|running → failed`` with the structured error; committed."""
        async with self._session_factory() as session, session.begin():
            await job_service.mark_failed(session, job_id=job_id, error=error)


# ---------------------------------------------------------------------------
# Production executor: initial-state build + graph invocation.
# ---------------------------------------------------------------------------


class JobContextError(Exception):
    """Initial Agent_State could not be built (pre-invocation failure).

    ``reason`` is a fixed, operator-safe code — never resume or
    job-description content (`security.md`).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _SynthesizerFailureOutput(BaseModel):
    """Empty output document for a failed Synthesizer Agent_Run row.

    A Synthesizer failure propagates before any output exists
    (Requirement 7.6), so the persisted ``output_state_json`` is honestly
    empty — ``{}`` — while the row still records the input state, status
    ``failed``, and the structured failure reason (Requirement 12.7).
    """


@dataclass(frozen=True, slots=True)
class _JobContext:
    """Everything the graph composition needs for one job.

    ``resume_text`` and ``job_description`` are Restricted PII held in
    memory for the duration of one job only — they feed the job-scoped
    :class:`WorkerScorerAdapter` and never enter :class:`AgentState`
    (Requirement 1.3) or any log line.
    """

    match: MatchResult
    resume_text: str
    job_description: str
    initial_state: AgentState


def _terms(entries: list[Any]) -> list[str]:
    """Extract ``term`` strings from a persisted keyword JSONB list."""
    return [
        str(entry["term"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("term"), str)
    ]


def _suggestion_texts(entries: list[Any]) -> list[str]:
    """Extract ``text`` strings from a persisted suggestions JSONB list."""
    return [
        str(entry["text"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("text"), str)
    ]


def _numeric_breakdown(raw: dict[str, Any]) -> dict[str, float]:
    """Project the persisted score_breakdown onto its numeric-only shape.

    The Phase 2 breakdown carries a non-numeric ``similarity_method``
    marker; ``MatchSnapshot.breakdown`` is ``dict[str, float]``, so only
    numeric values project (``bool`` excluded — it is an ``int`` subtype).
    """
    return {
        key: float(value)
        for key, value in raw.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _libpq_conn_string(settings: Settings) -> str:
    """The database URL in libpq form for the LangGraph checkpointer.

    ``AsyncPostgresSaver.from_conn_string`` speaks psycopg/libpq URLs
    (``postgresql://…``), not SQLAlchemy driver-qualified ones — the same
    conversion migration 0006 performs for ``setup()``.
    """
    return str(settings.database_url).replace("+asyncpg", "", 1)


class GraphJobExecutor:
    """The production :data:`JobExecutor` — one full Agent_Graph run per job.

    Composed once at worker startup; each call executes one Agent_Job:
    build the initial :class:`AgentState` (design §6 step 4), compose the
    five agents with their job-scoped dependencies, invoke the compiled
    graph with ``thread_id = job_id`` under the best-effort checkpointer,
    and return the serialized ``AnalysisResult``. Raises
    :class:`JobExecutionError` on every failure path, with the
    Requirement 12.7 Synthesizer-row ordering already satisfied.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings if settings is not None else get_settings()

    async def __call__(self, message: JobMessage) -> dict[str, Any]:
        """Execute one Agent_Job; return the AnalysisResult JSON document."""
        job_id = UUID(message.job_id)
        async with self._session_factory() as session:
            # ---- step 4: initial state (pre-invocation, Req 1.8, 11.8) ----
            try:
                context = await self._build_context(session, message)
            except JobContextError as exc:
                detail = FailureDetail(
                    trigger="error",
                    detail=f"pre-invocation validation failed: {exc.reason}",
                )
                _log.warning(
                    "agent_job_context_build_failed",
                    job_id=message.job_id,
                    reason=exc.reason,
                )
                raise JobExecutionError(detail.model_dump(mode="json")) from exc
            except Exception as exc:
                # classify_failure records the exception class name only —
                # never str(exc), which could echo Restricted content.
                detail = classify_failure(exc)
                _log.warning(
                    "agent_job_context_build_failed",
                    job_id=message.job_id,
                    reason=detail.trigger,
                )
                raise JobExecutionError(detail.model_dump(mode="json")) from exc

            # ---- step 5: invoke the compiled graph ------------------------
            try:
                final_state = await self._invoke_graph(session, context, job_id)
            except Exception as exc:
                # Commit best-effort so LLM invocation-log rows staged on
                # this session survive the failure (they are append-only
                # audit data); a poisoned transaction rolls back instead.
                await self._commit_quietly(session)
                detail = classify_failure(exc)
                # Requirement 12.7: persist the Synthesizer's failed
                # Agent_Run row BEFORE the terminal transition the caller
                # records. Upstream rows were committed per invocation and
                # are retained. A persistence failure here propagates —
                # the message is then not deleted and SQS retries.
                await self._persist_synthesizer_failure(job_id, context.initial_state, detail)
                raise JobExecutionError(detail.model_dump(mode="json")) from exc

            # LLM results + invocation logs staged by the orchestrator.
            await session.commit()

        result = final_state.analysis_result
        if result is None:
            detail = FailureDetail(
                trigger="error",
                detail="graph completed without an AnalysisResult",
            )
            raise JobExecutionError(detail.model_dump(mode="json"))
        return result.model_dump(mode="json")

    # ---- context (design §6 step 4) ----------------------------------------

    async def _build_context(self, session: AsyncSession, message: JobMessage) -> _JobContext:
        """Load job context by identifier and build the initial AgentState.

        All resume-content processing happens here in the worker
        (Requirement 11.8): the PII_Redactor runs over ``extracted_text``
        and only the redacted form enters state; the Skill_Extractor runs
        over the Job_Description; the MatchSnapshot projects persisted
        Match_Result columns. Any failure is a pre-invocation validation
        failure — the caller fails the job and no node executes
        (Requirement 1.8).
        """
        match_id = UUID(message.match_id)
        user_id = UUID(message.user_id)

        match_row = await session.execute(
            select(MatchResult).where(MatchResult.id == match_id, MatchResult.user_id == user_id)
        )
        match = match_row.scalar_one_or_none()
        if match is None:
            raise JobContextError("match_result_not_found")

        resume_row = await session.execute(select(Resume).where(Resume.id == match.resume_id))
        resume = resume_row.scalar_one_or_none()
        if resume is None:
            raise JobContextError("resume_not_found")

        extracted_text = resume.extracted_text or ""
        # PII_Redactor (Phase 3) — deterministic, versioned. Empty text
        # skips redaction and yields None so the Resume_Analysis_Agent
        # takes its empty-input degraded path (zero provider calls).
        # A RedactionError propagates as a pre-invocation failure: with
        # redaction unavailable, no safe redacted text can exist.
        redacted_resume_text = (
            redact(extracted_text, kind="resume").text if extracted_text else None
        )

        job_description = match.job_description_text
        jd_skills = extract_job_description_skills(job_description, self._settings)

        snapshot = MatchSnapshot(
            score=float(match.score),
            breakdown=_numeric_breakdown(dict(match.score_breakdown)),
            scorer_version=match.scorer_version,
            matched_skills=_terms(list(match.matched_keywords)),
            missing_skills=_terms(list(match.missing_keywords)),
            suggestions=_suggestion_texts(list(match.suggestions)),
        )

        # AgentState construction IS the Pydantic validation gate of
        # Requirement 1.8 — a ValidationError propagates to the caller.
        initial_state = AgentState(
            job_id=message.job_id,
            match_id=message.match_id,
            user_id=message.user_id,
            redacted_resume_text=redacted_resume_text,
            job_description_skills=jd_skills,
            match_snapshot=snapshot,
        )
        return _JobContext(
            match=match,
            resume_text=extracted_text,
            job_description=job_description,
            initial_state=initial_state,
        )

    # ---- graph composition + invocation (design §6 step 5) -----------------

    async def _invoke_graph(
        self, session: AsyncSession, context: _JobContext, job_id: UUID
    ) -> AgentState:
        """Compose the five agents and run the compiled graph for one job."""
        settings = self._settings
        deps = AgentDeps(
            node_timeout_s=float(settings.agent_node_timeout_seconds),
            persist_agent_run=build_persist_agent_run(self._session_factory, job_id=job_id),
            tracer=trace.get_tracer(_TRACER_NAME),
            clock=time,
        )
        # A per-job Redis client via the one sanctioned factory —
        # core/redis.py is the only module that imports ``redis``
        # (phase-3 import boundary, decision D6); the generator's
        # teardown drains the pool when the job finishes.
        async with asynccontextmanager(get_redis_client)() as redis_client:
            orchestrator = MatchScopedOrchestrator(
                orchestrator=LLMOrchestrator(
                    session=session,
                    quota=DailyQuota(redis_client, limit=settings.llm_daily_quota),
                    cache=LLMCache(redis_client, ttl_seconds=settings.llm_cache_ttl_seconds),
                    breaker=get_spend_circuit_breaker(),
                    client_factory=build_llm_client,
                ),
                match=context.match,
                model=settings.llm_model,
            )
            scorer = WorkerScorerAdapter(
                resume_text=context.resume_text,
                job_description=context.job_description,
                settings=settings,
            )
            agents = build_agents(deps, orchestrator, scorer)
            config: RunnableConfig = {"configurable": {"thread_id": str(job_id)}}
            async with self._checkpointer() as checkpointer:
                graph = compile_graph(agents, checkpointer)
                raw_state = await graph.ainvoke(context.initial_state, config)
            return AgentState.model_validate(raw_state)

    @asynccontextmanager
    async def _checkpointer(self) -> AsyncIterator[BaseCheckpointSaver[str] | None]:
        """Yield the best-effort Postgres checkpointer, or ``None``.

        The saver's schema was created by the Alembic migration
        (Requirement 2.5 — no runtime DDL); checkpoints are keyed by
        ``thread_id = job_id`` (Requirement 2.2). Checkpointing is
        best-effort end to end (Requirement 2.4): a failure *constructing*
        the saver degrades to running without one — one structured
        warning, exception class name only — and write failures during
        the run are absorbed by :class:`BestEffortSaver`.
        """
        saver_cm = AsyncPostgresSaver.from_conn_string(_libpq_conn_string(self._settings))
        try:
            saver = await saver_cm.__aenter__()
        except Exception as exc:
            _log.warning("agent_checkpointer_unavailable", reason=type(exc).__name__)
            yield None
            return
        try:
            yield BestEffortSaver(saver)
        finally:
            await saver_cm.__aexit__(None, None, None)

    async def _persist_synthesizer_failure(
        self, job_id: UUID, input_state: AgentState, detail: FailureDetail
    ) -> None:
        """Persist and commit the Synthesizer's ``failed`` Agent_Run row.

        Requirement 12.7 ordering: this commits BEFORE the caller records
        the terminal ``failed`` Job_Status. The Synthesizer re-raises
        instead of degrading (Requirement 7.6), so its lifecycle never
        reached its own persistence step — the worker records the row
        directly. ``input_state`` is the job's initial state (the exact
        merged pre-Synthesizer state is not observable once the exception
        propagates; the committed upstream Agent_Run rows carry each
        branch's output). ``latency_ms`` is 0 because the node's own
        invocation window is unknown here. Persistence failures propagate
        (Agent_Run persistence is never best-effort): the message is then
        not acknowledged and SQS redelivery retries.
        """
        async with self._session_factory() as session, session.begin():
            await persist_agent_run(
                session,
                job_id=job_id,
                agent_name=SynthesizerAgent.name,
                input_state=input_state,
                output=_SynthesizerFailureOutput(),
                status="failed",
                failure_reason=detail,
                latency_ms=0,
            )

    @staticmethod
    async def _commit_quietly(session: AsyncSession) -> None:
        """Commit staged LLM audit rows; roll back a poisoned transaction."""
        try:
            await session.commit()
        except Exception as exc:
            _log.warning("agent_llm_log_commit_failed", reason=type(exc).__name__)
            await session.rollback()


# ---------------------------------------------------------------------------
# Process entrypoint — mirrors the API startup (design §6).
# ---------------------------------------------------------------------------


def _worker_settings() -> Settings:
    """The process Settings with the worker's OTel service name applied.

    When the operator left ``MATCHLAYER_OTEL_SERVICE_NAME`` at the API
    default, the worker substitutes ``matchlayer-worker`` so its spans
    are attributed to this process (design §9's per-process service
    name); an explicitly configured non-default value is respected.
    """
    settings = get_settings()
    if settings.otel_service_name == _API_DEFAULT_SERVICE_NAME:
        return settings.model_copy(update={"otel_service_name": _WORKER_SERVICE_NAME})
    return settings


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Configure the process and consume the Job_Queue until stopped.

    Startup mirrors the API's factory/lifespan step for step (design §6:
    "the same structlog JSON logging and OTel setup as the API"):

    1. :func:`configure_logging` — structlog JSON (non-development).
    2. :func:`configure_tracing` — no-op tracer without an exporter
       endpoint (Requirement 13.5), service name ``matchlayer-worker``.
    3. Fail-fast database probe (a worker that cannot reach Postgres
       cannot process any job — exit non-zero like the API lifespan).
    4. Phase 2 semantic-pipeline load — Degraded_Mode on failure; the
       scorer adapter then serves the Phase 1 engine.
    5. Phase 3 LLM availability — keyless startup serves fallbacks; a
       present-but-invalid key fails startup fast (same as the API).
    """
    settings = _worker_settings()
    configure_logging(settings)
    configure_tracing(settings)
    await verify_database_connection()
    load_semantic_pipeline()
    await initialize_llm_availability(settings)

    worker = AgentWorker(
        queue=get_job_queue(),
        store=DbJobStore(SessionLocal),
        execute_job=GraphJobExecutor(session_factory=SessionLocal, settings=settings),
        max_attempts=settings.agent_max_attempts,
    )
    _log.info(
        "agent_worker_started",
        environment=settings.environment,
        max_attempts=settings.agent_max_attempts,
    )
    await worker.run_forever(stop_event=stop_event)
    _log.info("agent_worker_stopping")


async def _run_with_signals() -> None:
    """Run the worker with SIGINT/SIGTERM wired to a graceful stop."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Suppress: signal handlers are unavailable on non-POSIX platforms.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await run_worker(stop)


def main() -> None:
    """Synchronous entrypoint: ``python -m matchlayer_api.workers.agent_worker``."""
    asyncio.run(_run_with_signals())


if __name__ == "__main__":
    main()
