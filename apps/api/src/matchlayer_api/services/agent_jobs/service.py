"""Agent_Job lifecycle service — create, transition, and read jobs.

This is the ONLY module in the API that mutates ``agent_jobs`` rows
(single-writer discipline, mirroring the model docstring in
``db/models.py``). It owns three concerns:

* **Create-with-idempotency** (:func:`create_job`) — Requirement 10.5,
  design decision D5. The insert relies on the **partial unique index**
  ``agent_jobs_match_user_inflight_uniq`` (migration ``0005``) for the
  database-level guarantee that at most one non-terminal Agent_Job
  exists per (Match_Result, User_Account) pair, even under concurrent
  requests. An application-level check-then-insert would race; the
  index cannot. On a unique violation the existing non-terminal job is
  fetched and returned with ``created=False`` so the analyze endpoint
  can respond ``202`` with that job's id and — critically — skip the
  enqueue (only a freshly created job is enqueued).

* **Guarded status transitions** — Requirements 12.6 and 11.5.
  The Job_Status state machine::

      queued ──→ running ──→ completed
        │           │
        └───────────┴──────→ failed

  ``queued → failed`` exists for the enqueue-failure compensation of
  design decision D6 (persist job → commit → enqueue; enqueue failure
  transitions the job to ``failed`` so no orphaned ``queued`` row
  remains and the partial unique index unblocks a retry, Requirement
  11.6). Every transition is an atomic guarded ``UPDATE ... WHERE
  status IN (...)`` so a concurrent transition (e.g. SQS redelivery
  racing two workers, Requirement 11.5) loses cleanly with an
  :class:`InvalidJobTransitionError` instead of overwriting a terminal
  state. The only columns any transition touches are ``status``, its
  associated timestamps (``started_at``/``completed_at``), the
  ``attempts`` redelivery counter (part of the ``running`` transition
  per Requirement 11.5), and ``result_json``/``error_json`` — nothing
  else on the row mutates after creation (Requirement 12.6).

* **Owner-scoped reads** (:func:`get_job_with_runs`) — Requirements
  10.4 and 12.4. The read joins ``agent_runs`` so the jobs router can
  derive per-agent step statuses from run rows alone. The lookup is
  scoped by ``user_id``; a missing job and an other-owner job collapse
  to the same :class:`~matchlayer_api.core.errors.NotFoundError`, so
  the existence of another account's job is never disclosed.

**No delete.** No function in this module (or anywhere in Phase 4)
deletes ``agent_jobs`` or ``agent_runs`` rows — they are retained for
Phase 5 evaluation consumption (Requirement 12.6).

**PII discipline.** ``error_json`` and ``result_json`` are persisted
verbatim; callers are responsible for passing structured, PII-free
content (the worker builds errors from the closed ``FailureDetail``
vocabulary; the ``AnalysisResult`` carries redacted/derived content by
construction). Log lines here reference jobs by id only.

**Transaction boundary.** Like every service in this package tree, the
functions stage work on the caller's :class:`AsyncSession` and never
commit — the router (API paths) or the worker's message loop own the
commit, which matters for Requirement 11.4's "delete the message only
after the terminal transition is committed" ordering.

Design reference: phase-4-agentic design §7 and §8; decisions D5, D6.
Requirements covered: 10.5, 12.1, 12.4, 12.6.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast
from uuid import UUID

import structlog
from sqlalchemy import Row, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.core.errors import NotFoundError
from matchlayer_api.db.models import AgentJob, AgentRun

__all__ = [
    "INFLIGHT_STATUSES",
    "InvalidJobTransitionError",
    "JobCreation",
    "JobWithRuns",
    "create_job",
    "get_inflight_job",
    "get_job_with_runs",
    "mark_completed",
    "mark_failed",
    "mark_running",
]

_log = structlog.get_logger(__name__)

#: The two non-terminal Job_Status values — exactly the predicate of the
#: partial unique index ``agent_jobs_match_user_inflight_uniq``
#: (``WHERE status IN ('queued', 'running')``, migration 0005 / D5).
INFLIGHT_STATUSES: Final[tuple[str, str]] = ("queued", "running")

# The partial unique index backing create-with-idempotency. Matched by
# name against the driver error text so a unique violation from THIS
# index is distinguishable from any other IntegrityError (e.g. an FK
# violation), which must propagate rather than masquerade as "job
# already exists".
_INFLIGHT_INDEX_NAME: Final[str] = "agent_jobs_match_user_inflight_uniq"

# Bounded retry for the insert → violation → fetch loop. More than one
# retry is only reachable in the vanishingly narrow race where the
# conflicting job reaches a terminal status between our violation and
# our fetch; 3 attempts is comfortable headroom.
_CREATE_MAX_ATTEMPTS: Final[int] = 3


def _now() -> datetime:
    """Return a timezone-aware "now" in UTC.

    Centralised (mirroring ``services/resumes.py``) so one UTC clock
    drives ``created_at``, ``started_at``, and ``completed_at`` — all
    stored as UTC timestamptz per Requirement 12.1 and rendered with
    the ``Z`` suffix by the router per ``conventions.md``. Tests freeze
    time by monkey-patching ``services.agent_jobs.service._now``.
    """
    return datetime.now(UTC)


class InvalidJobTransitionError(Exception):
    """A guarded Job_Status transition matched no row.

    Raised when the atomic ``UPDATE ... WHERE status IN (<allowed>)``
    affected zero rows: the job does not exist, or its current status
    is outside the transition's allowed source states (for example a
    redelivered message racing a worker that already recorded the
    terminal state, Requirement 11.5a). The message carries the job id
    and the attempted target only — ids are Internal, never PII.
    """

    def __init__(self, *, job_id: UUID, target: str) -> None:
        super().__init__(f"Job {job_id} could not transition to '{target}'.")
        self.job_id = job_id
        self.target = target


@dataclass(frozen=True, slots=True)
class JobCreation:
    """Result of :func:`create_job`.

    Attributes:
        job: The Agent_Job to respond with — freshly inserted, or the
            existing non-terminal job for the (match, user) pair.
        created: ``True`` iff a new row was inserted. The analyze
            endpoint enqueues a Job_Queue message ONLY when this is
            ``True``; returning an existing job must not enqueue a
            duplicate (Requirement 10.5).
    """

    job: AgentJob
    created: bool


@dataclass(frozen=True, slots=True)
class JobWithRuns:
    """One owner-scoped Agent_Job plus its Agent_Run rows.

    Attributes:
        job: The owned Agent_Job.
        runs: The job's ``agent_runs`` rows ordered by ``created_at``
            (ties broken by ``id``) — the source from which the jobs
            router derives per-agent step statuses (``pending`` for an
            agent with no row yet, else the row's status; Requirement
            10.2 / design §7).
    """

    job: AgentJob
    runs: list[AgentRun]


# ---------------------------------------------------------------------------
# Create-with-idempotency (Requirement 10.5, decision D5).
# ---------------------------------------------------------------------------


def _is_inflight_violation(exc: IntegrityError) -> bool:
    """Return whether *exc* is a violation of the in-flight unique index.

    Postgres embeds the violated constraint/index name in the driver
    error message (asyncpg: ``duplicate key value violates unique
    constraint "agent_jobs_match_user_inflight_uniq"``), so a name
    match is the portable way to distinguish the idempotency violation
    from any other integrity error without depending on asyncpg's
    exception hierarchy.
    """
    return _INFLIGHT_INDEX_NAME in str(exc.orig)


async def get_inflight_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_id: UUID,
) -> AgentJob | None:
    """Return the non-terminal Agent_Job for (match, user), if any.

    Backed by the ``agent_jobs_match_user_status_idx`` index; at most
    one row can match because the partial unique index enforces the
    at-most-one-in-flight invariant (Requirement 10.5, D5) —
    ``scalar_one_or_none`` would raise if that invariant were ever
    broken, which is the correct loud failure.
    """
    result = await session.execute(
        select(AgentJob).where(
            AgentJob.match_id == match_id,
            AgentJob.user_id == user_id,
            AgentJob.status.in_(INFLIGHT_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def create_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_id: UUID,
) -> JobCreation:
    """Create a ``queued`` Agent_Job, or return the existing in-flight one.

    Insert-first (never check-then-insert): the partial unique index is
    the arbiter under concurrency (D5). The insert is flushed inside a
    SAVEPOINT (``begin_nested``) so a unique violation rolls back only
    the attempted insert and leaves the caller's outer transaction
    healthy for the fetch-and-return path — the same savepoint
    rationale as ``Resume_Service._embed_resume_best_effort``.

    Ownership note: this function does NOT verify that *match_id*
    exists or is owned by *user_id* — the analyze router resolves the
    owned Match_Result first (indistinguishable 404, Requirement 10.4)
    and only then calls this. A dangling *match_id* surfaces as an FK
    ``IntegrityError``, which propagates (it is not an idempotency
    violation).

    Args:
        session: The caller's active session; work is staged, never
            committed here.
        user_id: The owning User_Account id.
        match_id: The subject Match_Result id.

    Returns:
        A :class:`JobCreation` — ``created=True`` with the fresh
        ``queued`` job, or ``created=False`` with the existing
        non-terminal job (Requirement 10.5: respond 202 with that
        job's id, do not enqueue a duplicate).

    Raises:
        IntegrityError: A non-idempotency integrity failure (e.g. FK
            violation), or — theoretical — the retry bound exhausting
            while the in-flight row keeps vanishing between violation
            and fetch.
    """
    last_violation: IntegrityError | None = None
    for _ in range(_CREATE_MAX_ATTEMPTS):
        job = AgentJob(
            id=uuid7(),
            user_id=user_id,
            match_id=match_id,
            status="queued",
            attempts=0,
            created_at=_now(),
            started_at=None,
            completed_at=None,
            result_json=None,
            error_json=None,
        )
        try:
            async with session.begin_nested():
                session.add(job)
                await session.flush()
        except IntegrityError as exc:
            if not _is_inflight_violation(exc):
                raise
            last_violation = exc
            existing = await get_inflight_job(session, user_id=user_id, match_id=match_id)
            if existing is not None:
                _log.info(
                    "agent_job_inflight_reuse",
                    job_id=str(existing.id),
                    match_id=str(match_id),
                )
                return JobCreation(job=existing, created=False)
            # The conflicting job reached a terminal status between our
            # violation and our fetch — a fresh job is now legal; retry
            # the insert (Requirement 10.5: a terminal prior job means a
            # new analyze request creates a fresh Agent_Job).
            continue
        return JobCreation(job=job, created=True)

    assert last_violation is not None  # loop exits only via the except path
    raise last_violation


# ---------------------------------------------------------------------------
# Guarded status transitions (Requirements 12.6, 11.5; decisions D5, D6).
# ---------------------------------------------------------------------------


async def _refresh_job(session: AsyncSession, job_id: UUID) -> AgentJob:
    """Re-read *job_id* with ``populate_existing`` after a bulk UPDATE.

    The guarded transitions below run as core ``UPDATE`` statements that
    bypass ORM instance state, so any already-loaded ``AgentJob`` in the
    identity map would be stale. ``populate_existing=True`` forces the
    refreshed column values onto that instance.
    """
    result = await session.execute(
        select(AgentJob).where(AgentJob.id == job_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def mark_running(session: AsyncSession, *, job_id: UUID) -> AgentJob:
    """Transition *job_id* to ``running`` and count the delivery attempt.

    Allowed source states: ``queued`` (first delivery) and ``running``
    (SQS redelivery of an in-flight job below the attempt cap —
    Requirement 11.5b; the worker checks ``attempts`` against
    ``MATCHLAYER_AGENT_MAX_ATTEMPTS`` before calling). Effects, atomic
    in one guarded UPDATE:

    * ``status = 'running'``;
    * ``attempts = attempts + 1`` (the persisted redelivery counter,
      Requirement 11.5);
    * ``started_at = coalesce(started_at, now)`` — set on the first
      attempt only, so Requirement 14.1's ``started_at → completed_at``
      measurement spans the job's whole execution window.

    Returns:
        The refreshed Agent_Job.

    Raises:
        InvalidJobTransitionError: The job does not exist or is already
            terminal (redelivery case 11.5a — the caller acks without
            re-execution).
    """
    result = await session.execute(
        update(AgentJob)
        .where(AgentJob.id == job_id, AgentJob.status.in_(INFLIGHT_STATUSES))
        .values(
            status="running",
            attempts=AgentJob.attempts + 1,
            started_at=func.coalesce(AgentJob.started_at, _now()),
        )
        .returning(AgentJob.id)
    )
    if result.scalar_one_or_none() is None:
        raise InvalidJobTransitionError(job_id=job_id, target="running")
    return await _refresh_job(session, job_id)


async def mark_completed(
    session: AsyncSession,
    *,
    job_id: UUID,
    result: dict[str, Any],  # Any: the AnalysisResult JSON document (JSONB column shape).
) -> AgentJob:
    """Transition *job_id* from ``running`` to the terminal ``completed``.

    Sets ``completed_at`` and persists *result* (the serialized
    ``AnalysisResult`` — redacted/derived content by construction) into
    ``result_json``, which is set iff the job completes (Requirement
    12.1). ``completed`` is terminal: no further transition can touch
    the row (every guard excludes terminal states).

    Raises:
        InvalidJobTransitionError: The job does not exist or is not
            currently ``running`` (a ``queued`` job cannot complete
            without having run).
    """
    row = await session.execute(
        update(AgentJob)
        .where(AgentJob.id == job_id, AgentJob.status == "running")
        .values(status="completed", completed_at=_now(), result_json=result)
        .returning(AgentJob.id)
    )
    if row.scalar_one_or_none() is None:
        raise InvalidJobTransitionError(job_id=job_id, target="completed")
    return await _refresh_job(session, job_id)


async def mark_failed(
    session: AsyncSession,
    *,
    job_id: UUID,
    error: dict[str, Any],  # Any: structured PII-free error JSON (JSONB column shape).
) -> AgentJob:
    """Transition *job_id* to the terminal ``failed``.

    Allowed source states — both non-terminal statuses:

    * ``queued`` — the enqueue-failure compensation of decision D6 (the
      analyze endpoint persisted+committed the job, the Job_Queue send
      failed, and an orphaned ``queued`` row is forbidden by
      Requirement 11.6) and the extra breadth over the task's shorthand
      ``queued→running→completed|failed`` is exactly that D6 path;
    * ``running`` — graph/Synthesizer failure, pre-invocation
      validation failure, or the redelivery attempt cap (Requirement
      11.5c).

    Sets ``completed_at`` (the terminal timestamp) and persists *error*
    into ``error_json`` — the structured, PII-free, display-safe error
    that ``GET /jobs/{id}`` returns when failed (Requirements 10.3,
    12.1: error is null unless ``failed``). The caller builds *error*
    from the closed ``FailureDetail`` vocabulary; nothing here inspects
    or logs its content.

    Raises:
        InvalidJobTransitionError: The job does not exist or is already
            terminal.
    """
    row = await session.execute(
        update(AgentJob)
        .where(AgentJob.id == job_id, AgentJob.status.in_(INFLIGHT_STATUSES))
        .values(status="failed", completed_at=_now(), error_json=error)
        .returning(AgentJob.id)
    )
    if row.scalar_one_or_none() is None:
        raise InvalidJobTransitionError(job_id=job_id, target="failed")
    return await _refresh_job(session, job_id)


# ---------------------------------------------------------------------------
# Owner-scoped reads (Requirements 10.4, 12.4).
# ---------------------------------------------------------------------------


async def get_job_with_runs(
    session: AsyncSession,
    *,
    user_id: UUID,
    job_id: UUID,
) -> JobWithRuns:
    """Return the caller's Agent_Job joined with its Agent_Run rows.

    One ``LEFT OUTER JOIN`` query scoped by ``user_id`` (backed by the
    ``agent_jobs_user_id_idx`` and ``agent_runs_job_id_idx`` indexes):
    a job with no runs yet (still ``queued``) returns with an empty
    ``runs`` list, from which the router derives all-``pending`` step
    statuses (design §7). Runs are ordered ``created_at`` ascending
    (ties by ``id`` — UUIDv7, so time-ordered) matching invocation
    order.

    A missing id and an id owned by a different User_Account raise the
    same :class:`NotFoundError`, so ownership is never disclosed
    (Requirements 10.4, 12.4).

    Raises:
        NotFoundError: The job does not exist or is owned by another
            User_Account (404, indistinguishable).
    """
    result = await session.execute(
        select(AgentJob, AgentRun)
        .outerjoin(AgentRun, AgentRun.job_id == AgentJob.id)
        .where(AgentJob.id == job_id, AgentJob.user_id == user_id)
        .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
    )
    # The outer join makes the AgentRun side nullable at runtime; the
    # cast records that truth (SQLAlchemy types the select as if both
    # entities were always present).
    rows = cast("Sequence[Row[tuple[AgentJob, AgentRun | None]]]", result.all())
    if not rows:
        raise NotFoundError("Job not found.")
    job = rows[0][0]
    runs = [row[1] for row in rows if row[1] is not None]
    return JobWithRuns(job=job, runs=runs)
