"""Logic-level tests for ``services/agent_jobs/service.py`` (task 8.1).

Validates Requirements 10.5, 12.4, and 12.6 of ``phase-4-agentic`` at
the service-logic level using scripted fake sessions — no Postgres.
The database-backed behavior (real partial-unique-index violations,
guarded UPDATE row-matching, the outer join) is covered by the gated
integration suite in
``tests/integration/test_agent_job_service.py``.

Covered here:

* create-with-idempotency control flow (D5): violation → fetch existing
  → ``created=False``; foreign IntegrityErrors re-raised untouched;
  retry when the conflicting job went terminal between violation and
  fetch; bounded give-up.
* guarded transitions raise :class:`InvalidJobTransitionError` when the
  guarded UPDATE matches no row (missing job / terminal state).
* owner-scoped read shaping: indistinguishable ``NotFoundError`` on an
  empty result; NULL run rows from the outer join filtered out.
* the no-delete rule (Requirement 12.6): the module contains no delete
  code path.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from uuid_utils.compat import uuid7

from matchlayer_api.core.errors import NotFoundError
from matchlayer_api.db.models import AgentJob, AgentRun
from matchlayer_api.services.agent_jobs import service
from matchlayer_api.services.agent_jobs.service import (
    InvalidJobTransitionError,
    create_job,
    get_job_with_runs,
    mark_completed,
    mark_failed,
    mark_running,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResult:
    """Scripted stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, *, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        assert self._scalar is not None, "scalar_one on empty FakeResult"
        return self._scalar

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Scripted stand-in for ``AsyncSession``.

    ``flush_effects`` is consumed one entry per ``flush()`` call: an
    Exception instance is raised, anything else is a successful flush.
    ``execute_results`` is consumed one entry per ``execute()`` call.
    """

    def __init__(
        self,
        *,
        flush_effects: list[Any] | None = None,
        execute_results: list[FakeResult] | None = None,
    ) -> None:
        self._flush_effects = list(flush_effects or [])
        self._execute_results = list(execute_results or [])
        self.added: list[Any] = []
        self.execute_count = 0

    def begin_nested(self) -> Any:
        @asynccontextmanager
        async def _savepoint() -> AsyncIterator[None]:
            yield

        return _savepoint()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        if self._flush_effects:
            effect = self._flush_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect

    async def execute(self, _stmt: Any) -> FakeResult:
        self.execute_count += 1
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)


def _inflight_violation() -> IntegrityError:
    """An IntegrityError naming the partial unique index (D5)."""
    orig = Exception(
        'duplicate key value violates unique constraint "agent_jobs_match_user_inflight_uniq"'
    )
    return IntegrityError("INSERT INTO agent_jobs ...", None, orig)


def _fk_violation() -> IntegrityError:
    """An IntegrityError that is NOT the idempotency violation."""
    orig = Exception(
        'insert or update on table "agent_jobs" violates foreign key '
        'constraint "agent_jobs_match_id_fkey"'
    )
    return IntegrityError("INSERT INTO agent_jobs ...", None, orig)


def _job(status: str = "queued") -> AgentJob:
    return AgentJob(
        id=uuid7(),
        user_id=uuid7(),
        match_id=uuid7(),
        status=status,
        attempts=0,
    )


# ---------------------------------------------------------------------------
# create_job (Requirement 10.5, D5)
# ---------------------------------------------------------------------------


async def test_create_job_inserts_queued_job() -> None:
    session = FakeSession()
    user_id, match_id = uuid4(), uuid4()

    creation = await create_job(session, user_id=user_id, match_id=match_id)  # type: ignore[arg-type]

    assert creation.created is True
    assert creation.job.status == "queued"
    assert creation.job.attempts == 0
    assert creation.job.user_id == user_id
    assert creation.job.match_id == match_id
    assert creation.job.started_at is None
    assert creation.job.completed_at is None
    assert creation.job.result_json is None
    assert creation.job.error_json is None
    # UTC timestamptz discipline (Requirement 12.1).
    assert creation.job.created_at.tzinfo is UTC
    assert session.added == [creation.job]


async def test_create_job_returns_existing_on_inflight_violation() -> None:
    existing = _job(status="running")
    session = FakeSession(
        flush_effects=[_inflight_violation()],
        execute_results=[FakeResult(scalar=existing)],
    )

    creation = await create_job(session, user_id=uuid4(), match_id=uuid4())  # type: ignore[arg-type]

    assert creation.created is False
    assert creation.job is existing


async def test_create_job_reraises_foreign_integrity_errors() -> None:
    """A non-idempotency IntegrityError (FK violation) must propagate."""
    session = FakeSession(flush_effects=[_fk_violation()])

    with pytest.raises(IntegrityError):
        await create_job(session, user_id=uuid4(), match_id=uuid4())  # type: ignore[arg-type]

    # It never masqueraded as "job exists": no fetch was attempted.
    assert session.execute_count == 0


async def test_create_job_retries_when_conflicting_job_went_terminal() -> None:
    """Violation + empty fetch (prior job just went terminal) → retry insert."""
    session = FakeSession(
        flush_effects=[_inflight_violation(), None],
        execute_results=[FakeResult(scalar=None)],
    )

    creation = await create_job(session, user_id=uuid4(), match_id=uuid4())  # type: ignore[arg-type]

    assert creation.created is True
    assert len(session.added) == 2  # first attempt rolled back, second stuck


async def test_create_job_gives_up_after_bounded_retries() -> None:
    violations: list[Any] = [_inflight_violation()] * 5
    empties = [FakeResult(scalar=None)] * 5
    session = FakeSession(flush_effects=violations, execute_results=empties)

    with pytest.raises(IntegrityError):
        await create_job(session, user_id=uuid4(), match_id=uuid4())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Guarded transitions (Requirement 12.6)
# ---------------------------------------------------------------------------


async def test_mark_running_raises_when_guard_matches_no_row() -> None:
    session = FakeSession(execute_results=[FakeResult(scalar=None)])
    job_id = uuid4()

    with pytest.raises(InvalidJobTransitionError) as exc_info:
        await mark_running(session, job_id=job_id)  # type: ignore[arg-type]

    assert exc_info.value.job_id == job_id
    assert exc_info.value.target == "running"


async def test_mark_completed_raises_when_not_running() -> None:
    session = FakeSession(execute_results=[FakeResult(scalar=None)])

    with pytest.raises(InvalidJobTransitionError) as exc_info:
        await mark_completed(session, job_id=uuid4(), result={"ok": True})  # type: ignore[arg-type]

    assert exc_info.value.target == "completed"


async def test_mark_failed_raises_when_already_terminal() -> None:
    session = FakeSession(execute_results=[FakeResult(scalar=None)])

    with pytest.raises(InvalidJobTransitionError) as exc_info:
        await mark_failed(session, job_id=uuid4(), error={"trigger": "error"})  # type: ignore[arg-type]

    assert exc_info.value.target == "failed"


async def test_mark_running_returns_refreshed_job() -> None:
    refreshed = _job(status="running")
    session = FakeSession(
        execute_results=[
            FakeResult(scalar=refreshed.id),  # guarded UPDATE ... RETURNING id
            FakeResult(scalar=refreshed),  # populate_existing re-read
        ]
    )

    job = await mark_running(session, job_id=refreshed.id)  # type: ignore[arg-type]

    assert job is refreshed


# ---------------------------------------------------------------------------
# Owner-scoped reads (Requirements 10.4, 12.4)
# ---------------------------------------------------------------------------


async def test_get_job_with_runs_raises_not_found_on_empty_result() -> None:
    """Missing and other-owner ids collapse to the same NotFoundError."""
    session = FakeSession(execute_results=[FakeResult(rows=[])])

    with pytest.raises(NotFoundError):
        await get_job_with_runs(session, user_id=uuid4(), job_id=uuid4())  # type: ignore[arg-type]


async def test_get_job_with_runs_filters_the_null_outer_join_row() -> None:
    """A job with no runs yet returns an empty runs list, not [None]."""
    job = _job()
    session = FakeSession(execute_results=[FakeResult(rows=[(job, None)])])

    read = await get_job_with_runs(session, user_id=job.user_id, job_id=job.id)  # type: ignore[arg-type]

    assert read.job is job
    assert read.runs == []


async def test_get_job_with_runs_returns_all_run_rows() -> None:
    job = _job()
    run_a = AgentRun(
        id=uuid7(),
        job_id=job.id,
        agent_name="resume_analysis",
        input_state_json={},
        output_state_json={},
        latency_ms=10,
        status="completed",
    )
    run_b = AgentRun(
        id=uuid7(),
        job_id=job.id,
        agent_name="ats",
        input_state_json={},
        output_state_json={},
        latency_ms=5,
        status="degraded",
        failure_reason_json={"trigger": "timeout"},
    )
    session = FakeSession(execute_results=[FakeResult(rows=[(job, run_a), (job, run_b)])])

    read = await get_job_with_runs(session, user_id=job.user_id, job_id=job.id)  # type: ignore[arg-type]

    assert read.runs == [run_a, run_b]


# ---------------------------------------------------------------------------
# No delete code path (Requirement 12.6)
# ---------------------------------------------------------------------------


def test_module_has_no_delete_code_path() -> None:
    """No Phase 4 code path deletes jobs or runs (Requirement 12.6)."""
    source = inspect.getsource(service)
    # No delete call of any kind (session.delete, bulk delete(), etc.).
    assert "delete(" not in source
    # SQLAlchemy's bulk-delete construct is never imported.
    import_line = next(
        line for line in source.splitlines() if line.startswith("from sqlalchemy import")
    )
    assert "delete" not in import_line
    assert not any("delete" in name for name in service.__all__)
