"""Database-backed tests for ``services/agent_jobs/service.py`` (task 8.1).

Validates Requirements 10.5, 12.1, 12.4, and 12.6 of ``phase-4-agentic``
against the docker-compose Postgres (gated skip when unreachable,
mirroring the sibling migration tests):

* create-with-idempotency backed by the real partial unique index
  ``agent_jobs_match_user_inflight_uniq`` (D5): a second create for the
  same (match, user) returns the existing non-terminal job; a terminal
  prior job unblocks a fresh creation.
* guarded transitions: ``queued → running`` sets ``started_at`` and
  counts the attempt; redelivery re-``running`` increments ``attempts``
  and preserves ``started_at``; terminal states reject every further
  transition; ``queued → failed`` (the D6 enqueue-failure compensation)
  is permitted.
* owner-scoped reads joining ``agent_runs``: runs returned in
  invocation order; other-owner and missing ids raise the same
  ``NotFoundError`` (Requirements 10.4, 12.4).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.core.errors import NotFoundError
from matchlayer_api.db.models import AgentRun
from matchlayer_api.services.agent_jobs.service import (
    InvalidJobTransitionError,
    create_job,
    get_inflight_job,
    get_job_with_runs,
    mark_completed,
    mark_failed,
    mark_running,
)

from .conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres not available (docker-compose not running)",
)


# ---------------------------------------------------------------------------
# Create-with-idempotency (Requirement 10.5, D5)
# ---------------------------------------------------------------------------


async def test_create_job_then_reuse_inflight(
    db_session: AsyncSession, factory_user, factory_match
) -> None:
    user = await factory_user()
    match = await factory_match(user_id=user.id)

    first = await create_job(db_session, user_id=user.id, match_id=match.id)
    assert first.created is True
    assert first.job.status == "queued"
    assert first.job.attempts == 0

    second = await create_job(db_session, user_id=user.id, match_id=match.id)
    assert second.created is False
    assert second.job.id == first.job.id

    inflight = await get_inflight_job(db_session, user_id=user.id, match_id=match.id)
    assert inflight is not None and inflight.id == first.job.id


async def test_terminal_job_unblocks_fresh_creation(
    db_session: AsyncSession, factory_user, factory_match
) -> None:
    """A terminal prior job means a new analyze creates a fresh job (10.5)."""
    user = await factory_user()
    match = await factory_match(user_id=user.id)

    first = await create_job(db_session, user_id=user.id, match_id=match.id)
    # queued → failed: the D6 enqueue-failure compensation path.
    failed = await mark_failed(
        db_session, job_id=first.job.id, error={"trigger": "error", "detail": "enqueue failed"}
    )
    assert failed.status == "failed"
    assert failed.completed_at is not None
    assert failed.error_json == {"trigger": "error", "detail": "enqueue failed"}

    second = await create_job(db_session, user_id=user.id, match_id=match.id)
    assert second.created is True
    assert second.job.id != first.job.id


# ---------------------------------------------------------------------------
# Guarded transitions (Requirements 12.1, 12.6, 11.5)
# ---------------------------------------------------------------------------


async def test_running_transition_counts_attempts_and_pins_started_at(
    db_session: AsyncSession, factory_user, factory_match
) -> None:
    user = await factory_user()
    match = await factory_match(user_id=user.id)
    creation = await create_job(db_session, user_id=user.id, match_id=match.id)

    running = await mark_running(db_session, job_id=creation.job.id)
    assert running.status == "running"
    assert running.attempts == 1
    assert running.started_at is not None
    first_started_at = running.started_at

    # SQS redelivery of an in-flight job (11.5b): attempts increments,
    # started_at is pinned to the first attempt (coalesce).
    redelivered = await mark_running(db_session, job_id=creation.job.id)
    assert redelivered.attempts == 2
    assert redelivered.started_at == first_started_at


async def test_completed_lifecycle_and_terminal_guards(
    db_session: AsyncSession, factory_user, factory_match
) -> None:
    user = await factory_user()
    match = await factory_match(user_id=user.id)
    creation = await create_job(db_session, user_id=user.id, match_id=match.id)

    # queued → completed is not a legal transition (12.6 state machine).
    with pytest.raises(InvalidJobTransitionError):
        await mark_completed(db_session, job_id=creation.job.id, result={"ok": True})

    await mark_running(db_session, job_id=creation.job.id)
    completed = await mark_completed(db_session, job_id=creation.job.id, result={"ok": True})
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.result_json == {"ok": True}
    assert completed.error_json is None  # null unless failed (12.1)

    # Terminal: every further transition rejects (12.6; redelivery 11.5a).
    with pytest.raises(InvalidJobTransitionError):
        await mark_running(db_session, job_id=creation.job.id)
    with pytest.raises(InvalidJobTransitionError):
        await mark_failed(db_session, job_id=creation.job.id, error={"trigger": "error"})
    with pytest.raises(InvalidJobTransitionError):
        await mark_completed(db_session, job_id=creation.job.id, result={"again": True})


# ---------------------------------------------------------------------------
# Owner-scoped reads joining agent_runs (Requirements 10.4, 12.4)
# ---------------------------------------------------------------------------


async def test_get_job_with_runs_owner_scoped(
    db_session: AsyncSession, factory_user, factory_match
) -> None:
    owner = await factory_user()
    other = await factory_user()
    match = await factory_match(user_id=owner.id)
    creation = await create_job(db_session, user_id=owner.id, match_id=match.id)

    # No runs yet: empty list, not an error (still-queued jobs read fine).
    read = await get_job_with_runs(db_session, user_id=owner.id, job_id=creation.job.id)
    assert read.job.id == creation.job.id
    assert read.runs == []

    run_a = AgentRun(
        id=uuid7(),
        job_id=creation.job.id,
        agent_name="resume_analysis",
        input_state_json={"job_id": str(creation.job.id)},
        output_state_json={"skills": []},
        latency_ms=42,
        status="completed",
    )
    run_b = AgentRun(
        id=uuid7(),
        job_id=creation.job.id,
        agent_name="ats",
        input_state_json={"job_id": str(creation.job.id)},
        output_state_json={"score": 72.0},
        latency_ms=7,
        status="degraded",
        failure_reason_json={"trigger": "timeout"},
    )
    db_session.add_all([run_a, run_b])
    await db_session.flush()

    read = await get_job_with_runs(db_session, user_id=owner.id, job_id=creation.job.id)
    assert [run.agent_name for run in read.runs] == ["resume_analysis", "ats"]
    assert [run.status for run in read.runs] == ["completed", "degraded"]

    # Other-owner and missing ids raise the SAME NotFoundError (10.4, 12.4).
    with pytest.raises(NotFoundError):
        await get_job_with_runs(db_session, user_id=other.id, job_id=creation.job.id)
    with pytest.raises(NotFoundError):
        await get_job_with_runs(db_session, user_id=owner.id, job_id=uuid7())


# ---------------------------------------------------------------------------
# Live concurrency: the partial unique index under real concurrent
# transactions (phase-4-agentic task 11.7 / Property 15, Requirement 10.5).
#
# The fake-driven interleaving property lives in
# ``tests/property/test_inflight_job_idempotency.py``; this docker-gated
# companion exercises the DB-level guarantee itself: N concurrent
# ``create_job`` calls on independent sessions/connections, where the
# blocked inserts resolve their unique violation against the committed
# winner.
# ---------------------------------------------------------------------------


async def test_concurrent_creates_share_one_inflight_job_under_real_index(
    db_session: AsyncSession, factory_user, factory_match
) -> None:
    """Property 15's live half: the index is the arbiter under concurrency.

    Six concurrent ``create_job`` calls — each on its own session and
    connection, each committing — must produce exactly one inserted
    ``queued`` row, with every caller returning that job's id; and a
    terminal transition unblocks a fresh creation (Requirement 10.5).
    """
    import asyncio

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from matchlayer_api.config import get_settings
    from matchlayer_api.db.models import AgentJob

    # The concurrent sessions run in independent transactions, so the
    # user + match rows must be COMMITTED (the per-test ``db_session``
    # only flushes). The autouse ``_truncate_auth_tables`` fixture wipes
    # ``users`` CASCADE before every integration test, so these
    # committed rows cannot leak state into later tests.
    user = await factory_user()
    match = await factory_match(user_id=user.id)
    await db_session.commit()

    engine = create_async_engine(
        str(get_settings().database_url),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _caller() -> tuple[str, bool]:
        async with session_factory() as session:
            creation = await create_job(session, user_id=user.id, match_id=match.id)
            await session.commit()
            return str(creation.job.id), creation.created

    try:
        results = await asyncio.gather(*(_caller() for _ in range(6)))

        ids = {job_id for job_id, _ in results}
        created_flags = [created for _, created in results]
        assert len(ids) == 1, f"every caller must receive the same job id; got {ids}"
        assert created_flags.count(True) == 1, (
            f"exactly one concurrent caller may insert; got {created_flags}"
        )

        # Exactly one non-terminal row exists for the pair — the partial
        # unique index invariant, checked against the committed table.
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(AgentJob)
                .where(
                    AgentJob.match_id == match.id,
                    AgentJob.user_id == user.id,
                    AgentJob.status.in_(("queued", "running")),
                )
            )
            assert count == 1

        # Terminal → a subsequent request creates a FRESH job (the second
        # clause of Property 15).
        async with session_factory() as session:
            await mark_failed(
                session,
                job_id=UUID(next(iter(ids))),
                error={"type": "job_failed", "detail": "test terminalization"},
            )
            await session.commit()

        fresh_id, fresh_created = await _caller()
        assert fresh_created is True
        assert fresh_id not in ids
    finally:
        await engine.dispose()
