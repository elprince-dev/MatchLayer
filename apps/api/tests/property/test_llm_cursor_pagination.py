"""Feature: phase-3-llm-layer — Property 18.

Property 18: Cursor pagination is complete, ordered, and bounded.

    *For any* generated set of persisted LLM_Results under a match and any
    valid ``limit`` in 1..100, walking the list endpoint page-by-page yields
    entries in strictly descending ``created_at`` (UUIDv7) order with no
    duplicates or gaps, and the concatenation of pages equals the full set;
    and *for any* out-of-bounds ``limit`` or malformed/undecodable
    ``cursor``, the request is rejected with 422 and returns no result data.

**Validates: Requirements 16.4, 16.10**

Why this drives the real HTTP surface (with Postgres)
-----------------------------------------------------
The guarantee spans three layers that must agree for pagination to be
complete and bounded end to end: FastAPI's ``Query(ge=1, le=100)`` limit
validation (the 422 on an out-of-bounds ``limit``), the opaque cursor codec
in ``services/llm/results.py`` (the 422 on a malformed token, and the exact
``(created_at, id)`` keyset position a ``next_cursor`` resumes from), and
the keyset SQL itself (``tuple_(created_at, id) < cursor`` ordered
``created_at DESC, id DESC`` against real Postgres row-value comparison
semantics). A pure in-memory model cannot vouch for the SQL half, so —
exactly like Property 16 (``test_ownership_indistinguishability.py``) —
this module reuses the integration harness shape (``create_app`` +
``get_session`` dependency override + direct row inserts, flush-only, one
teardown rollback) and skips when docker-compose is not running.

What Hypothesis quantifies over (>=100 examples per property)
-------------------------------------------------------------
* the **list endpoint**: all three LLM sub-resources (``coaching-reports``,
  ``bullet-rewrites``, ``interview-question-sets``) share one pagination
  implementation; each example samples one so a per-feature regression is
  caught;
* the **persisted set**: 0..10 rows whose ``created_at`` values are drawn
  from a deliberately small pool of UTC instants, so duplicate timestamps
  are common and the descending-``id`` tiebreak genuinely decides page
  boundaries (the hand-built ``@example`` forces three rows sharing one
  instant to straddle a ``limit=2`` boundary);
* the **valid limit**: biased toward small values (so multi-page walks are
  the norm) but ranging over the full documented 1..100;
* the **rejection inputs**: limits outside 1..100, and malformed cursors
  (garbage base64, base64 of separator-less text, base64 of
  ``not-a-date|not-a-uuid``) — issued against a match that *does* have a
  persisted row, so a 422 provably returns no data even when data exists.

Each walk example inserts its generated rows (flush, no commit), follows
the real ``next_cursor`` from the first page to ``None`` through real HTTP
GETs, then deletes its rows — examples are independent by construction and
the teardown rollback leaves the database clean. Fixed decoy rows under a
*second* match owned by the same user must never surface in any walk (the
"full set" of the property is the set under *the* match).

Async note: Hypothesis drives sync test functions, so a module-scoped
harness owns a single ``asyncio.Runner`` (one event loop for the app
client and DB session across all examples), mirroring Property 16.
"""

from __future__ import annotations

import asyncio
import base64
import socket
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, Final
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient, Response
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.core.security.passwords import hash_password
from matchlayer_api.db.models import LLMResult, MatchResult, Resume, User
from matchlayer_api.main import create_app
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    BulletRewriteEntry,
    CoachingReport,
    ImprovementAction,
    InterviewQuestion,
    InterviewQuestionCategory,
    InterviewQuestionSet,
)

# ---------------------------------------------------------------------------
# Infra availability (self-contained socket checks — the integration
# conftest is a different pytest package, so its helpers are mirrored
# here rather than imported; same shape as Property 16).
# ---------------------------------------------------------------------------


def _service_available(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not (_service_available("127.0.0.1", 5432) and _service_available("127.0.0.1", 6379)),
    reason="Postgres and Redis required (docker-compose not running)",
)

# The three list surfaces sharing the one pagination implementation
# (Requirement 16.1's sub-resources; Requirement 16.4's pagination).
_FEATURE_PATHS: Final[dict[str, LLMFeature]] = {
    "coaching-reports": LLMFeature.RESUME_COACH,
    "bullet-rewrites": LLMFeature.BULLET_REWRITE,
    "interview-question-sets": LLMFeature.INTERVIEW_QUESTIONS,
}

# The canonical RFC 7807 envelope keys (``core/errors.py``) — asserting the
# 422 body carries exactly these proves "returns no result data": there is
# structurally no ``items`` / ``next_cursor`` in the response.
_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {"type", "title", "detail", "status", "request_id"}
)

RESUME_TEXT: Final[str] = (
    "Backend engineer with production Python and FastAPI services, "
    "PostgreSQL data modeling, and Docker-based deployments on AWS."
)
JOB_DESCRIPTION: Final[str] = (
    "Hiring a backend engineer for Python REST APIs with FastAPI, "
    "PostgreSQL, Docker, and Kubernetes on AWS."
)


def _valid_payload(feature: LLMFeature) -> dict[str, Any]:
    """A minimal schema-valid payload for *feature*.

    Every persisted ``llm_results`` row is a validated LLM output
    (Requirement 9.5), and the list handler re-validates the stored
    payload when projecting it onto the response envelope — so the rows
    this test plants must genuinely conform to each feature's schema.
    """
    if feature is LLMFeature.RESUME_COACH:
        return CoachingReport(
            summary="Solid backend alignment with room to grow.",
            strengths=["Production Python services."],
            gaps=["No Kubernetes exposure."],
            improvements=[
                ImprovementAction(priority=1, action="Add a Kubernetes project."),
                ImprovementAction(priority=2, action="Quantify latency wins."),
                ImprovementAction(priority=3, action="Surface AWS deployments."),
            ],
        ).model_dump(mode="json")
    if feature is LLMFeature.BULLET_REWRITE:
        return BulletRewrite(
            entries=[
                BulletRewriteEntry(
                    original="Shipped the service.",
                    alternatives=["Delivered the FastAPI service to production."],
                    rationale="Names the stack the job description asks for.",
                )
            ]
        ).model_dump(mode="json")
    question = InterviewQuestion(
        question="How do you version REST APIs?",
        category=InterviewQuestionCategory.TECHNICAL,
        reason="The job description lists REST API design.",
    )
    return InterviewQuestionSet(questions=[question] * 5).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Harness: one event loop, one app+session, one user with two matches —
# ``walk`` (clean; each example plants and removes its own rows) and
# ``decoy`` (one fixed persisted row per feature: the rows a walk must
# never leak across matches, and the data a 422 must never return).
# ---------------------------------------------------------------------------


@dataclass
class _Harness:
    runner: asyncio.Runner
    client: AsyncClient
    session: AsyncSession
    token: str
    user_id: Any
    walk_match_id: UUID
    decoy_match_id: UUID
    decoy_result_ids: frozenset[str]

    def request_list(
        self, *, feature_path: str, params: dict[str, Any], match_id: UUID | None = None
    ) -> Response:
        """Issue one list GET on the harness loop."""
        target = match_id if match_id is not None else self.walk_match_id
        return self.runner.run(
            self.client.get(
                f"/api/v1/matches/{target}/{feature_path}",
                headers={"Authorization": f"Bearer {self.token}"},
                params=params,
            )
        )


@dataclass
class _State:
    engine: Any
    session: AsyncSession
    client: AsyncClient
    token: str
    user_id: Any
    walk_match_id: UUID
    decoy_match_id: UUID
    decoy_result_ids: frozenset[str]


def _make_user(prefix: str) -> User:
    return User(
        id=uuid7(),
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password!12345"),
        display_name=prefix,
        failed_login_count=0,
        last_failed_login_at=None,
        locked_until=None,
        deleted_at=None,
    )


async def _insert_match(session: AsyncSession, *, user_id: Any) -> MatchResult:
    """Insert an owned Resume + MatchResult pair (integration-factory shape)."""
    resume = Resume(
        id=uuid7(),
        user_id=user_id,
        original_filename="resume.pdf",
        storage_key=f"{uuid7()}.pdf",
        content_type="application/pdf",
        byte_size=2048,
        extracted_text=RESUME_TEXT,
        extraction_status="succeeded",
        extraction_char_count=len(RESUME_TEXT),
        deleted_at=None,
    )
    session.add(resume)
    await session.flush()
    match = MatchResult(
        id=uuid7(),
        user_id=user_id,
        resume_id=resume.id,
        job_description_text=JOB_DESCRIPTION,
        score=72,
        score_breakdown={
            "similarity_component": 0.7,
            "keyword_coverage_component": 0.75,
            "weight_similarity": 0.6,
            "weight_keyword": 0.4,
            "final_score": 72,
        },
        matched_keywords=[{"term": "python", "weight": 1.0}],
        missing_keywords=[{"term": "kubernetes", "weight": 0.8}],
        suggestions=["Add Kubernetes experience to your resume."],
        scorer_version="test-scorer",
    )
    session.add(match)
    await session.flush()
    return match


async def _insert_results(
    session: AsyncSession,
    *,
    user_id: Any,
    match_result_id: UUID,
    feature: LLMFeature,
    created_ats: list[datetime],
) -> list[LLMResult]:
    """Plant one schema-valid LLM_Result row per generated ``created_at``."""
    payload = _valid_payload(feature)
    rows: list[LLMResult] = []
    for created_at in created_ats:
        row = LLMResult(
            id=uuid7(),
            user_id=user_id,
            match_result_id=match_result_id,
            feature=feature.value,
            prompt_template_version=1,
            llm_model="test-model",
            payload=payload,
            created_at=created_at,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def _remove_rows(session: AsyncSession, rows: list[LLMResult]) -> None:
    """Delete an example's planted rows so examples stay independent."""
    for row in rows:
        await session.delete(row)
    await session.flush()


async def _setup() -> _State:
    """Build the app client and the fixed row population (flush, no commit)."""
    engine = create_async_engine(
        str(get_settings().database_url),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_factory()

    owner = _make_user("p18-owner")
    session.add(owner)
    await session.flush()
    walk_match = await _insert_match(session, user_id=owner.id)
    decoy_match = await _insert_match(session, user_id=owner.id)

    # One fixed persisted row per feature under the decoy match: rows a
    # walk of the *walk* match must never surface, and the data a 422 on
    # the decoy match must never return.
    decoy_ids: set[str] = set()
    for feature in _FEATURE_PATHS.values():
        (decoy_row,) = await _insert_results(
            session,
            user_id=owner.id,
            match_result_id=decoy_match.id,
            feature=feature,
            created_ats=[datetime(2024, 6, 1, 8, 0, 0, tzinfo=UTC)],
        )
        decoy_ids.add(str(decoy_row.id))

    app = create_app()

    async def _override_session() -> Any:
        yield session

    app.dependency_overrides[get_session] = _override_session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    return _State(
        engine=engine,
        session=session,
        client=client,
        token=issue_access_token(sub=str(owner.id)),
        user_id=owner.id,
        walk_match_id=walk_match.id,
        decoy_match_id=decoy_match.id,
        decoy_result_ids=frozenset(decoy_ids),
    )


async def _teardown(state: _State) -> None:
    await state.client.aclose()
    await state.session.rollback()
    await state.session.close()
    await state.engine.dispose()


@pytest.fixture(scope="module")
def harness() -> Iterator[_Harness]:
    """Module-scoped app + fixed rows, one loop for all examples.

    ``loop_factory`` keeps the runner's loop private: with the default
    factory, ``asyncio.Runner`` registers its loop as the *current* event
    loop, which pytest-asyncio's per-test loop management then closes
    between this module's two test functions — killing the harness loop
    mid-module. A factory-created loop is never registered as current, so
    it survives across both tests.
    """
    with asyncio.Runner(loop_factory=asyncio.new_event_loop) as runner:
        state = runner.run(_setup())
        try:
            yield _Harness(
                runner=runner,
                client=state.client,
                session=state.session,
                token=state.token,
                user_id=state.user_id,
                walk_match_id=state.walk_match_id,
                decoy_match_id=state.decoy_match_id,
                decoy_result_ids=state.decoy_result_ids,
            )
        finally:
            runner.run(_teardown(state))


# ---------------------------------------------------------------------------
# Smart generators.
#
# ``created_at`` values are UTC instants drawn from a small per-example pool,
# so duplicate timestamps are common and the descending-``id`` tiebreak is
# genuinely exercised across page boundaries. The valid ``limit`` is biased
# toward values at or below the row count (multi-page walks) while still
# ranging over the documented 1..100.
# ---------------------------------------------------------------------------

_utc_created_ats = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2035, 1, 1),
    timezones=st.just(UTC),
)

# One walk case: (feature path, the rows' created_at values, the page limit).
WalkCase = tuple[str, list[datetime], int]


@st.composite
def _walk_cases(draw: st.DrawFn) -> WalkCase:
    feature_path = draw(st.sampled_from(sorted(_FEATURE_PATHS)))
    row_count = draw(st.integers(min_value=0, max_value=10))
    pool = draw(st.lists(_utc_created_ats, min_size=1, max_size=3))
    created_ats = [draw(st.sampled_from(pool)) for _ in range(row_count)]
    limit = draw(
        st.one_of(
            st.integers(min_value=1, max_value=max(1, row_count)),
            st.integers(min_value=1, max_value=100),
        )
    )
    return feature_path, created_ats, limit


# A hand-built case: three rows share one instant so the ``id`` DESC
# tiebreak straddles a page boundary at ``limit=2``; one later row sorts
# first.
_TS_A = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
_TS_B = datetime(2025, 1, 2, 9, 30, 0, tzinfo=UTC)
_EXAMPLE_CASE: WalkCase = ("coaching-reports", [_TS_A, _TS_A, _TS_A, _TS_B], 2)


def _walk_all_pages(
    harness: _Harness, *, feature_path: str, limit: int, expected_total: int
) -> list[tuple[datetime, UUID]]:
    """Follow the real ``next_cursor`` from the first page to exhaustion.

    Every page is a real HTTP GET through auth → ownership → the keyset
    query against Postgres. Asserts the per-page bounds along the way:
    every page is 200, carries at most ``limit`` items, and every
    non-final page (one that returns a ``next_cursor``) is exactly full.
    """
    collected: list[tuple[datetime, UUID]] = []
    cursor: str | None = None
    pages = 0
    max_pages = expected_total // limit + 2  # every non-final page yields `limit` rows

    while True:
        pages += 1
        assert pages <= max_pages, f"{feature_path}: pagination failed to terminate"

        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        response = harness.request_list(feature_path=feature_path, params=params)
        assert response.status_code == 200

        body = response.json()
        assert "items" in body
        page = body["items"]
        assert len(page) <= limit, "a page must never exceed the requested limit"
        for item in page:
            collected.append((datetime.fromisoformat(item["created_at"]), UUID(item["id"])))

        cursor = body["next_cursor"]
        if cursor is None:
            break
        # A page that advertises another page must itself be full —
        # otherwise the walk would have gaps.
        assert len(page) == limit

    return collected


# ===========================================================================
# Property 18, walk facet: complete, ordered, gap- and duplicate-free.
# ===========================================================================


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_walk_cases())
@example(case=_EXAMPLE_CASE)
def test_walking_pages_is_complete_ordered_and_bounded(
    harness: _Harness,
    case: WalkCase,
) -> None:
    """Property 18 (Requirements 16.4, 16.10), walk facet: for any
    generated set of persisted LLM_Results under a match and any valid
    ``limit`` in 1..100, walking the list endpoint page-by-page yields
    entries in strictly descending ``(created_at, id)`` order with no
    duplicates or gaps, and the concatenation of pages equals the full
    set — never a row from another match."""
    feature_path, created_ats, limit = case
    feature = _FEATURE_PATHS[feature_path]

    rows = harness.runner.run(
        _insert_results(
            harness.session,
            user_id=harness.user_id,
            match_result_id=harness.walk_match_id,
            feature=feature,
            created_ats=created_ats,
        )
    )
    try:
        inserted = [(row.created_at, row.id) for row in rows]
        collected = _walk_all_pages(
            harness, feature_path=feature_path, limit=limit, expected_total=len(inserted)
        )

        # Completeness + correct order + no duplication, in one structural
        # equality: the concatenation of pages equals the full set sorted
        # by (created_at DESC, id DESC).
        expected = sorted(inserted, key=lambda key: (key[0], key[1]), reverse=True)
        assert collected == expected

        # Each planted row appears exactly once (none dropped or
        # duplicated across a page boundary).
        collected_ids = [row_id for (_created_at, row_id) in collected]
        assert len(collected_ids) == len(set(collected_ids)) == len(inserted)

        # Strictly descending adjacency on the composite key — the
        # ordering clause of the property stated directly.
        for earlier, later in pairwise(collected):
            assert (earlier[0], earlier[1]) > (later[0], later[1])

        # The full set is the set under *this* match: the decoy match's
        # persisted rows (same user, same features) never surface.
        assert harness.decoy_result_ids.isdisjoint({str(row_id) for row_id in collected_ids})
    finally:
        harness.runner.run(_remove_rows(harness.session, rows))


# ===========================================================================
# Property 18, rejection facet: out-of-bounds limit / malformed cursor
# → 422 with no result data.
# ===========================================================================


def _b64(text: str) -> str:
    """URL-safe base64 of *text* (how the real codec builds its tokens)."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


# Letters-only parts can never parse as an ISO-8601 timestamp or a UUID.
_alpha_part = st.text(alphabet="GHIJKLMNOPQRSTUVWXYZghijklmnopqrstuvwxyz", min_size=1, max_size=12)

# (1) base64 of a payload with NO "|": the decoder's missing-separator path.
_no_separator_cursor = st.text(min_size=0, max_size=24).filter(lambda s: "|" not in s).map(_b64)

# (2) base64 of "<not-a-date>|<not-a-uuid>": separator present, parts unparseable.
_bad_parts_cursor = st.builds(lambda left, right: _b64(f"{left}|{right}"), _alpha_part, _alpha_part)

# (3) raw garbage that is not even valid base64url.
_garbage_cursor = st.sampled_from(["", "A", "!!", "not-a-valid-cursor", "===="])

_malformed_cursors = st.one_of(_no_separator_cursor, _bad_parts_cursor, _garbage_cursor)

# Limits outside the documented 1..100 (Requirement 16.10).
_bad_limits = st.one_of(
    st.integers(min_value=-50, max_value=0),
    st.integers(min_value=101, max_value=500),
)

# A rejection input: either a bad limit (cursor omitted) or a malformed
# cursor (limit left at its valid default).
_rejections = st.one_of(
    st.tuples(st.just("limit"), _bad_limits),
    st.tuples(st.just("cursor"), _malformed_cursors),
)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(feature_path=st.sampled_from(sorted(_FEATURE_PATHS)), rejection=_rejections)
@example(feature_path="coaching-reports", rejection=("limit", 0))
@example(feature_path="bullet-rewrites", rejection=("limit", 101))
@example(feature_path="interview-question-sets", rejection=("cursor", "not-a-valid-cursor"))
def test_bad_limit_or_malformed_cursor_is_422_with_no_result_data(
    harness: _Harness,
    feature_path: str,
    rejection: tuple[str, int | str],
) -> None:
    """Property 18 (Requirements 16.4, 16.10), rejection facet: for any
    out-of-bounds ``limit`` or malformed/undecodable ``cursor``, the list
    request is rejected with a 422 ``validation_error`` RFC 7807 body that
    carries no result data — issued against a match that *does* have a
    persisted row, so "no data" is proven against existing data."""
    kind, value = rejection
    params: dict[str, Any] = {kind: value}

    response = harness.request_list(
        feature_path=feature_path, params=params, match_id=harness.decoy_match_id
    )

    assert response.status_code == 422
    body = response.json()
    # Exactly the canonical RFC 7807 envelope — structurally no ``items``,
    # no ``next_cursor``, no payload fields: no result data is returned.
    assert set(body) == _ENVELOPE_KEYS
    assert body["type"] == "validation_error"
    assert body["status"] == 422
    # The persisted decoy payload never leaks into the rejection body.
    assert "entries" not in response.text
    assert "questions" not in response.text
