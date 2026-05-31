"""Integration tests for the rate-limit and quota envelopes (task 12.8).

Validates Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7 against the
wired application (``create_app`` mounts ``/api/v1/resumes`` and
``/api/v1/matches``). Three envelopes are covered:

* **Per-minute rate limiting** (11.1, 11.2, 11.3) -- a per-user sliding-window
  budget is reached and the next request returns 429 ``rate_limited``.
* **Daily quotas** (11.4, 11.5, 11.6) -- the per-UTC-day Upload_Quota /
  Scoring_Quota is reached and the next create returns 429 ``quota_exceeded``
  with a ``detail`` that names the daily limit and the UTC reset time, plus a
  committed ``quota_rejected`` audit row carrying the quota category only.
* **Redis fail-closed 503** (11.7) -- when the Rate_Limiter cannot reach Redis,
  the invoked router returns 503 ``rate_limiter_unavailable``.

These tests drive the real wired routers end to end against the docker-compose
Postgres and Redis, reusing the existing integration harness in
``tests/integration/conftest.py``: the ``client_with_session`` ASGI fixture
(whose ``get_session`` override yields the per-test ``db_session``), the autouse
``_truncate_auth_tables`` reset, the autouse ``_flush_rate_limiter_keys`` reset,
the ``factory_user`` builder, the ``redis_client`` fixture, and ``unique_email``.
Authentication mirrors ``test_resumes_api.py`` / ``test_matches_api.py`` -- a
real access token minted with ``issue_access_token`` and presented as
``Authorization: Bearer <token>``.

Why gate on Postgres **and** Redis: the rate-limit tests seed the real Redis
sliding-window key the app's ``RateLimiter`` reads, and the quota tests seed the
``resumes`` / ``match_results`` tables the services count. When either service
is down the module skips rather than fails (CI runs them for real); assertions
are NEVER weakened to pass without infra. Mirrors the module-level skipif used
across the integration suite.

How each envelope is driven (the design's "1-2 representative cases" for
infrastructure config -- design "Testing Strategy" / "Not property-tested"):

* **Rate limit (429)** -- rather than issue ``limit`` successful requests first
  (each of which would do a real upload / score), the per-user sliding-window
  ZSET (``rl:resume:user:{id}`` / ``rl:match:user:{id}``) is seeded directly via
  ``redis_client`` to exactly the configured per-minute budget, so the request
  under test is the one that trips the limiter. The rate-limit dependency runs
  before the route handler, so the seeded-to-capacity key yields a clean 429
  without needing a valid resume or a scoreable body. This is the seed-Redis
  option called out in the task.

* **Quota (429)** -- the per-UTC-day quota is a Postgres ROW COUNT
  (``created_at >= start_of_utc_day``), not a Redis counter, so it is seeded by
  inserting exactly ``MATCHLAYER_RESUME_DAILY_QUOTA`` / ``MATCHLAYER_MATCH_DAILY_QUOTA``
  rows dated "today" via ``db_session``. This exercises the real count path and
  demonstrates 11.5's guarantee that the quota holds independent of Redis (no
  Redis seeding participates in these tests at all). The inserted rows share the
  per-test ``db_session`` with the app, so the in-request count sees them.

* **Redis fail-closed (503)** -- the least-invasive simulation: a local client
  whose ``get_rate_limiter`` dependency is overridden with a stand-in limiter
  whose ``check`` returns ``RateLimitDecision(redis_unavailable=True)`` (the
  exact fail-closed decision the production ``RateLimiter`` emits on a Redis
  error). No services/dependencies/routers are modified.

Retry-After CAVEAT (verified against the wired app, originally surfaced by task
9.2): although ``user_rate_limit`` sets ``Retry-After`` on its injected
``Response`` before raising, that header does NOT survive to the wire -- the
reused foundation ``rate_limited`` handler in ``core/errors.py`` renders a fresh
``JSONResponse`` that carries no ``Retry-After``. The end-to-end assertions
below therefore assert the ACTUAL wired behavior (429 ``rate_limited`` body;
``Retry-After`` absent). The dependency-level ``Retry-After`` placement -- where
the production code actually sets it -- is covered by the unit test in
``tests/unit/test_user_rate_limit_and_idempotency.py``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.rate_limit import RateLimitDecision, get_rate_limiter
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import AuditEvent, MatchResult, Resume, User
from matchlayer_api.main import create_app

from .conftest import UserFactory, postgres_available, redis_available, unique_email

# Both Postgres and Redis must be reachable: the rate-limit tests seed the real
# Redis sliding-window key, and the quota tests count real Postgres rows. When
# either is down the suite skips rather than fails (CI runs them for real).
pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)


# A job description comfortably inside the default 30..50000-char window so the
# match request body validates and the only failure under test is the rate
# limit / quota / 503 envelope (never a 422 from the JD-length validator).
JOB_DESCRIPTION = (
    "Senior Backend Engineer building scalable REST APIs in Python with FastAPI, "
    "PostgreSQL, Docker, and AWS. CI/CD and pytest experience required."
)


# ---------------------------------------------------------------------------
# Auth helpers (mirroring the sibling integration suites).
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    """Build the ``Authorization: Bearer`` header for *token*."""
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    """Create a user row and a matching access token."""
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


# ---------------------------------------------------------------------------
# Seeding helpers.
# ---------------------------------------------------------------------------


async def _seed_rate_limit_at_capacity(
    redis_client: Redis, *, endpoint: str, user_id: Any, limit: int
) -> None:
    """Fill the per-user sliding-window ZSET to exactly *limit* members.

    Writes the same key shape the production ``RateLimiter`` reads --
    ``rl:{endpoint}:user:{user_id}`` -- with *limit* distinct members scored at
    the current millisecond, so they all fall inside the 60-second window. The
    next ``RateLimiter.check`` finds ``ZCARD == limit`` and, since the Lua
    script rejects when ``count >= limit``, returns a by-policy rejection before
    appending the request -- exactly the limit-exceeded condition under test
    (Requirements 11.1, 11.2). The autouse ``_flush_rate_limiter_keys`` fixture
    (which runs at setup) has already cleared any ``rl:*`` keys, so the seeded
    count is precisely *limit*.
    """
    key = f"rl:{endpoint}:user:{user_id}"
    now_ms = int(time.time() * 1000)
    # Distinct members (ZSET members are unique); scores all "now" so every
    # member is inside the window the check applies.
    mapping = {f"{now_ms}:seed-{index}": float(now_ms) for index in range(limit)}
    await redis_client.zadd(key, mapping)


def _make_resume_row(user_id: Any, *, created_at: datetime) -> Resume:
    """Build a fully-extractable ``resumes`` row dated *created_at*.

    Used both as the quota-seeding row for the Upload_Quota test and as the
    single owned resume the seeded ``match_results`` rows reference. Only the
    NOT NULL columns and a today-stamped ``created_at`` matter for the quota
    count; the content is otherwise inert.
    """
    return Resume(
        id=uuid7(),
        user_id=user_id,
        original_filename="seed.pdf",
        storage_key=f"{uuid7()}.pdf",
        content_type="application/pdf",
        byte_size=1024,
        extracted_text="seed extracted text",
        extraction_status="succeeded",
        extraction_char_count=len("seed extracted text"),
        created_at=created_at,
        updated_at=created_at,
        deleted_at=None,
    )


def _make_match_row(user_id: Any, resume_id: Any, *, created_at: datetime) -> MatchResult:
    """Build a minimal ``match_results`` row dated *created_at*.

    Only the NOT NULL columns and a today-stamped ``created_at`` matter for the
    Scoring_Quota count; the JSONB payloads are inert empty/zero values.
    """
    return MatchResult(
        id=uuid7(),
        user_id=user_id,
        resume_id=resume_id,
        job_description_text="seed jd",
        score=0,
        score_breakdown={},
        matched_keywords=[],
        missing_keywords=[],
        suggestions=[],
        scorer_version="test+lex.test",
        created_at=created_at,
        updated_at=created_at,
        deleted_at=None,
    )


async def _quota_rejected_payloads(session: AsyncSession, user_id: Any) -> list[dict[str, Any]]:
    """Return the payloads of every ``quota_rejected`` audit row for *user_id*."""
    result = await session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "quota_rejected",
            AuditEvent.user_id == user_id,
        )
    )
    return [row.payload for row in result.scalars().all()]


@asynccontextmanager
async def _client_with_dead_rate_limiter(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Yield an ASGI client whose Rate_Limiter is forced fail-closed.

    Builds a fresh app via ``create_app`` (the same factory ``client_with_session``
    uses), overrides ``get_session`` to share the per-test ``db_session`` (so
    ``get_current_user`` resolves the real seeded user), and overrides
    ``get_rate_limiter`` with a stand-in whose ``check`` returns
    ``RateLimitDecision(redis_unavailable=True)`` -- the exact fail-closed
    decision the production ``RateLimiter`` emits on a Redis error. This is the
    least-invasive way to exercise Requirement 11.7 without touching
    services/dependencies/routers or needing a dead Redis URL.
    """

    class _RedisDownRateLimiter:
        """Returns a fail-closed decision from every ``check`` (Redis outage)."""

        async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
            return RateLimitDecision(allowed=False, retry_after_seconds=60, redis_unavailable=True)

    app = create_app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_rate_limiter] = lambda: _RedisDownRateLimiter()

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Per-minute rate limiting (Requirements 11.1, 11.2, 11.3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_post_rate_limited_429(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    redis_client: Redis,
) -> None:
    """POST /api/v1/resumes past the per-minute budget → 429 ``rate_limited`` (11.1, 11.3).

    The per-user resume ZSET is seeded to exactly ``resume_rate_limit_per_min``
    members, so this request is the one the sliding window rejects. The rate-limit
    dependency short-circuits before the route handler, so a well-formed multipart
    body never reaches the upload orchestration.
    """
    user, token = await _make_user_and_token(factory_user, "rlresume")
    limit = get_settings().resume_rate_limit_per_min
    await _seed_rate_limit_at_capacity(
        redis_client, endpoint="resume", user_id=user.id, limit=limit
    )

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        # A well-formed file part so the only failure is the rate limit (not a
        # 422 for a missing body). Content is irrelevant: the limiter rejects
        # before any byte is read.
        files={"file": ("resume.pdf", b"%PDF-1.4 placeholder", "application/pdf")},
    )

    assert res.status_code == 429, res.text
    body = res.json()
    assert body["type"] == "rate_limited"
    assert body["status"] == 429

    # CAVEAT (verified against the wired app; first surfaced by task 9.2): the
    # ``Retry-After`` header ``user_rate_limit`` sets on its injected Response
    # does NOT survive -- the foundation ``rate_limited`` handler renders a fresh
    # JSONResponse with no ``Retry-After``. We assert the ACTUAL wired behavior;
    # the dependency-level Retry-After placement is covered by the unit test
    # tests/unit/test_user_rate_limit_and_idempotency.py.
    assert "retry-after" not in res.headers


@pytest.mark.asyncio
async def test_match_post_rate_limited_429(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    redis_client: Redis,
) -> None:
    """POST /api/v1/matches past the per-minute budget → 429 ``rate_limited`` (11.2, 11.3).

    The per-user match ZSET is seeded to exactly ``match_rate_limit_per_min``
    members. A valid body (valid JD length, any resume_id) is sent so the only
    failure is the rate limit; the dependency rejects before the resume is even
    loaded.
    """
    user, token = await _make_user_and_token(factory_user, "rlmatch")
    limit = get_settings().match_rate_limit_per_min
    await _seed_rate_limit_at_capacity(redis_client, endpoint="match", user_id=user.id, limit=limit)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(uuid7()), "job_description": JOB_DESCRIPTION},
    )

    assert res.status_code == 429, res.text
    body = res.json()
    assert body["type"] == "rate_limited"
    assert body["status"] == 429
    # Same Retry-After caveat as the resume case above.
    assert "retry-after" not in res.headers


# ---------------------------------------------------------------------------
# Daily quotas (Requirements 11.4, 11.5, 11.6).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_upload_quota_exceeded_429_with_detail_and_audit(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Upload past the daily quota → 429 ``quota_exceeded`` + audit (11.4, 11.6).

    Seeds exactly ``MATCHLAYER_RESUME_DAILY_QUOTA`` ``resumes`` rows dated today
    (UTC) for the user via the shared session, so the in-request Postgres count
    is already at the ceiling. The next ``POST /api/v1/resumes`` is refused with
    429 ``quota_exceeded`` whose ``detail`` names the daily limit and a UTC reset
    time, and a single ``quota_rejected {quota: "upload"}`` audit row is
    committed. The quota check runs first in ``create_resume``, before any object
    write, so a placeholder body is fine.
    """
    user, token = await _make_user_and_token(factory_user, "qresume")
    quota = get_settings().resume_daily_quota
    now = datetime.now(UTC)
    for _ in range(quota):
        db_session.add(_make_resume_row(user.id, created_at=now))
    await db_session.flush()

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files={"file": ("resume.pdf", b"%PDF-1.4 placeholder", "application/pdf")},
    )

    assert res.status_code == 429, res.text
    body = res.json()
    assert body["type"] == "quota_exceeded"
    assert body["status"] == 429

    # Requirement 11.6: the detail names the daily limit and the UTC reset time.
    detail = body["detail"]
    assert str(quota) in detail
    # The reset instant is the next UTC midnight, rendered as an ISO-8601 string
    # with the +00:00 UTC offset (datetime.isoformat on a UTC-aware datetime).
    reset_at = datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)
    assert reset_at.isoformat() in detail

    # Requirement 11.6: exactly one quota_rejected audit row naming the category.
    payloads = await _quota_rejected_payloads(db_session, user.id)
    assert payloads == [{"quota": "upload"}]


@pytest.mark.asyncio
async def test_match_scoring_quota_exceeded_429_with_detail_and_audit(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """Create-match past the daily quota → 429 ``quota_exceeded`` + audit (11.5, 11.6).

    Seeds exactly ``MATCHLAYER_MATCH_DAILY_QUOTA`` ``match_results`` rows dated
    today (UTC) so the in-request Postgres count is at the ceiling. The next
    ``POST /api/v1/matches`` is refused with 429 ``quota_exceeded`` (detail names
    the limit + UTC reset) and one ``quota_rejected {quota: "scoring"}`` audit row
    is committed. Because the count is Postgres-based (not Redis), this holds
    independent of the rate limiter (Requirement 11.5): no Redis seeding is
    involved, and the request still passes the (empty) rate-limit window cleanly
    before the quota check refuses it.
    """
    user, token = await _make_user_and_token(factory_user, "qmatch")
    quota = get_settings().match_daily_quota
    now = datetime.now(UTC)
    # One owning resume the seeded match rows reference (FK to resumes.id).
    resume = _make_resume_row(user.id, created_at=now)
    db_session.add(resume)
    for _ in range(quota):
        db_session.add(_make_match_row(user.id, resume.id, created_at=now))
    await db_session.flush()

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )

    assert res.status_code == 429, res.text
    body = res.json()
    assert body["type"] == "quota_exceeded"
    assert body["status"] == 429

    # Requirement 11.6: the detail names the daily limit and the UTC reset time.
    detail = body["detail"]
    assert str(quota) in detail
    reset_at = datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)
    assert reset_at.isoformat() in detail

    # Requirement 11.6: exactly one quota_rejected audit row naming the category.
    payloads = await _quota_rejected_payloads(db_session, user.id)
    assert payloads == [{"quota": "scoring"}]


# ---------------------------------------------------------------------------
# Redis fail-closed 503 (Requirement 11.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_post_redis_unavailable_503(
    db_session: AsyncSession,
    factory_user: UserFactory,
) -> None:
    """POST /api/v1/resumes with the Rate_Limiter failing closed → 503 (11.7).

    A locally-built app whose ``get_rate_limiter`` is overridden with a stand-in
    that returns the fail-closed ``RateLimitDecision(redis_unavailable=True)``
    simulates a Redis outage. The router maps that decision to 503
    ``rate_limiter_unavailable`` rather than letting the request proceed --
    the fail-closed contract from ``phase-1-auth`` reused here.
    """
    user = await factory_user(email=unique_email("rdownresume"))
    token = issue_access_token(sub=str(user.id))

    async with _client_with_dead_rate_limiter(db_session) as client:
        res = await client.post(
            "/api/v1/resumes",
            headers=_auth(token),
            files={"file": ("resume.pdf", b"%PDF-1.4 placeholder", "application/pdf")},
        )

    assert res.status_code == 503, res.text
    body = res.json()
    assert body["type"] == "rate_limiter_unavailable"
    assert body["status"] == 503


@pytest.mark.asyncio
async def test_match_post_redis_unavailable_503(
    db_session: AsyncSession,
    factory_user: UserFactory,
) -> None:
    """POST /api/v1/matches with the Rate_Limiter failing closed → 503 (11.7)."""
    user = await factory_user(email=unique_email("rdownmatch"))
    token = issue_access_token(sub=str(user.id))

    async with _client_with_dead_rate_limiter(db_session) as client:
        res = await client.post(
            "/api/v1/matches",
            headers=_auth(token),
            json={"resume_id": str(uuid7()), "job_description": JOB_DESCRIPTION},
        )

    assert res.status_code == 503, res.text
    body = res.json()
    assert body["type"] == "rate_limiter_unavailable"
    assert body["status"] == 503
