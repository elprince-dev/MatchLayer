"""Integration tests for the match HTTP surface (task 11.5).

Validates Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.8, 9.3, 9.4, 9.6 (and
the 9.1/9.2 list-shape exclusion the lifecycle tests exercise alongside 9.3).

These tests drive the wired ``Matches_Router`` (mounted at ``/api/v1/matches``
by ``create_app``) end to end against the docker-compose Postgres and Redis,
reusing the existing integration harness: the ``client_with_session`` ASGI
fixture (whose ``get_session`` override yields the per-test ``db_session``), the
autouse ``_truncate_auth_tables`` reset, the ``factory_user`` builder, and
``unique_email`` (see ``tests/integration/conftest.py``). Authentication mirrors
``test_me.py`` — a real access token minted with ``issue_access_token`` and
presented as ``Authorization: Bearer <token>``.

Why gate on Postgres **and** Redis (not Postgres alone, unlike the auth-only
suites): every ``Matches_Router`` route depends on ``user_rate_limit("match")``,
which calls the Redis-backed ``RateLimiter`` on each request, and ``POST``
additionally injects the Redis-backed ``IdempotencyStore``. Without Redis the
rate limiter fails closed (503) and the endpoints can't be exercised, so the
module skips when either service is down — the suite stays green locally without
Docker and runs for real in CI. This matches the spec's design note that the
matching surface reuses the auth Redis primitives.

Resume setup strategy (design "Integration tests" — "Add factories for resumes
and matches"): a match needs an OWNED resume with ``extraction_status ==
'succeeded'``. Rather than driving the full ``POST /api/v1/resumes`` upload (S3
storage, MIME sniffing, extraction), these tests insert ``resumes`` rows
directly via ``db_session`` with the extraction columns pre-set. That keeps the
focus on the match surface (the upload path is covered by task 10.7) while still
exercising real, deterministic scoring through ``ml.scorer_adapter`` against the
committed lexicon — no scorer mock is needed or used.

PRIVACY (Requirement 8.8): ``job_description_text`` is Restricted PII. The
"never logged" negative uses the ``structlog.testing.capture_logs`` pattern from
``tests/unit/test_extraction_failures.py`` to assert a JD sentinel reaches no
log event, and additionally asserts the sentinel and the field name never appear
in any response body (POST 201, GET detail, GET list items).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import AuditEvent, Resume, User

from .conftest import UserFactory, postgres_available, redis_available, unique_email

# Both Postgres and Redis must be reachable: every match route runs the
# Redis-backed per-user rate limiter, and POST also uses the Redis idempotency
# store. When either is down the suite skips rather than fails (CI runs them for
# real). Mirrors the module-level skipif used across the auth integration suite.
pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)


# ---------------------------------------------------------------------------
# Test data — realistic, deterministic resume/JD text.
#
# Drawn from the committed eyeball "strong match" pair so real scoring through
# ``ml.scorer_adapter`` returns a stable, non-trivial result with matched
# keywords. Scoring is deterministic for a fixed algorithm + lexicon version
# (Requirement 5.4), so no mock is required.
# ---------------------------------------------------------------------------

RESUME_TEXT = (
    "Senior Backend Engineer with eight years of experience building scalable "
    "REST APIs in Python. I have shipped production services with the FastAPI "
    "framework, modeled relational data in PostgreSQL, and containerized every "
    "service with Docker. I deployed and operated these systems on AWS, owning "
    "the CI/CD pipeline end to end and writing thorough pytest suites. I have "
    "also used Redis for caching and run workloads on Kubernetes."
)

JOB_DESCRIPTION = (
    "We are hiring a Senior Backend Engineer to design and build scalable REST "
    "APIs in Python using the FastAPI framework. You will model data in "
    "PostgreSQL, containerize services with Docker, and deploy to AWS. Strong "
    "experience with CI/CD pipelines and automated testing with pytest is "
    "required. Familiarity with Redis caching and Kubernetes is preferred."
)

# The full field set Requirement 8.7 enumerates for the match response body.
_MATCH_RESPONSE_FIELDS = {
    "id",
    "resume_id",
    "score",
    "score_breakdown",
    "matched_keywords",
    "missing_keywords",
    "suggestions",
    "scorer_version",
    "created_at",
    "updated_at",
}

# The minimum field set Requirement 9.2 enumerates for a list item.
_MATCH_LIST_ITEM_FIELDS = {"id", "resume_id", "score", "created_at"}


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


ResumeFactory = Callable[..., Awaitable[Resume]]


@pytest_asyncio.fixture
async def factory_resume(db_session: AsyncSession) -> ResumeFactory:
    """Insert a ``resumes`` row into the per-test session.

    Defaults produce a fully extractable resume (``extraction_status =
    'succeeded'`` with ``extracted_text`` set) so it is immediately scoreable.
    Pass ``extraction_status='pending'`` / ``'failed'`` to build a
    non-extractable resume for the Requirement 8.5 path; a failed/pending resume
    carries no extracted text, mirroring what the real extractor writes.

    The row is flushed (not committed); because ``client_with_session`` shares
    this exact session with the API, the inserted resume is visible to the match
    endpoints within the same transaction. The autouse ``_truncate_auth_tables``
    fixture's ``TRUNCATE ... CASCADE`` reaches ``resumes`` / ``match_results``
    (both FK-reference ``users``), so no cross-test leakage occurs.
    """

    async def _build(
        *,
        user_id: object,
        extraction_status: str = "succeeded",
        extracted_text: str | None = RESUME_TEXT,
        content_type: str = "application/pdf",
        original_filename: str = "resume.pdf",
        deleted_at: datetime | None = None,
    ) -> Resume:
        succeeded = extraction_status == "succeeded"
        text = extracted_text if succeeded else None
        resume = Resume(
            id=uuid7(),
            user_id=user_id,
            original_filename=original_filename,
            storage_key=f"{uuid7()}.pdf",
            content_type=content_type,
            byte_size=2048,
            extracted_text=text,
            extraction_status=extraction_status,
            extraction_char_count=len(text) if text is not None else None,
            deleted_at=deleted_at,
        )
        db_session.add(resume)
        await db_session.flush()
        return resume

    return _build


def _auth(token: str) -> dict[str, str]:
    """Build the ``Authorization: Bearer`` header for *token*."""
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    """Create a user and a matching access token."""
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


# ---------------------------------------------------------------------------
# POST /api/v1/matches — happy path (Requirements 8.1, 8.6, 8.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_match_happy_201_full_field_set_and_audit(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """POST with a valid resume + JD → 201 with the full MatchResponse field set.

    Exercises real scoring through ``ml.scorer_adapter`` (Requirement 8.1),
    asserts the body carries exactly the ten Requirement 8.7 fields and never
    ``job_description_text`` (Requirement 8.8), and that a ``match_created``
    audit row referencing internal ids only is emitted (Requirement 8.6).
    """
    user, token = await _make_user_and_token(factory_user, "matchhappy")
    resume = await factory_resume(user_id=user.id)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )

    assert res.status_code == 201
    body = res.json()

    # Requirement 8.7: exactly the enumerated field set, no extras.
    assert set(body.keys()) == _MATCH_RESPONSE_FIELDS
    # Requirement 8.8: the Restricted JD text is never in the response body.
    assert "job_description_text" not in body

    assert body["resume_id"] == str(resume.id)
    # Real, deterministic scoring: a bounded integer (Requirements 5.1, 5.3).
    assert isinstance(body["score"], int) and not isinstance(body["score"], bool)
    assert 0 <= body["score"] <= 100

    # Breakdown carries the five explainability fields (Requirement 5.5 shape).
    assert set(body["score_breakdown"].keys()) == {
        "similarity_component",
        "keyword_coverage_component",
        "weight_similarity",
        "weight_keyword",
        "final_score",
    }
    assert body["score_breakdown"]["final_score"] == body["score"]

    # Keyword/suggestion lists carry the declared item shapes.
    assert isinstance(body["matched_keywords"], list)
    assert isinstance(body["missing_keywords"], list)
    for kw in body["matched_keywords"] + body["missing_keywords"]:
        assert set(kw.keys()) == {"term", "weight"}
    assert isinstance(body["suggestions"], list)
    assert body["scorer_version"]

    # Requirement 8.6: a match_created audit row with ids only (no PII).
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "match_created",
            AuditEvent.user_id == user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.payload == {"resume_id": str(resume.id), "match_id": body["id"]}
    # Defense in depth: the JD text never reaches the audit payload.
    assert JOB_DESCRIPTION not in str(audit.payload)


# ---------------------------------------------------------------------------
# POST /api/v1/matches — validation (Requirements 8.2, 8.3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_match_jd_too_short_422_validation_error(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """A JD shorter than MATCHLAYER_JD_MIN_CHARS after trimming → 422 (8.3)."""
    user, token = await _make_user_and_token(factory_user, "jdshort")
    resume = await factory_resume(user_id=user.id)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        # 5 visible chars after trimming, well under the default 30-char floor.
        json={"resume_id": str(resume.id), "job_description": "   short   "},
    )
    assert res.status_code == 422
    assert res.json()["type"] == "validation_error"

    # No Match_Result was created on the rejected path.
    assert await _count_match_created(db_session, user.id) == 0


@pytest.mark.asyncio
async def test_create_match_jd_too_long_422_validation_error(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
) -> None:
    """A JD longer than MATCHLAYER_JD_MAX_CHARS after trimming → 422 (8.3)."""
    user, token = await _make_user_and_token(factory_user, "jdlong")
    resume = await factory_resume(user_id=user.id)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        # Exceeds the default 50000-char ceiling.
        json={"resume_id": str(resume.id), "job_description": "x" * 50_001},
    )
    assert res.status_code == 422
    assert res.json()["type"] == "validation_error"


@pytest.mark.asyncio
async def test_create_match_invalid_body_422_validation_error(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
) -> None:
    """A body that fails Pydantic validation → 422 validation_error (8.2).

    Two shapes that must both be rejected before any Match_Result is created:
    a missing required field and an unknown extra field (the request schema is
    ``extra="forbid"``).
    """
    _user, token = await _make_user_and_token(factory_user, "badbody")

    # Missing the required ``job_description`` field.
    res_missing = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(uuid7())},
    )
    assert res_missing.status_code == 422
    assert res_missing.json()["type"] == "validation_error"

    # Unknown extra field — rejected by ``extra="forbid"``.
    res_extra = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={
            "resume_id": str(uuid7()),
            "job_description": JOB_DESCRIPTION,
            "unexpected": "nope",
        },
    )
    assert res_extra.status_code == 422
    assert res_extra.json()["type"] == "validation_error"


# ---------------------------------------------------------------------------
# POST /api/v1/matches — not_found (Requirement 8.4).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_match_missing_resume_404_not_found(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    db_session: AsyncSession,
) -> None:
    """A resume_id that resolves to no row → 404 not_found (8.4)."""
    user, token = await _make_user_and_token(factory_user, "noresume")

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(uuid7()), "job_description": JOB_DESCRIPTION},
    )
    assert res.status_code == 404
    assert res.json()["type"] == "not_found"
    assert await _count_match_created(db_session, user.id) == 0


@pytest.mark.asyncio
async def test_create_match_other_owner_resume_404_not_found(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """A resume owned by a different user → 404 not_found, no disclosure (8.4)."""
    owner = await factory_user(email=unique_email("owner"))
    requester, token = await _make_user_and_token(factory_user, "intruder")

    # Resume belongs to ``owner``, but ``requester`` presents its id.
    resume = await factory_resume(user_id=owner.id)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )
    assert res.status_code == 404
    assert res.json()["type"] == "not_found"
    # Neither account got a Match_Result.
    assert await _count_match_created(db_session, requester.id) == 0
    assert await _count_match_created(db_session, owner.id) == 0


# ---------------------------------------------------------------------------
# POST /api/v1/matches — resume_not_extractable (Requirement 8.5).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("extraction_status", ["pending", "failed"])
async def test_create_match_resume_not_extractable_422(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
    extraction_status: str,
) -> None:
    """A resume whose extraction_status != 'succeeded' → 422 (8.5)."""
    user, token = await _make_user_and_token(factory_user, "notextract")
    resume = await factory_resume(user_id=user.id, extraction_status=extraction_status)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )
    assert res.status_code == 422
    assert res.json()["type"] == "resume_not_extractable"
    assert await _count_match_created(db_session, user.id) == 0


# ---------------------------------------------------------------------------
# GET /api/v1/matches (list) — Requirements 9.1, 9.2.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_matches_omits_job_description_text(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
) -> None:
    """The list view returns only {id, resume_id, score, created_at} (9.1, 9.2)."""
    user, token = await _make_user_and_token(factory_user, "listmatch")
    resume = await factory_resume(user_id=user.id)

    created = await _create_match(client_with_session, token, str(resume.id), JOB_DESCRIPTION)

    res = await client_with_session.get("/api/v1/matches", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"items", "next_cursor"}
    assert len(body["items"]) == 1

    item = body["items"][0]
    # Requirement 9.2: exactly the trimmed field set, never the JD text.
    assert set(item.keys()) == _MATCH_LIST_ITEM_FIELDS
    assert "job_description_text" not in item
    assert item["id"] == created["id"]
    assert item["resume_id"] == str(resume.id)


# ---------------------------------------------------------------------------
# GET /api/v1/matches/{id} — Requirement 9.3.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_match_returns_full_response(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
) -> None:
    """GET by id returns the owned match's full MatchResponse field set (9.3)."""
    user, token = await _make_user_and_token(factory_user, "getmatch")
    resume = await factory_resume(user_id=user.id)
    created = await _create_match(client_with_session, token, str(resume.id), JOB_DESCRIPTION)

    res = await client_with_session.get(f"/api/v1/matches/{created['id']}", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == _MATCH_RESPONSE_FIELDS
    assert "job_description_text" not in body
    assert body["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_match_other_owner_404_not_found(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
) -> None:
    """A match owned by another user is indistinguishable from missing (9.3)."""
    owner, owner_token = await _make_user_and_token(factory_user, "matchowner")
    resume = await factory_resume(user_id=owner.id)
    created = await _create_match(client_with_session, owner_token, str(resume.id), JOB_DESCRIPTION)

    _intruder, intruder_token = await _make_user_and_token(factory_user, "matchintruder")
    res = await client_with_session.get(
        f"/api/v1/matches/{created['id']}", headers=_auth(intruder_token)
    )
    assert res.status_code == 404
    assert res.json()["type"] == "not_found"


# ---------------------------------------------------------------------------
# DELETE /api/v1/matches/{id} — idempotent (Requirements 9.4, 9.5).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_match_idempotent_204_and_single_audit(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """First delete soft-deletes (204 + audit); the second is a 204 no-op (9.4, 9.5)."""
    user, token = await _make_user_and_token(factory_user, "delmatch")
    resume = await factory_resume(user_id=user.id)
    created = await _create_match(client_with_session, token, str(resume.id), JOB_DESCRIPTION)

    # First delete → 204 and a match_deleted audit row.
    res1 = await client_with_session.delete(
        f"/api/v1/matches/{created['id']}", headers=_auth(token)
    )
    assert res1.status_code == 204

    # The match is gone from subsequent reads (Requirement 1.6 / 9.x).
    res_get = await client_with_session.get(
        f"/api/v1/matches/{created['id']}", headers=_auth(token)
    )
    assert res_get.status_code == 404

    # Second delete → still 204, idempotent (Requirement 9.5).
    res2 = await client_with_session.delete(
        f"/api/v1/matches/{created['id']}", headers=_auth(token)
    )
    assert res2.status_code == 204

    # Exactly one match_deleted audit row was emitted (Requirement 9.5).
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "match_deleted",
            AuditEvent.user_id == user.id,
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"match_id": created["id"]}


# ---------------------------------------------------------------------------
# Retained-after-resume-deletion (Requirement 9.6).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_retained_after_resume_soft_deleted(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """A match is still returned after its resume is soft-deleted (9.6).

    The score and analysis are retained independently of the resume's
    lifecycle: ``GET /api/v1/matches/{id}`` does not filter on the resume's
    ``deleted_at``.
    """
    user, token = await _make_user_and_token(factory_user, "retain")
    resume = await factory_resume(user_id=user.id)
    created = await _create_match(client_with_session, token, str(resume.id), JOB_DESCRIPTION)

    # Soft-delete the underlying resume directly on the shared session.
    resume.deleted_at = datetime.now(UTC)
    await db_session.flush()

    res = await client_with_session.get(f"/api/v1/matches/{created['id']}", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == created["id"]
    assert body["resume_id"] == str(resume.id)
    assert set(body.keys()) == _MATCH_RESPONSE_FIELDS


# ---------------------------------------------------------------------------
# JD never logged + never in any response body (Requirement 8.8).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_description_never_logged_or_in_response_bodies(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
) -> None:
    """A JD sentinel reaches no log event and no response body (8.8).

    Uses the ``structlog.testing.capture_logs`` pattern: the entire
    create → get-detail → list flow runs inside a capture context, and the
    sentinel embedded in the job description must appear in none of the captured
    structured log events. The same sentinel — and the field name
    ``job_description_text`` itself — must also be absent from the POST 201 body,
    the GET detail body, and every GET list item.
    """
    user, token = await _make_user_and_token(factory_user, "jdsecret")
    resume = await factory_resume(user_id=user.id)

    sentinel = "ZZSENTINELJDPRIVATE1234567890ZZ"
    jd_with_sentinel = (
        "We are hiring a Python backend engineer with FastAPI and PostgreSQL "
        f"experience. {sentinel} Apply to join the team building REST APIs."
    )

    with structlog.testing.capture_logs() as captured:
        post_res = await client_with_session.post(
            "/api/v1/matches",
            headers=_auth(token),
            json={"resume_id": str(resume.id), "job_description": jd_with_sentinel},
        )
        assert post_res.status_code == 201
        match_id = post_res.json()["id"]

        get_res = await client_with_session.get(f"/api/v1/matches/{match_id}", headers=_auth(token))
        assert get_res.status_code == 200

        list_res = await client_with_session.get("/api/v1/matches", headers=_auth(token))
        assert list_res.status_code == 200

    # The JD sentinel must not appear in any captured structured log event.
    haystack = repr(captured)
    assert sentinel not in haystack, "job_description leaked into a structured log event"

    # ...and not in any response body, nor the field name itself (Requirement 8.8).
    for body_text in (post_res.text, get_res.text, list_res.text):
        assert sentinel not in body_text
        assert "job_description_text" not in body_text


# ---------------------------------------------------------------------------
# Local helpers.
# ---------------------------------------------------------------------------


async def _create_match(
    client: AsyncClient, token: str, resume_id: str, job_description: str
) -> dict[str, object]:
    """POST a match and return its 201 body (fails the test on a non-201)."""
    res = await client.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": resume_id, "job_description": job_description},
    )
    assert res.status_code == 201, f"expected 201, got {res.status_code}: {res.text}"
    return res.json()


async def _count_match_created(session: AsyncSession, user_id: object) -> int:
    """Count ``match_created`` audit rows for *user_id* in the shared session."""
    result = await session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "match_created",
            AuditEvent.user_id == user_id,
        )
    )
    return len(result.scalars().all())
