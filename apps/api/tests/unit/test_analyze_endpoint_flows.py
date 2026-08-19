"""Endpoint-flow unit tests for the async analyze/jobs surface (task 11.8).

Validates phase-4-agentic Requirements 10.1, 10.6, 10.7, and 11.6 at the
HTTP level, driving the wired FastAPI app (``create_app``) through
``httpx.ASGITransport`` with dependency overrides — no Postgres, no
Redis, no LocalStack:

* **Analyze 202 flow** (Requirement 10.1) — an accepted request persists
  a ``queued`` Agent_Job, commits it BEFORE the single enqueue (design
  D6 / Requirement 11.1's persist-before-enqueue ordering), and responds
  ``202 {id, status: "queued", job_url}``.
* **Enqueue-failure 503 compensation** (Requirement 11.6 / D6) — a
  failing Job_Queue send transitions the committed job to ``failed``
  (structured, display-safe ``error_json``), leaves NO orphaned
  ``queued`` row (the partial-unique-index emulation would block a
  retry), returns the 503 ``job_queue_unavailable`` RFC 7807 envelope
  with fixed display-safe copy — and a subsequent request creates a
  fresh job (the index unblocked).
* **In-flight idempotent reuse** (Requirement 10.5, exercised as an
  endpoint flow) — an existing non-terminal job is returned with 202
  and its id, with no duplicate enqueue.
* **Headers** (Requirement 10.6) — ``X-Robots-Tag: noindex, nofollow``
  on the 202, the 503, and the jobs 404 alike (the middleware covers
  every ``/api/v1/*`` response).
* **RFC 7807 envelopes** — the 429 ``quota_exceeded`` precheck
  rejection (no job row, no message), the 404 ``not_found`` jobs read,
  and the 503 ``job_queue_unavailable`` shape all carry exactly the
  canonical envelope keys.
* **Rate limiting** (Requirement 10.7) — a rejecting limiter maps to
  429 ``rate_limited`` before any job work; a Redis-unavailable limiter
  fails closed to 503 ``rate_limiter_unavailable``. (The dependency's
  ``Retry-After`` placement is unit-covered in
  ``tests/unit/test_user_rate_limit_and_idempotency.py``; the header
  does not survive the foundation handler — the documented caveat.)

The ``/healthz`` ``agents`` field cases for task 11.8 (both branches +
the ~10 s cache) live with the other healthz tests in
``tests/test_health.py``.

The in-memory session fake answers the owner-scoped Match_Result
select, the create-job insert (with the partial unique index's
semantics enforced at flush), the in-flight fetch, and the guarded
``mark_failed`` UPDATE — the exact surface the analyze path touches.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Update
from uuid_utils.compat import uuid7

from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import get_current_user
from matchlayer_api.core.rate_limit import RateLimitDecision, get_rate_limiter
from matchlayer_api.db.models import AgentJob, AgentRun, MatchResult, User
from matchlayer_api.main import create_app
from matchlayer_api.services.agent_jobs.queue import get_job_queue
from matchlayer_api.services.llm.quota import QuotaDecision, get_daily_quota

_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {"type", "title", "detail", "status", "request_id"}
)
_INFLIGHT: Final[tuple[str, str]] = ("queued", "running")
_INDEX_NAME: Final[str] = "agent_jobs_match_user_inflight_uniq"
_NOINDEX: Final[str] = "noindex, nofollow"


# ---------------------------------------------------------------------------
# In-memory session fake for the analyze path.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, *, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows if rows is not None else ([scalar] if scalar is not None else [])

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalar_one(self) -> Any:
        assert self._scalar is not None
        return self._scalar

    def all(self) -> list[Any]:
        return self._rows


class _FakeSavepoint:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            self._session.pending.clear()
        return False


class _FakeSession:
    """The analyze path's session surface over an in-memory job store.

    ``execute`` dispatches on the statement: the owned Match_Result
    select, AgentJob selects (in-flight fetch / post-UPDATE re-read /
    the jobs router's join), and the guarded ``mark_failed`` UPDATE.
    Row visibility is decided by the statement's own bind parameters,
    so the production WHERE clauses are what's exercised.
    """

    def __init__(self, matches: list[MatchResult]) -> None:
        self.matches = matches
        self.jobs: list[AgentJob] = []
        self.runs: list[AgentRun] = []
        self.pending: list[Any] = []
        self.commits = 0
        self.events: list[str] = []

    # ---- transaction surface ------------------------------------------

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint(self)

    def add(self, obj: Any) -> None:
        self.pending.append(obj)

    async def flush(self) -> None:
        for obj in list(self.pending):
            if isinstance(obj, AgentJob):
                # The partial unique index's semantics (D5).
                if any(
                    j.match_id == obj.match_id
                    and j.user_id == obj.user_id
                    and j.status in _INFLIGHT
                    for j in self.jobs
                ):
                    orig = Exception(
                        f'duplicate key value violates unique constraint "{_INDEX_NAME}"'
                    )
                    raise IntegrityError("INSERT INTO agent_jobs ...", None, orig)
                self.jobs.append(obj)
            else:  # pragma: no cover - nothing else is staged on this path
                raise AssertionError(f"unexpected staged object {obj!r}")
        self.pending.clear()

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")

    # ---- statement dispatch ---------------------------------------------

    @staticmethod
    def _match_params(row: Any, params: dict[str, Any]) -> bool:
        for name, value in params.items():
            column = name.rsplit("_", 1)[0]
            if not hasattr(row, column):
                continue
            attr = getattr(row, column)
            if isinstance(value, (list, tuple, set)):
                if attr not in value:
                    return False
            elif attr != value:
                return False
        return True

    async def execute(self, stmt: Any) -> _FakeResult:
        params: dict[str, Any] = dict(stmt.compile().params)

        if isinstance(stmt, Update):
            # mark_failed's guarded UPDATE ... WHERE status IN (...) RETURNING id.
            target = next(
                (j for j in self.jobs if j.id == params.get("id_1") and j.status in _INFLIGHT),
                None,
            )
            if target is None:
                return _FakeResult(scalar=None)
            target.status = params["status"]
            target.completed_at = params["completed_at"]
            target.error_json = params["error_json"]
            self.events.append("mark_failed")
            return _FakeResult(scalar=target.id)

        entities = [desc["type"] for desc in stmt.column_descriptions]
        if entities and entities[0] is MatchResult:
            rows = [m for m in self.matches if self._match_params(m, params)]
            return _FakeResult(scalar=rows[0] if rows else None)
        if entities and entities[0] is AgentJob:
            jobs = [j for j in self.jobs if self._match_params(j, params)]
            if len(entities) > 1 and entities[1] is AgentRun:
                joined: list[tuple[AgentJob, AgentRun | None]] = []
                for job in jobs:
                    job_runs = [r for r in self.runs if r.job_id == job.id]
                    if job_runs:
                        joined.extend((job, run) for run in job_runs)
                    else:
                        joined.append((job, None))
                return _FakeResult(rows=joined)
            return _FakeResult(scalar=jobs[0] if jobs else None)
        raise AssertionError(f"unexpected statement over entities {entities!r}")


# ---------------------------------------------------------------------------
# Collaborator fakes.
# ---------------------------------------------------------------------------


class _FakeQuota:
    def __init__(self, remaining: int = 10) -> None:
        self.remaining = remaining

    async def gate(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=self.remaining > 0, remaining=self.remaining)


class _FakeQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.enqueued: list[Any] = []
        self.events: list[str] | None = None

    async def enqueue(self, message: Any) -> None:
        if self.events is not None:
            self.events.append("enqueue")
        if self.fail:
            raise ConnectionError("sqs unreachable (synthetic)")
        self.enqueued.append(message)

    async def healthcheck(self) -> bool:
        return not self.fail


class _ScriptedLimiter:
    """RateLimiter fake: allow, reject-by-policy, or fail-closed."""

    def __init__(self, mode: str = "allow") -> None:
        self.mode = mode

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        if self.mode == "reject":
            return RateLimitDecision(allowed=False, retry_after_seconds=37)
        if self.mode == "unavailable":
            return RateLimitDecision(allowed=False, retry_after_seconds=60, redis_unavailable=True)
        return RateLimitDecision(allowed=True, retry_after_seconds=0)


# ---------------------------------------------------------------------------
# Harness fixtures.
# ---------------------------------------------------------------------------


def _make_user() -> User:
    return User(
        id=uuid7(),
        email=f"analyze-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name="analyze-flows",
    )


def _make_match(user_id: Any) -> MatchResult:
    return MatchResult(
        id=uuid7(),
        user_id=user_id,
        resume_id=uuid7(),
        job_description_text="hiring a backend engineer",
        score=72,
        deleted_at=None,
    )


class _Harness:
    def __init__(self) -> None:
        self.user = _make_user()
        self.match = _make_match(self.user.id)
        self.session = _FakeSession([self.match])
        self.quota = _FakeQuota()
        self.queue = _FakeQueue()
        self.queue.events = self.session.events
        self.limiter = _ScriptedLimiter()

        app = create_app()

        async def _override_session() -> AsyncIterator[Any]:
            yield self.session

        async def _override_user() -> User:
            return self.user

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[get_current_user] = _override_user
        app.dependency_overrides[get_rate_limiter] = lambda: self.limiter
        app.dependency_overrides[get_daily_quota] = lambda: self.quota
        app.dependency_overrides[get_job_queue] = lambda: self.queue
        self.app = app


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[tuple[_Harness, AsyncClient]]:
    h = _Harness()
    transport = ASGITransport(app=h.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield h, client


# ---------------------------------------------------------------------------
# Analyze 202 flow (Requirements 10.1, 10.6, 11.1/D6 ordering).
# ---------------------------------------------------------------------------


async def test_analyze_accepts_with_202_job_persisted_before_single_enqueue(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    h, client = harness

    res = await client.post(f"/api/v1/matches/{h.match.id}/analyze")

    assert res.status_code == 202
    assert len(h.session.jobs) == 1
    job = h.session.jobs[0]
    assert job.status == "queued"
    assert job.user_id == h.user.id
    assert job.match_id == h.match.id

    body = res.json()
    assert body == {
        "id": str(job.id),
        "status": "queued",
        "job_url": f"/api/v1/jobs/{job.id}",
    }

    # Persist-before-enqueue (D6): the commit strictly precedes the send.
    assert h.session.events == ["commit", "enqueue"]
    assert len(h.queue.enqueued) == 1
    message = h.queue.enqueued[0]
    assert message.job_id == str(job.id)
    assert message.match_id == str(h.match.id)
    assert message.user_id == str(h.user.id)

    # Requirement 10.6: the API-wide noindex header on the 202.
    assert res.headers["X-Robots-Tag"] == _NOINDEX


async def test_analyze_reuses_inflight_job_without_second_enqueue(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    """An existing non-terminal job is returned as-is (Requirement 10.5)."""
    h, client = harness

    first = await client.post(f"/api/v1/matches/{h.match.id}/analyze")
    assert first.status_code == 202
    second = await client.post(f"/api/v1/matches/{h.match.id}/analyze")

    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert len(h.session.jobs) == 1
    assert len(h.queue.enqueued) == 1  # no duplicate message


# ---------------------------------------------------------------------------
# Enqueue-failure 503 compensation (Requirement 11.6 / D6).
# ---------------------------------------------------------------------------


async def test_enqueue_failure_marks_job_failed_and_returns_503_envelope(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    h, client = harness
    h.queue.fail = True

    res = await client.post(f"/api/v1/matches/{h.match.id}/analyze")

    # The 503 job_queue_unavailable RFC 7807 envelope with fixed
    # display-safe copy — never the queue URL or exception text.
    assert res.status_code == 503
    body = res.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["type"] == "job_queue_unavailable"
    assert body["status"] == 503
    assert "sqs" not in res.text.lower()
    assert "ConnectionError" not in res.text
    assert res.headers["X-Robots-Tag"] == _NOINDEX

    # Compensation: the committed job is failed — no orphaned queued row.
    assert len(h.session.jobs) == 1
    job = h.session.jobs[0]
    assert job.status == "failed"
    assert job.error_json == {
        "type": "enqueue_failed",
        "detail": "The analysis could not be queued. Please try again later.",
    }
    assert job.completed_at is not None
    # Ordering: queued-commit → failed enqueue attempt → compensating
    # transition → compensating commit.
    assert h.session.events == ["commit", "enqueue", "mark_failed", "commit"]


async def test_enqueue_failure_compensation_unblocks_a_retry(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    """The failed row releases the partial unique index for a fresh job."""
    h, client = harness

    h.queue.fail = True
    first = await client.post(f"/api/v1/matches/{h.match.id}/analyze")
    assert first.status_code == 503

    h.queue.fail = False
    retry = await client.post(f"/api/v1/matches/{h.match.id}/analyze")

    assert retry.status_code == 202
    statuses = sorted(job.status for job in h.session.jobs)
    assert statuses == ["failed", "queued"]
    assert retry.json()["id"] != str(h.session.jobs[0].id)
    assert len(h.queue.enqueued) == 1  # only the retry's message went out


# ---------------------------------------------------------------------------
# Quota precheck 429 (Requirement 9.4's endpoint surface, RFC 7807 shape).
# ---------------------------------------------------------------------------


async def test_quota_precheck_below_two_units_yields_429_and_creates_nothing(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    h, client = harness
    h.quota.remaining = 1

    res = await client.post(f"/api/v1/matches/{h.match.id}/analyze")

    assert res.status_code == 429
    body = res.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["type"] == "quota_exceeded"
    assert body["status"] == 429
    assert "Quota resets at " in body["detail"]  # the UTC reset instant
    assert h.session.jobs == []
    assert h.queue.enqueued == []
    assert h.session.commits == 0


# ---------------------------------------------------------------------------
# Rate limiting (Requirement 10.7) on both phase-4 endpoints.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method_path", ["analyze", "job_get"])
async def test_rate_limit_rejection_maps_to_429_rate_limited(
    harness: tuple[_Harness, AsyncClient], method_path: str
) -> None:
    h, client = harness
    h.limiter.mode = "reject"

    if method_path == "analyze":
        res = await client.post(f"/api/v1/matches/{h.match.id}/analyze")
    else:
        res = await client.get(f"/api/v1/jobs/{uuid7()}")

    assert res.status_code == 429
    body = res.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["type"] == "rate_limited"
    # Rejected before any job work.
    assert h.session.jobs == []
    assert h.queue.enqueued == []
    assert res.headers["X-Robots-Tag"] == _NOINDEX


@pytest.mark.parametrize("method_path", ["analyze", "job_get"])
async def test_rate_limiter_outage_fails_closed_with_503(
    harness: tuple[_Harness, AsyncClient], method_path: str
) -> None:
    h, client = harness
    h.limiter.mode = "unavailable"

    if method_path == "analyze":
        res = await client.post(f"/api/v1/matches/{h.match.id}/analyze")
    else:
        res = await client.get(f"/api/v1/jobs/{uuid7()}")

    assert res.status_code == 503
    body = res.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["type"] == "rate_limiter_unavailable"
    assert h.session.jobs == []


# ---------------------------------------------------------------------------
# Jobs read: 404 envelope + headers; 200 read of a queued job.
# ---------------------------------------------------------------------------


async def test_job_get_missing_job_returns_not_found_envelope_with_noindex(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    _h, client = harness

    res = await client.get(f"/api/v1/jobs/{uuid7()}")

    assert res.status_code == 404
    body = res.json()
    assert set(body) == _ENVELOPE_KEYS
    assert body["type"] == "not_found"
    assert body["status"] == 404
    assert res.headers["X-Robots-Tag"] == _NOINDEX


async def test_job_get_returns_queued_job_with_pending_steps_and_noindex(
    harness: tuple[_Harness, AsyncClient],
) -> None:
    """The accepted job polls back: queued, five pending steps, headers."""
    h, client = harness

    accepted = await client.post(f"/api/v1/matches/{h.match.id}/analyze")
    job_url = accepted.json()["job_url"]

    res = await client.get(job_url)

    assert res.status_code == 200
    body = res.json()
    assert body["id"] == accepted.json()["id"]
    assert body["status"] == "queued"
    assert body["started_at"] is None and body["completed_at"] is None
    assert body["created_at"].endswith("Z")
    assert [step["status"] for step in body["steps"]] == ["pending"] * 5
    assert body["result"] is None and body["error"] is None
    assert res.headers["X-Robots-Tag"] == _NOINDEX
