"""Feature: phase-4-agentic — Property 14.

# Feature: phase-4-agentic, Property 14: Ownership indistinguishability

Property 14: Ownership indistinguishability.

    *For any* mix of job identifiers owned by another user and
    identifiers that exist for no job, requests to the analyze and job
    endpoints return 404 RFC 7807 responses whose bodies are identical
    between the not-owned and not-found cases except for ``request_id``.

**Validates: Requirements 10.4, 12.4**

How this drives the real HTTP surface (without Postgres)
---------------------------------------------------------
The guarantee is an HTTP-level one, so the pair of probes is issued
through the full FastAPI stack — the same shape as the phase-3
``test_ownership_indistinguishability.py`` property, but with the
Postgres dependency replaced by dependency overrides (the alternative
the task explicitly sanctions), so the property runs with no docker
infrastructure:

* ``get_session`` is overridden with an in-memory fake whose ``execute``
  answers the two owner-scoped lookups **by matching the compiled
  statement's bind parameters against the stored rows' attributes** —
  the same convention as ``test_repeat_request_suppression.py``'s fake.
  This gives the emulation real teeth: if either endpoint's query ever
  dropped the ``user_id`` scoping, the not-owned probe would *find* the
  other user's row and the test would fail with a 2xx.
* ``get_current_user`` is overridden with a holder-backed fake so each
  probe runs as a generated requester (one owning nothing, one owning a
  match+job of its own — owning *something* must not help you see
  someone else's rows).
* ``get_rate_limiter`` is overridden with an always-allow fake (rate
  limiting is Requirement 10.7's concern, unit-tested separately; with
  no Redis it would otherwise fail closed and mask the 404s).
* ``get_daily_quota`` / ``get_job_queue`` are replaced by **sentinels
  that record and raise**: an ownership-404 request must never reach
  the quota precheck, job creation, or the Job_Queue — any request that
  survived the ownership check would blow up distinctively, and the
  recorded-calls lists are asserted empty after every example.

What Hypothesis quantifies over (>=100 examples)
------------------------------------------------
* the **endpoint**: ``POST /api/v1/matches/{id}/analyze`` and
  ``GET /api/v1/jobs/{id}`` — the two phase-4 surfaces Requirements
  10.4/12.4 name;
* the **requester**: sampled from the two non-owner accounts;
* the **nonexistent identifier**: random well-formed UUIDs and
  malformed non-UUID path segments (both routers map a malformed id
  onto the same single 404).

Each example issues the *pair* of probes — same requester, same
endpoint; one aimed at the owner's real row (the owner's Match_Result
for analyze; the owner's Agent_Job, with real Agent_Run rows attached,
for the jobs read — the strongest leak candidate), one at a nonexistent
identifier — and asserts both are 404 ``not_found`` RFC 7807 envelopes
carrying exactly the canonical keys, byte-identical apart from
``request_id``, with the quota/queue sentinels never touched.
"""

# Feature: phase-4-agentic, Property 14: Ownership indistinguishability

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient, Response
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st
from uuid_utils.compat import uuid7

from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import get_current_user
from matchlayer_api.core.rate_limit import RateLimitDecision, get_rate_limiter
from matchlayer_api.db.models import AgentJob, AgentRun, MatchResult, User
from matchlayer_api.main import create_app
from matchlayer_api.services.agent_jobs.queue import get_job_queue
from matchlayer_api.services.llm.quota import get_daily_quota

# The canonical RFC 7807 envelope keys (``core/errors.py`` /
# ``conventions.md`` "Error shape").
_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {"type", "title", "detail", "status", "request_id"}
)

_ENDPOINTS: Final[tuple[str, ...]] = ("analyze", "job_get")


class _PipelineEnteredError(AssertionError):
    """Raised if an ownership-404 request reaches quota or queue work."""


# ---------------------------------------------------------------------------
# In-memory fake session: answers the two owner-scoped selects by matching
# compiled bind parameters against row attributes, so the WHERE clause the
# production query actually carries is what decides visibility.
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Answers owner-scoped selects from in-memory rows via bind params.

    Every equality bind in the statement (``id_1`` → ``id``, ``user_id_1``
    → ``user_id``, ...) must match the candidate row's attribute for the
    row to be visible — exactly the semantics of the production WHERE
    clauses, minus the non-parameterized ``IS NULL`` filters (all stored
    rows satisfy those). The target entity is read from the statement's
    ``column_descriptions``.
    """

    def __init__(self, matches: list[MatchResult], jobs: list[AgentJob], runs: list[AgentRun]):
        self._matches = matches
        self._jobs = jobs
        self._runs = runs

    @staticmethod
    def _matches_params(row: Any, params: dict[str, Any]) -> bool:
        for name, value in params.items():
            column = name.rsplit("_", 1)[0]
            if hasattr(row, column) and getattr(row, column) != value:
                return False
        return True

    async def execute(self, stmt: Any) -> _ScalarResult:
        params: dict[str, Any] = dict(stmt.compile().params)
        entities = [desc["type"] for desc in stmt.column_descriptions]

        if entities and entities[0] is MatchResult:
            rows = [m for m in self._matches if self._matches_params(m, params)]
            return _ScalarResult(rows)

        if entities and entities[0] is AgentJob:
            jobs = [j for j in self._jobs if self._matches_params(j, params)]
            if len(entities) > 1 and entities[1] is AgentRun:
                # The LEFT OUTER JOIN shape get_job_with_runs consumes.
                joined: list[tuple[AgentJob, AgentRun | None]] = []
                for job in jobs:
                    job_runs = sorted(
                        (r for r in self._runs if r.job_id == job.id),
                        key=lambda r: (r.created_at, str(r.id)),
                    )
                    if job_runs:
                        joined.extend((job, run) for run in job_runs)
                    else:
                        joined.append((job, None))
                return _ScalarResult(joined)
            return _ScalarResult(jobs)

        raise AssertionError(f"unexpected statement over entities {entities!r}")


# ---------------------------------------------------------------------------
# Sentinels and always-allow fakes for the surrounding dependencies.
# ---------------------------------------------------------------------------


class _SentinelQuota:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def gate(self, user_id: str) -> Any:
        self._calls.append("quota.gate")
        raise _PipelineEnteredError("quota precheck reached on an ownership-404 request")

    async def reserve(self, user_id: str) -> Any:
        self._calls.append("quota.reserve")
        raise _PipelineEnteredError("quota reserve reached on an ownership-404 request")


class _SentinelQueue:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def enqueue(self, message: Any) -> None:
        self._calls.append("queue.enqueue")
        raise _PipelineEnteredError("Job_Queue reached on an ownership-404 request")

    async def healthcheck(self) -> bool:
        return False


class _AllowAllLimiter:
    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, retry_after_seconds=0)


# ---------------------------------------------------------------------------
# Harness: one app + fixed rows, shared across every example.
# ---------------------------------------------------------------------------


def _make_user(prefix: str) -> User:
    return User(
        id=uuid7(),
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name=prefix,
    )


class _Harness:
    def __init__(self) -> None:
        self.owner = _make_user("p14-owner")
        self.requesters = (_make_user("p14-bare"), _make_user("p14-hasdata"))
        self.pipeline_calls: list[str] = []
        self._current_user: User = self.requesters[0]

        # The owner's real rows: a Match_Result, an in-flight Agent_Job
        # over it, and Agent_Run rows on that job (probing the exact id
        # of another user's populated job is the strongest leak shape).
        self.owner_match = MatchResult(
            id=uuid7(),
            user_id=self.owner.id,
            resume_id=uuid7(),
            job_description_text="hiring a backend engineer",
            score=72,
            deleted_at=None,
        )
        self.owner_job = AgentJob(
            id=uuid7(),
            user_id=self.owner.id,
            match_id=self.owner_match.id,
            status="running",
            attempts=1,
        )
        runs = [
            AgentRun(
                id=uuid7(),
                job_id=self.owner_job.id,
                agent_name="resume_analysis",
                input_state_json={},
                output_state_json={},
                latency_ms=10,
                status="completed",
                created_at=datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
            )
        ]

        # The second requester owns data of its own: owning *something*
        # must not make another user's rows visible.
        requester_match = MatchResult(
            id=uuid7(),
            user_id=self.requesters[1].id,
            resume_id=uuid7(),
            job_description_text="another role",
            score=50,
            deleted_at=None,
        )
        requester_job = AgentJob(
            id=uuid7(),
            user_id=self.requesters[1].id,
            match_id=requester_match.id,
            status="queued",
            attempts=0,
        )

        session = _FakeSession(
            matches=[self.owner_match, requester_match],
            jobs=[self.owner_job, requester_job],
            runs=runs,
        )

        app = create_app()

        async def _override_session() -> AsyncIterator[Any]:
            yield session

        async def _override_current_user() -> User:
            return self._current_user

        app.dependency_overrides[get_session] = _override_session
        app.dependency_overrides[get_current_user] = _override_current_user
        app.dependency_overrides[get_rate_limiter] = lambda: _AllowAllLimiter()
        app.dependency_overrides[get_daily_quota] = lambda: _SentinelQuota(self.pipeline_calls)
        app.dependency_overrides[get_job_queue] = lambda: _SentinelQueue(self.pipeline_calls)

        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
        self.runner = asyncio.Runner()

    def request(self, *, endpoint: str, requester_index: int, target_id: str) -> Response:
        self._current_user = self.requesters[requester_index]
        if endpoint == "analyze":
            return self.runner.run(self.client.post(f"/api/v1/matches/{target_id}/analyze"))
        return self.runner.run(self.client.get(f"/api/v1/jobs/{target_id}"))

    def owned_target(self, endpoint: str) -> str:
        """The other user's REAL row id for *endpoint*."""
        if endpoint == "analyze":
            return str(self.owner_match.id)
        return str(self.owner_job.id)

    def close(self) -> None:
        self.runner.run(self.client.aclose())
        self.runner.close()


@pytest.fixture(scope="module")
def harness() -> Iterator[_Harness]:
    h = _Harness()
    try:
        yield h
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Strategies.
# ---------------------------------------------------------------------------


def _is_not_a_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return True
    return False


# A nonexistent identifier: a well-formed UUID matching no row, or a
# malformed URL-safe path segment (both routers map malformed ids onto
# the same single 404).
_missing_uuid = st.uuids(version=4).map(str)
_malformed_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=1,
    max_size=36,
).filter(_is_not_a_uuid)
_absent_id = st.one_of(_missing_uuid, _malformed_id)


def _stable(body: dict[str, Any]) -> dict[str, Any]:
    """The body minus the per-request correlation id."""
    return {k: v for k, v in body.items() if k != "request_id"}


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 14: Ownership indistinguishability
@settings(max_examples=100, deadline=None)
@given(
    endpoint=st.sampled_from(_ENDPOINTS),
    requester_index=st.sampled_from((0, 1)),
    absent_id=_absent_id,
)
@example(endpoint="analyze", requester_index=0, absent_id="00000000-0000-4000-8000-000000000000")
@example(endpoint="analyze", requester_index=1, absent_id="not-a-uuid")
@example(endpoint="job_get", requester_index=0, absent_id="00000000-0000-4000-8000-000000000000")
@example(endpoint="job_get", requester_index=1, absent_id="not-a-uuid")
def test_not_owned_and_nonexistent_are_indistinguishable_404(
    harness: _Harness,
    endpoint: str,
    requester_index: int,
    absent_id: str,
) -> None:
    """Not-owned and not-found collapse to one identical 404 envelope.

    Property 14 (Requirements 10.4, 12.4): for either phase-4 endpoint,
    any requester, and any nonexistent identifier, the response to a
    probe at another user's real row and the response to a probe at a
    nonexistent id are 404 ``not_found`` RFC 7807 envelopes with the
    canonical key set, byte-identical apart from ``request_id`` — and no
    quota precheck, job creation, or Job_Queue work ever occurs.
    """
    assume(absent_id != harness.owned_target(endpoint))

    not_owned = harness.request(
        endpoint=endpoint, requester_index=requester_index, target_id=harness.owned_target(endpoint)
    )
    nonexistent = harness.request(
        endpoint=endpoint, requester_index=requester_index, target_id=absent_id
    )

    assert not_owned.status_code == 404
    assert nonexistent.status_code == 404

    not_owned_body = not_owned.json()
    nonexistent_body = nonexistent.json()

    # The canonical envelope, identical apart from the correlation id.
    assert set(not_owned_body) == set(nonexistent_body) == _ENVELOPE_KEYS
    assert _stable(not_owned_body) == _stable(nonexistent_body)
    assert not_owned_body["type"] == "not_found"
    assert not_owned_body["status"] == 404

    # No quota, job-creation, or queue work occurred (the sentinels
    # would also have crashed the request with a distinctive error).
    assert harness.pipeline_calls == []
