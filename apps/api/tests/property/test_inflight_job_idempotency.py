"""Feature: phase-4-agentic — Property 15.

# Feature: phase-4-agentic, Property 15: In-flight job idempotency under concurrency

Property 15: In-flight job idempotency under concurrency.

    *For any* number of concurrent ``POST /matches/{id}/analyze``
    requests for the same (Match_Result, User_Account), at most one
    non-terminal Agent_Job exists afterward and every 202 response
    carries that job's id; and once that job reaches a terminal status,
    a subsequent request creates a fresh job.

**Validates: Requirements 10.5**

What is driven, and how
-----------------------
The unit under test is the real
:func:`~matchlayer_api.services.agent_jobs.service.create_job` — the
create-with-idempotency path every analyze request funnels through
(design decision D5). The database-level arbiter is the **partial unique
index** ``agent_jobs_match_user_inflight_uniq``; with no live Postgres
available in this suite's default environment, the property tests the
service's violation-handling logic against a scripted store that
reproduces the index's exact semantics **atomically at flush time**
(the check-and-install runs synchronously, with no awaits inside — the
in-process analogue of the index's atomicity), raising an
:class:`~sqlalchemy.exc.IntegrityError` naming the index whenever a
non-terminal job already exists for the (match, user) pair. The
matching **live-Postgres concurrency test** (docker-gated) lives in
``tests/integration/test_agent_job_service.py`` and exercises the real
index under real concurrent transactions.

Hypothesis quantifies over the *interleaving*: the number of concurrent
callers (2..6) and, per caller, generated cooperative-yield counts
inserted before the flush (the insert attempt) and before the fetch of
the existing job — so callers overtake each other at every seam the
real async path has. All callers run concurrently under
``asyncio.gather`` on one event loop.

Assertions per example:

* every caller receives the **same** Agent_Job id;
* exactly one caller observed ``created=True`` (the single insert);
* the store holds exactly one non-terminal job for the pair afterward
  — the partial-unique-index invariant;
* after that job is transitioned to a terminal status, a subsequent
  ``create_job`` inserts a **fresh** job with a different id (the
  second clause of the property), leaving exactly one non-terminal job
  again.
"""

# Feature: phase-4-agentic, Property 15: In-flight job idempotency under
# concurrency

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.exc import IntegrityError

from matchlayer_api.db.models import AgentJob
from matchlayer_api.services.agent_jobs.service import (
    INFLIGHT_STATUSES,
    JobCreation,
    create_job,
)

_INDEX_NAME = "agent_jobs_match_user_inflight_uniq"


# ---------------------------------------------------------------------------
# The scripted store: the partial unique index's semantics, atomic at flush.
# ---------------------------------------------------------------------------


class _InflightStore:
    """In-memory ``agent_jobs`` table enforcing the partial unique index.

    ``try_insert`` is the atomic check-and-install: it runs synchronously
    end-to-end (no awaits between the check and the write), mirroring the
    atomicity the database index guarantees the concurrent inserts —
    under a single event loop, no two callers can interleave inside it.
    """

    def __init__(self) -> None:
        self.jobs: list[AgentJob] = []

    def _inflight(self, *, match_id: UUID, user_id: UUID) -> AgentJob | None:
        for job in self.jobs:
            if (
                job.match_id == match_id
                and job.user_id == user_id
                and job.status in INFLIGHT_STATUSES
            ):
                return job
        return None

    def try_insert(self, job: AgentJob) -> None:
        if self._inflight(match_id=job.match_id, user_id=job.user_id) is not None:
            orig = Exception(f'duplicate key value violates unique constraint "{_INDEX_NAME}"')
            raise IntegrityError("INSERT INTO agent_jobs ...", None, orig)
        self.jobs.append(job)

    def inflight_jobs(self, *, match_id: UUID, user_id: UUID) -> list[AgentJob]:
        return [
            job
            for job in self.jobs
            if job.match_id == match_id
            and job.user_id == user_id
            and job.status in INFLIGHT_STATUSES
        ]


class _FakeResult:
    def __init__(self, scalar: Any) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _ConcurrentFakeSession:
    """One caller's session over the shared store, with scripted yields.

    ``yields_before_flush`` / ``yields_before_fetch`` insert cooperative
    ``asyncio.sleep(0)`` yield points before the atomic insert attempt
    and before the existing-job fetch, so Hypothesis controls how the
    concurrent callers overtake each other at the two seams the real
    async path has.
    """

    def __init__(
        self,
        store: _InflightStore,
        *,
        match_id: UUID,
        user_id: UUID,
        yields_before_flush: int,
        yields_before_fetch: int,
    ) -> None:
        self._store = store
        self._match_id = match_id
        self._user_id = user_id
        self._yields_before_flush = yields_before_flush
        self._yields_before_fetch = yields_before_fetch
        self._pending: list[AgentJob] = []

    def begin_nested(self) -> Any:
        @asynccontextmanager
        async def _savepoint() -> AsyncIterator[None]:
            try:
                yield
            except IntegrityError:
                # SAVEPOINT rollback: the attempted insert is discarded.
                self._pending.clear()
                raise

        return _savepoint()

    def add(self, job: AgentJob) -> None:
        self._pending.append(job)

    async def flush(self) -> None:
        for _ in range(self._yields_before_flush):
            await asyncio.sleep(0)
        # Atomic from here: check + install with no awaits in between.
        for job in list(self._pending):
            self._store.try_insert(job)
        self._pending.clear()

    async def execute(self, _stmt: Any) -> _FakeResult:
        # The only SELECT on create_job's path is get_inflight_job for
        # the single (match, user) pair each example uses; the answer is
        # computed from the live store AT FETCH TIME, so overtaking
        # callers observe exactly what a fresh statement would.
        for _ in range(self._yields_before_fetch):
            await asyncio.sleep(0)
        return _FakeResult(self._store._inflight(match_id=self._match_id, user_id=self._user_id))


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Strategies: caller count and per-caller yield scripts.
# ---------------------------------------------------------------------------

_yield_count = st.integers(min_value=0, max_value=3)

_interleavings = st.integers(min_value=2, max_value=6).flatmap(
    lambda n: st.lists(
        st.tuples(_yield_count, _yield_count),
        min_size=n,
        max_size=n,
    )
)


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 15: In-flight job idempotency under
# concurrency
@settings(max_examples=100, deadline=None)
@given(interleaving=_interleavings)
def test_concurrent_creates_yield_one_inflight_job_shared_by_all(
    interleaving: list[tuple[int, int]],
) -> None:
    """At most one non-terminal job; every caller receives its id.

    Property 15 (Requirement 10.5): for any interleaving of concurrent
    ``create_job`` calls for the same (Match_Result, User_Account), the
    partial-unique-index semantics admit exactly one insert; every other
    caller resolves the violation to the existing non-terminal job, so
    all callers respond with one shared job id — and once that job goes
    terminal, a subsequent request creates a fresh job.
    """
    user_id, match_id = uuid4(), uuid4()

    async def _run() -> None:
        store = _InflightStore()

        async def _caller(yields: tuple[int, int]) -> JobCreation:
            session = _ConcurrentFakeSession(
                store,
                match_id=match_id,
                user_id=user_id,
                yields_before_flush=yields[0],
                yields_before_fetch=yields[1],
            )
            return await create_job(
                session,  # type: ignore[arg-type]
                user_id=user_id,
                match_id=match_id,
            )

        creations = await asyncio.gather(*(_caller(yields) for yields in interleaving))

        # --- exactly one insert; everyone shares that job's id ------------
        created_flags = [creation.created for creation in creations]
        assert created_flags.count(True) == 1, (
            f"exactly one caller must insert; got {created_flags}"
        )
        ids = {str(creation.job.id) for creation in creations}
        assert len(ids) == 1, f"every caller must receive the same job id; got {ids}"

        inflight = store.inflight_jobs(match_id=match_id, user_id=user_id)
        assert len(inflight) == 1, "at most one non-terminal job may exist (Requirement 10.5)"
        assert str(inflight[0].id) in ids

        # --- terminal prior job unblocks a fresh creation ------------------
        inflight[0].status = "failed"
        fresh_session = _ConcurrentFakeSession(
            store,
            match_id=match_id,
            user_id=user_id,
            yields_before_flush=0,
            yields_before_fetch=0,
        )
        fresh = await create_job(
            fresh_session,  # type: ignore[arg-type]
            user_id=user_id,
            match_id=match_id,
        )
        assert fresh.created is True
        assert str(fresh.job.id) not in ids, "a terminal prior job means a FRESH job is created"
        assert len(store.inflight_jobs(match_id=match_id, user_id=user_id)) == 1

    _run_sync(_run)
