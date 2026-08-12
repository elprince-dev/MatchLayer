"""Feature: phase-3-llm-layer — Property 17.

# Feature: phase-3-llm-layer, Property 17: Persistence round trip

Property 17: Persistence round trip.

    *For any* validated LLM_Result payload, persisting it and issuing a
    GET on its sub-resource path returns a 200 whose envelope carries
    the identical payload, an ``id`` that parses as a UUIDv7 exposed as
    a string, a ``created_at`` in ISO 8601 UTC with ``Z`` suffix, and
    the prompt template version.

**Validates: Requirements 6.5, 7.4, 16.3, 17.9**

Why this drives the real HTTP surface (with Postgres)
-----------------------------------------------------
The guarantee is a round-trip one: what the pipeline persists into
``llm_results`` must come back *identically* through the GET-one
sub-resource route — same payload bytes, the conventions-mandated id
and timestamp formats, and the prompt version the result was produced
under. That can only be proven across the full persistence boundary
(JSONB write → SQLAlchemy read → ``_stored_envelope`` projection →
Pydantic response serialization), so this module reuses the
Postgres-backed harness shape of ``test_ownership_indistinguishability``
(``create_app`` + ``get_session`` dependency override + direct row
inserts, flush-only, rollback at teardown) and skips when
docker-compose is not running. The design's Testing Strategy explicitly
allows Postgres-in-Docker sessions for persistence-dependent
properties.

What Hypothesis quantifies over (>=100 examples)
------------------------------------------------
* the **LLM_Feature** — all three sub-resources (``coaching-reports``,
  ``bullet-rewrites``, ``interview-question-sets``), each with its own
  result schema;
* the **validated payload** — arbitrary schema-valid
  ``CoachingReport`` / ``BulletRewrite`` / ``InterviewQuestionSet``
  instances (bounds-respecting improvement counts with strictly
  increasing priority ranks, 1..3 rewrite alternatives, 5..ceiling
  questions, unicode text across letters/numbers/punctuation/symbols);
* the **prompt template version** — any positive integer;
* the **persistence instant** — arbitrary microsecond-precision UTC
  timestamps (written explicitly so the round trip is checked against a
  known value rather than the server clock).

Each example persists one ``llm_results`` row exactly as the
orchestrator stages it (validated payload dumped to JSONB), issues the
GET on ``/api/v1/matches/{match_id}/{feature}/{result_id}``, and
asserts:

1. the response is 200 and carries the canonical envelope keys;
2. ``result`` equals the persisted payload exactly (JSONB round trip
   loses nothing, adds nothing);
3. ``id`` is a string equal to the persisted row id and parses as a
   UUID whose version is 7;
4. ``created_at`` is an ISO 8601 UTC instant with the ``Z`` suffix
   whose parsed value equals the persisted timestamp;
5. ``prompt_template_version`` equals the version the row was
   persisted under, and the envelope marks the result as a real LLM
   result (``is_fallback`` false, ``fallback_reason`` null — persisted
   rows are never fallbacks, Requirement 9.5).

Async note: Hypothesis drives sync test functions, so a module-scoped
harness owns a single ``asyncio.Runner`` (one event loop for the app
client and DB session across all examples). Rows are only flushed —
never committed — and the teardown rollback leaves the database clean.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import pytest
from httpx import ASGITransport, AsyncClient, Response
from hypothesis import HealthCheck, given, settings
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
# Infra availability (self-contained socket checks — mirrors the other
# Postgres-backed property modules rather than importing across pytest
# packages).
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

# ---------------------------------------------------------------------------
# The three sub-resource paths and their feature enum values.
# ---------------------------------------------------------------------------

_FEATURE_PATHS: Final[dict[str, LLMFeature]] = {
    "coaching-reports": LLMFeature.RESUME_COACH,
    "bullet-rewrites": LLMFeature.BULLET_REWRITE,
    "interview-question-sets": LLMFeature.INTERVIEW_QUESTIONS,
}

# The envelope keys of ``LLMResultEnvelope`` — the exact response shape.
_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "is_fallback", "fallback_reason", "prompt_template_version", "created_at", "result"}
)

RESUME_TEXT: Final[str] = (
    "Backend engineer with production Python and FastAPI services, "
    "PostgreSQL data modeling, and Docker-based deployments on AWS."
)
JOB_DESCRIPTION: Final[str] = (
    "Hiring a backend engineer for Python REST APIs with FastAPI, "
    "PostgreSQL, Docker, and Kubernetes on AWS."
)


# ---------------------------------------------------------------------------
# Harness: one event loop, one app+session, one owner user + match,
# shared by every Hypothesis example. Each example flushes its own
# ``llm_results`` row; nothing is ever committed.
# ---------------------------------------------------------------------------


@dataclass
class _Harness:
    runner: asyncio.Runner
    session: AsyncSession
    client: AsyncClient
    match_id: str
    user_id: Any
    token: str

    def persist_result(
        self,
        *,
        feature: LLMFeature,
        payload: dict[str, Any],
        prompt_template_version: int,
        created_at: datetime,
    ) -> str:
        """Insert one validated LLM_Result row (flush only) and return its id."""

        async def _insert() -> str:
            row = LLMResult(
                id=uuid7(),
                user_id=self.user_id,
                match_result_id=uuid.UUID(self.match_id),
                feature=feature.value,
                prompt_template_version=prompt_template_version,
                llm_model="test-model",
                payload=payload,
                created_at=created_at,
            )
            self.session.add(row)
            await self.session.flush()
            return str(row.id)

        return self.runner.run(_insert())

    def get_one(self, *, feature_path: str, result_id: str) -> Response:
        """GET the persisted result on its sub-resource path."""
        return self.runner.run(
            self.client.get(
                f"/api/v1/matches/{self.match_id}/{feature_path}/{result_id}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        )


@dataclass
class _State:
    engine: Any
    session: AsyncSession
    client: AsyncClient
    match_id: str
    user_id: Any
    token: str


async def _setup() -> _State:
    """Build the app client and the fixed owner rows (flush, no commit)."""
    engine = create_async_engine(
        str(get_settings().database_url),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_factory()

    user = User(
        id=uuid7(),
        email=f"p17-owner-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password!12345"),
        display_name="p17-owner",
        failed_login_count=0,
        last_failed_login_at=None,
        locked_until=None,
        deleted_at=None,
    )
    session.add(user)
    await session.flush()

    resume = Resume(
        id=uuid7(),
        user_id=user.id,
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
        user_id=user.id,
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

    app = create_app()

    async def _override_session() -> Any:
        yield session

    app.dependency_overrides[get_session] = _override_session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    return _State(
        engine=engine,
        session=session,
        client=client,
        match_id=str(match.id),
        user_id=user.id,
        token=issue_access_token(sub=str(user.id)),
    )


async def _teardown(state: _State) -> None:
    await state.client.aclose()
    await state.session.rollback()
    await state.session.close()
    await state.engine.dispose()


@pytest.fixture(scope="module")
def harness() -> Iterator[_Harness]:
    """Module-scoped app + owner rows, one loop for all examples."""
    with asyncio.Runner() as runner:
        state = runner.run(_setup())
        try:
            yield _Harness(
                runner=runner,
                session=state.session,
                client=state.client,
                match_id=state.match_id,
                user_id=state.user_id,
                token=state.token,
            )
        finally:
            runner.run(_teardown(state))


# ---------------------------------------------------------------------------
# Strategies — arbitrary *validated* payloads for each feature schema.
#
# Text alphabet: letters, numbers, punctuation, symbols, and the space
# separator. NUL is invalid inside Postgres JSONB strings (an infra
# limitation, not a schema bound), and surrogate codepoints are already
# excluded by Hypothesis' defaults for these categories. Values are
# constructed *through* the Pydantic models, so whatever normalization
# validation applies (whitespace stripping on constrained fields) is
# baked into the persisted payload — exactly as the pipeline persists
# validated output.
# ---------------------------------------------------------------------------

_text_alphabet = st.characters(
    categories=("L", "N", "P", "S"),
    include_characters=" ",
)

# Non-empty after stripping: at least one non-space character.
_nonempty_text = st.text(alphabet=_text_alphabet, min_size=1, max_size=60).filter(
    lambda s: s.strip() != ""
)
# Plain (unconstrained) strings, e.g. CoachingReport.strengths items and
# BulletRewriteEntry.original — the schema allows any string here.
_plain_text = st.text(alphabet=_text_alphabet, max_size=60)


@st.composite
def _coaching_reports(draw: st.DrawFn) -> CoachingReport:
    """A schema-valid CoachingReport: 3..10 improvements with strictly
    increasing priority ranks (highest → lowest priority order)."""
    count = draw(st.integers(min_value=3, max_value=10))
    ranks = sorted(
        draw(st.sets(st.integers(min_value=1, max_value=1000), min_size=count, max_size=count))
    )
    return CoachingReport(
        summary=draw(_nonempty_text),
        strengths=draw(st.lists(_plain_text, max_size=5)),
        gaps=draw(st.lists(_plain_text, max_size=5)),
        improvements=[
            ImprovementAction(priority=rank, action=draw(_nonempty_text)) for rank in ranks
        ],
    )


@st.composite
def _bullet_rewrites(draw: st.DrawFn) -> BulletRewrite:
    """A schema-valid BulletRewrite: >=1 entries, 1..3 alternatives each."""
    entry_count = draw(st.integers(min_value=1, max_value=5))
    entries = [
        BulletRewriteEntry(
            original=draw(_plain_text),
            alternatives=draw(st.lists(_nonempty_text, min_size=1, max_size=3)),
            rationale=draw(_nonempty_text),
        )
        for _ in range(entry_count)
    ]
    return BulletRewrite(entries=entries)


@st.composite
def _interview_question_sets(draw: st.DrawFn) -> InterviewQuestionSet:
    """A schema-valid InterviewQuestionSet: 5..ceiling questions within
    the per-field length bounds."""
    ceiling = min(8, get_settings().llm_max_questions)
    count = draw(st.integers(min_value=5, max_value=ceiling))
    questions = [
        InterviewQuestion(
            question=draw(_nonempty_text),
            category=draw(st.sampled_from(InterviewQuestionCategory)),
            reason=draw(_nonempty_text),
        )
        for _ in range(count)
    ]
    return InterviewQuestionSet(questions=questions)


@st.composite
def _persistence_cases(draw: st.DrawFn) -> tuple[str, dict[str, Any]]:
    """(feature path, validated payload dict) for a random feature."""
    feature_path = draw(st.sampled_from(sorted(_FEATURE_PATHS)))
    payload_strategies = {
        "coaching-reports": _coaching_reports(),
        "bullet-rewrites": _bullet_rewrites(),
        "interview-question-sets": _interview_question_sets(),
    }
    model = draw(payload_strategies[feature_path])
    return feature_path, model.model_dump(mode="json")


# Arbitrary microsecond-precision UTC persistence instants.
_created_at = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2035, 12, 31, 23, 59, 59),
    timezones=st.just(UTC),
)

_prompt_version = st.integers(min_value=1, max_value=999)


# ---------------------------------------------------------------------------
# Property 17 — persist, GET, and the envelope round-trips exactly.
# ---------------------------------------------------------------------------


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    case=_persistence_cases(),
    prompt_template_version=_prompt_version,
    created_at=_created_at,
)
def test_persisted_result_round_trips_through_get(
    harness: _Harness,
    case: tuple[str, dict[str, Any]],
    prompt_template_version: int,
    created_at: datetime,
) -> None:
    """Property 17 (Requirements 6.5, 7.4, 16.3, 17.9): for any validated
    LLM_Result payload, persisting it and issuing a GET on its
    sub-resource path returns a 200 whose envelope carries the identical
    payload, an ``id`` that parses as a UUIDv7 exposed as a string, a
    ``created_at`` in ISO 8601 UTC with ``Z`` suffix, and the prompt
    template version."""
    feature_path, payload = case

    result_id = harness.persist_result(
        feature=_FEATURE_PATHS[feature_path],
        payload=payload,
        prompt_template_version=prompt_template_version,
        created_at=created_at,
    )

    response = harness.get_one(feature_path=feature_path, result_id=result_id)

    # A 200 carrying exactly the envelope shape.
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _ENVELOPE_KEYS

    # The identical payload — the JSONB write → ORM read → envelope
    # projection → response serialization chain loses nothing and adds
    # nothing (Requirements 6.5, 7.4, 16.3).
    assert body["result"] == payload

    # ``id``: the persisted row id, exposed as a string that parses as a
    # UUID of version 7 (conventions.md "IDs"; Requirement 16.3).
    assert isinstance(body["id"], str)
    assert body["id"] == result_id
    assert uuid.UUID(body["id"]).version == 7

    # ``created_at``: ISO 8601 UTC with the ``Z`` suffix, equal to the
    # persisted instant (conventions.md "Timestamps"; Requirement 17.9).
    assert isinstance(body["created_at"], str)
    assert body["created_at"].endswith("Z")
    assert datetime.fromisoformat(body["created_at"]) == created_at

    # The prompt template version the result was produced under
    # (Requirement 17.9), on a real (non-fallback) persisted result.
    assert body["prompt_template_version"] == prompt_template_version
    assert body["is_fallback"] is False
    assert body["fallback_reason"] is None
