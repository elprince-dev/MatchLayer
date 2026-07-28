"""Integration tests for Degraded_Mode (phase-2-nlp-embeddings, task 11.2).

Validates phase-2 Requirements 7.1, 7.2, 7.5, 7.6, 7.7:

* An app instance whose semantic pipeline failed to load (broken model
  path) **starts and serves** — the real ``load_semantic_pipeline()`` is
  run against a broken path (the exact call the FastAPI lifespan makes)
  and returns ``None`` instead of raising (Requirement 7.1).
* Every match request in Degraded_Mode completes through the Phase 1
  engine with the Phase 1 ``scorer_version`` stamp and a valid response
  schema — never a 5xx (Requirements 7.2, 7.6).
* ``GET /healthz`` reports ``"semantic_scoring": "unavailable"`` while
  still returning 200 — a degraded instance is serving, so orchestration
  must not restart-loop it (Requirement 7.5).
* No embedding rows are written for degraded requests (Requirement 7.2).
* A restart with a working model **recovers** to the Phase 2 pipeline —
  new requests carry the v2 stamp and persist embeddings — while rows
  created during the degraded window are retained unchanged
  (Requirement 7.7).

Harness: the shared integration fixtures (``client_with_session`` /
``db_session`` / ``factory_user``) drive the wired app against the
docker-compose Postgres + Redis; the module skips when either service is
down, mirroring ``test_matches_api.py`` (every match route runs the
Redis-backed rate limiter).

The "working model" after the simulated restart is a real
:class:`Semantic_Match_Scorer` composition — the committed v2 lexicon, a
real ``Skill_Extractor`` over a tokenizer-only spaCy pipeline, the real
``Semantic_Scorer`` and ``Embedding_Service`` — over the deterministic
384-dimension stub ``Text_Encoder`` (matching the pgvector DDL literal),
installed as the semantic adapter's real module state. Only the
SentenceTransformer artifact itself is substituted: per the design's test
strategy the real model is exercised by the Eval_Runner, while this test
exercises the full service/persistence path around it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
import spacy
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import MatchEmbedding, Resume, ResumeEmbedding, User
from matchlayer_api.ml import scorer_adapter, semantic_adapter
from matchlayer_api.ml.semantic_adapter import (
    SemanticPipeline,
    load_semantic_pipeline,
    semantic_available,
)
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import load_lexicon_v2
from matchlayer_api.scoring.scorer import Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor
from matchlayer_api.scoring.versioning import semantic_scorer_version

from .conftest import UserFactory, postgres_available, redis_available, unique_email

pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)

# The pgvector DDL literal — the stub encoder must match it so recovered-mode
# vectors are accepted by the Vector_Store (Requirement 1.6).
_VECTOR_DIMENSION = 384

# Realistic, deterministic texts (the committed eyeball strong-match pair):
# both mention several v2-lexicon skills, so the recovered Phase 2 path has a
# non-empty analyzed set and never trips the skill_extraction_empty fallback.
RESUME_TEXT = (
    "Senior Backend Engineer with eight years of experience building scalable "
    "REST APIs in Python. I have shipped production services with the FastAPI "
    "framework, modeled relational data in PostgreSQL, and containerized every "
    "service with Docker. I deployed and operated these systems on AWS, owning "
    "the CI/CD pipeline end to end and writing thorough pytest suites."
)

JOB_DESCRIPTION = (
    "We are hiring a Senior Backend Engineer to design and build scalable REST "
    "APIs in Python using the FastAPI framework. You will model data in "
    "PostgreSQL, containerize services with Docker, and deploy to AWS. Strong "
    "experience with CI/CD pipelines and automated testing with pytest is "
    "required."
)

# The full match response field set (Requirement 8.7 / phase-2 9.1: the
# schema is valid and unchanged in Degraded_Mode).
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


ResumeFactory = Callable[..., Awaitable[Resume]]


@pytest_asyncio.fixture
async def factory_resume(db_session: AsyncSession) -> ResumeFactory:
    """Insert an extractable ``resumes`` row (mirrors test_matches_api)."""

    async def _build(
        *,
        user_id: object,
        extracted_text: str = RESUME_TEXT,
        deleted_at: datetime | None = None,
    ) -> Resume:
        resume = Resume(
            id=uuid7(),
            user_id=user_id,
            original_filename="resume.pdf",
            storage_key=f"{uuid7()}.pdf",
            content_type="application/pdf",
            byte_size=2048,
            extracted_text=extracted_text,
            extraction_status="succeeded",
            extraction_char_count=len(extracted_text),
            deleted_at=deleted_at,
        )
        db_session.add(resume)
        await db_session.flush()
        return resume

    return _build


@pytest.fixture
def degraded_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run the REAL startup loader against a broken model path.

    This is the exact call the FastAPI lifespan makes at startup. The
    loader must swallow the artifact failure and leave the process in
    Degraded_Mode (Requirement 7.1) — an exception here would mean a
    missing model artifact takes the instance down, which is precisely
    what the requirement forbids. Module state is restored afterwards via
    monkeypatch's teardown of ``_pipeline``.
    """
    broken_path = tmp_path / "missing-model"
    broken_path.mkdir()
    settings = semantic_adapter.get_settings().model_copy(
        update={"embedding_model_path": str(broken_path)}
    )
    monkeypatch.setattr(semantic_adapter, "get_settings", lambda: settings)
    # Snapshot/restore the module state through monkeypatch before the real
    # loader overwrites it.
    monkeypatch.setattr(semantic_adapter, "_pipeline", semantic_adapter._pipeline)

    pipeline = load_semantic_pipeline()

    assert pipeline is None, "a broken model path must degrade, not raise"
    assert semantic_available() is False


def _working_pipeline() -> SemanticPipeline:
    """A real Phase 2 composition over the 384-dim stub encoder."""
    lexicon = load_lexicon_v2()
    scorer_version = semantic_scorer_version(
        lexicon.lexicon_version,
        "stub-model",
        "stub-rev",
        "blank_en",
        "0.0.0",
    )
    scorer = Semantic_Match_Scorer(
        lexicon,
        Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=50),
        Semantic_Scorer(),
        w_similarity=0.6,
        w_keyword=0.4,
        max_suggestions=10,
        scorer_version=scorer_version,
    )
    return SemanticPipeline(
        embedding_service=Embedding_Service(
            Stub_Text_Encoder(dimension=_VECTOR_DIMENSION, max_tokens=64)
        ),
        scorer=scorer,
        model_name="stub-model",
        model_revision="stub-rev",
    )


def _install_working_pipeline(monkeypatch: pytest.MonkeyPatch) -> SemanticPipeline:
    """Simulate a process restart with a working model artifact (Req 7.7)."""
    pipeline = _working_pipeline()
    monkeypatch.setattr(semantic_adapter, "_pipeline", pipeline)
    assert semantic_available() is True
    return pipeline


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


def _phase1_version() -> str:
    """The stamp the real Phase 1 engine produces (never hard-coded)."""
    return scorer_adapter.score("python developer", "python engineer").scorer_version


async def _count(db_session: AsyncSession, model: type) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(model)) or 0)


# ---------------------------------------------------------------------------
# Degraded instance: serves Phase 1, health reports unavailable, no vectors
# (Requirements 7.1, 7.2, 7.5, 7.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_instance_serves_phase1_with_unavailable_health(
    degraded_mode: None,
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """Broken model path → serving instance, Phase 1 stamps, no embeddings."""
    # /healthz still 200 — degraded is serving, not dead (Requirement 7.5).
    health = await client_with_session.get("/healthz")
    assert health.status_code == 200
    assert health.json()["semantic_scoring"] == "unavailable"

    user, token = await _make_user_and_token(factory_user, "degraded")
    resume = await factory_resume(user_id=user.id)

    res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )

    # Completes — never a 5xx (Requirement 7.2) — with a valid schema (7.6).
    assert res.status_code == 201
    body = res.json()
    assert set(body.keys()) == _MATCH_RESPONSE_FIELDS
    assert isinstance(body["score"], int) and 0 <= body["score"] <= 100

    # Phase 1 engine, Phase 1 stamp (Requirements 7.2, 7.6).
    assert body["scorer_version"] == _phase1_version()
    assert not body["scorer_version"].startswith("2.0.0")
    assert body["score_breakdown"]["similarity_method"] == "tfidf"

    # No embedding rows for degraded requests (Requirement 7.2).
    assert await _count(db_session, ResumeEmbedding) == 0
    assert await _count(db_session, MatchEmbedding) == 0


# ---------------------------------------------------------------------------
# Restart with a working model recovers; degraded rows retained unchanged
# (Requirement 7.7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_with_working_model_recovers_and_retains_degraded_rows(
    degraded_mode: None,
    monkeypatch: pytest.MonkeyPatch,
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """Degraded rows survive verbatim; new requests use the Phase 2 pipeline."""
    user, token = await _make_user_and_token(factory_user, "recover")
    resume = await factory_resume(user_id=user.id)

    # --- A match created during the degraded window --------------------
    degraded_res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )
    assert degraded_res.status_code == 201
    degraded_body = degraded_res.json()
    assert degraded_body["scorer_version"] == _phase1_version()
    assert await _count(db_session, ResumeEmbedding) == 0

    # --- "Restart" with a working model (Requirement 7.7) --------------
    pipeline = _install_working_pipeline(monkeypatch)

    health = await client_with_session.get("/healthz")
    assert health.status_code == 200
    assert health.json()["semantic_scoring"] == "available"

    recovered_res = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )
    assert recovered_res.status_code == 201
    recovered_body = recovered_res.json()

    # The Phase 2 pipeline produced it: v2 stamp + semantic method.
    assert recovered_body["scorer_version"] == pipeline.scorer.scorer_version
    assert recovered_body["scorer_version"].startswith("2.0.0")
    assert recovered_body["score_breakdown"]["similarity_method"] == "semantic-embedding"

    # Embeddings now persist: the resume vector (stamped with the loaded
    # model identity) and the recovered match's JD vector — and only that
    # match's (the degraded row still has none).
    stored_resume_embeddings = (await db_session.execute(select(ResumeEmbedding))).scalars().all()
    assert len(stored_resume_embeddings) == 1
    assert stored_resume_embeddings[0].resume_id == resume.id
    assert stored_resume_embeddings[0].model_name == "stub-model"
    assert stored_resume_embeddings[0].model_revision == "stub-rev"

    stored_match_embeddings = (await db_session.execute(select(MatchEmbedding))).scalars().all()
    assert len(stored_match_embeddings) == 1
    assert str(stored_match_embeddings[0].match_result_id) == recovered_body["id"]

    # --- Degraded-window rows retained unchanged (Requirement 7.7) -----
    get_res = await client_with_session.get(
        f"/api/v1/matches/{degraded_body['id']}", headers=_auth(token)
    )
    assert get_res.status_code == 200
    retained = get_res.json()
    assert retained["score"] == degraded_body["score"]
    assert retained["scorer_version"] == degraded_body["scorer_version"]
    assert retained["score_breakdown"] == degraded_body["score_breakdown"]
