"""Contract-continuity integration tests (phase-2-nlp-embeddings, task 12.2).

Validates phase-2 Requirements 9.1, 9.2, 9.3, 9.4, 9.6, 9.8, 6.4, 6.5, 6.6:

* **OpenAPI snapshot diff** (Req 9.1, 9.2, 9.8): every Phase 1 response
  field keeps its name, type, and required status; the Phase 2 additions
  (``similarity_method`` on the breakdown, ``semantic_scoring`` on
  ``/healthz``) are optional-only; and no path, query/header parameter, or
  schema property exposes embeddings or vectors. The Phase 1 expectations
  are frozen inline (the "snapshot") rather than read from the live code,
  so a contract-breaking rename/removal fails against this table.
* **Pre-Phase-2 rows returned verbatim** (Req 9.4, 6.4): a stored
  Match_Result whose breakdown JSONB predates ``similarity_method`` is
  served with every stored value unchanged, the new field ``null``, and
  the stored row is never rewritten.
* **Idempotency-Key replay** (Req 9.6, 6.5): a replayed POST returns the
  stored response without invoking the Phase 2 pipeline (counted at the
  encoder) and without creating a second row.
* **Soft-delete retains embeddings** (Req 9.3): deleting a resume or a
  match leaves its stored Embedding row in place (Phase 1 retention
  semantics extended to vectors; purge is Phase 7).
* **One cross-process determinism example** (Req 6.6): an independently
  spawned Python process scoring the identical inputs through an
  independently constructed Phase 2 composition produces the identical
  full result.

Gating: the OpenAPI and cross-process tests need no infrastructure and
always run; the DB/HTTP tests skip without the docker-compose Postgres +
Redis, mirroring ``test_matches_api.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import spacy
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import MatchEmbedding, MatchResult, Resume, ResumeEmbedding, User
from matchlayer_api.main import create_app
from matchlayer_api.ml import semantic_adapter
from matchlayer_api.ml.semantic_adapter import SemanticPipeline
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import load_lexicon_v2
from matchlayer_api.scoring.scorer import ScoreResult, Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor
from matchlayer_api.scoring.versioning import semantic_scorer_version

from .conftest import UserFactory, postgres_available, redis_available, unique_email

# Gate for the DB/HTTP tests only — the schema and cross-process tests run
# everywhere (applied per-test via this marker rather than pytestmark).
_requires_infra = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)

_VECTOR_DIMENSION = 384

RESUME_TEXT = (
    "Senior Backend Engineer with eight years of experience building scalable "
    "REST APIs in Python. I have shipped production services with the FastAPI "
    "framework, modeled relational data in PostgreSQL, and containerized every "
    "service with Docker."
)

JOB_DESCRIPTION = (
    "We are hiring a Senior Backend Engineer to design and build scalable REST "
    "APIs in Python using the FastAPI framework. You will model data in "
    "PostgreSQL, containerize services with Docker, and deploy to AWS."
)


# ---------------------------------------------------------------------------
# THE PHASE 1 CONTRACT SNAPSHOT (frozen expectations, not read from code)
# ---------------------------------------------------------------------------

# MatchResponse: exactly these ten fields, all required (Phase 1 Req 8.7).
_MATCH_RESPONSE_REQUIRED = {
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

# ScoreBreakdownOut: the five Phase 1 fields with their JSON Schema types,
# all required (Phase 1 Req 5.5 shape).
_BREAKDOWN_PHASE1_TYPES = {
    "similarity_component": "number",
    "keyword_coverage_component": "number",
    "weight_similarity": "number",
    "weight_keyword": "number",
    "final_score": "integer",
}

# ResumeResponse: exactly these seven fields, all required (Phase 1 Req 4.2).
_RESUME_RESPONSE_REQUIRED = {
    "id",
    "original_filename",
    "content_type",
    "byte_size",
    "extraction_status",
    "created_at",
    "updated_at",
}

# The Phase 2 additions, all of which must be optional-only (phase-2 Req 9.1).
_PHASE2_OPTIONAL_ADDITIONS = {"similarity_method"}

# Structural names that would expose vector internals if they ever appeared
# in a path, parameter name, or schema property name (phase-2 Req 9.8).
_FORBIDDEN_SURFACE_SUBSTRINGS = ("embedding", "vector")


@pytest.fixture(scope="module")
def openapi_schema() -> dict[str, Any]:
    """The wired app's generated OpenAPI document (no lifespan needed)."""
    return create_app().openapi()


def _component(schema: dict[str, Any], name: str) -> dict[str, Any]:
    component = schema["components"]["schemas"].get(name)
    assert component is not None, f"component schema {name!r} missing from OpenAPI"
    return dict(component)


def test_match_response_keeps_every_phase1_field_required(
    openapi_schema: dict[str, Any],
) -> None:
    """MatchResponse: the ten Phase 1 fields, all present and required (Req 9.1)."""
    component = _component(openapi_schema, "MatchResponse")
    assert set(component["properties"]) == _MATCH_RESPONSE_REQUIRED
    assert set(component.get("required", [])) == _MATCH_RESPONSE_REQUIRED


def test_breakdown_phase1_fields_keep_type_and_required_status(
    openapi_schema: dict[str, Any],
) -> None:
    """ScoreBreakdownOut: Phase 1 names/types/required intact; additions optional.

    Validates phase-2 Requirements 9.1, 9.5: the five Phase 1 breakdown
    fields keep their exact JSON Schema types and remain required, while
    ``similarity_method`` — the one sanctioned Phase 2 addition — is
    present but NOT required (nullable, defaulting to null on pre-Phase-2
    rows).
    """
    component = _component(openapi_schema, "ScoreBreakdownOut")
    properties = component["properties"]
    required = set(component.get("required", []))

    for field, expected_type in _BREAKDOWN_PHASE1_TYPES.items():
        assert field in properties, f"Phase 1 breakdown field {field!r} was removed"
        assert properties[field].get("type") == expected_type, (
            f"Phase 1 breakdown field {field!r} changed type",
            properties[field],
        )
        assert field in required, f"Phase 1 breakdown field {field!r} became optional"

    # The full property set is Phase 1 + the sanctioned optional additions.
    assert set(properties) == set(_BREAKDOWN_PHASE1_TYPES) | _PHASE2_OPTIONAL_ADDITIONS
    for addition in _PHASE2_OPTIONAL_ADDITIONS:
        assert addition not in required, (
            f"Phase 2 addition {addition!r} must be optional-only (phase-2 Req 9.1)"
        )


def test_resume_response_unchanged(openapi_schema: dict[str, Any]) -> None:
    """ResumeResponse: the seven Phase 1 fields, all required, no additions."""
    component = _component(openapi_schema, "ResumeResponse")
    assert set(component["properties"]) == _RESUME_RESPONSE_REQUIRED
    assert set(component.get("required", [])) == _RESUME_RESPONSE_REQUIRED


def test_healthz_semantic_scoring_is_an_optional_addition(
    openapi_schema: dict[str, Any],
) -> None:
    """The /healthz ``semantic_scoring`` field is a bounded, additive enum.

    Phase 2's only other surface change (phase-2 Req 7.5): a new
    response-only field with exactly the two documented values. The Phase 1
    ``status`` field keeps its name, its ``Literal["ok"]`` shape, and its
    Phase 1 required status — which is *not* required, because it carries
    ``default="ok"`` and JSON Schema omits defaulted fields from
    ``required``. The addition never alters that existing field.
    """
    # The health response model is registered under its component name.
    component = _component(openapi_schema, "HealthResponse")
    status_property = component["properties"]["status"]
    assert status_property.get("const") == "ok"
    assert status_property.get("default") == "ok"
    assert "status" not in set(component.get("required", []))
    semantic = component["properties"]["semantic_scoring"]
    assert set(semantic.get("enum", [])) == {"available", "unavailable"}


def _walk_json(node: object) -> list[tuple[str, object]]:
    """Yield ``(key, value)`` for every mapping entry in a nested JSON tree."""
    found: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((str(key), value))
            found.extend(_walk_json(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_json(item))
    return found


def test_no_endpoint_parameter_or_property_exposes_embeddings(
    openapi_schema: dict[str, Any],
) -> None:
    """No embedding-exposing path, parameter, or schema property (Req 9.8).

    Structural names only — descriptions may legitimately *mention* the
    words (e.g. the healthz field's prose), but no route path, no
    query/header parameter name, and no request/response property name may
    carry vector internals to a client.
    """
    # Paths.
    offenders = [
        path
        for path in openapi_schema["paths"]
        if any(term in path.lower() for term in _FORBIDDEN_SURFACE_SUBSTRINGS)
    ]
    assert not offenders, f"embedding-exposing endpoint(s): {offenders}"

    # Parameter names (query, header, path, cookie) across every operation.
    for path, operations in openapi_schema["paths"].items():
        for key, value in _walk_json(operations):
            if key == "parameters" and isinstance(value, list):
                bad = [
                    param["name"]
                    for param in value
                    if isinstance(param, dict)
                    and any(
                        term in str(param.get("name", "")).lower()
                        for term in _FORBIDDEN_SURFACE_SUBSTRINGS
                    )
                ]
                assert not bad, f"embedding-exposing parameter(s) on {path}: {bad}"

    # Schema property names across every component.
    for name, component in openapi_schema["components"]["schemas"].items():
        for key, value in _walk_json(component):
            if key == "properties" and isinstance(value, dict):
                bad = [
                    prop
                    for prop in value
                    if any(term in prop.lower() for term in _FORBIDDEN_SURFACE_SUBSTRINGS)
                ]
                assert not bad, f"embedding-exposing propert(ies) on {name}: {bad}"


# ---------------------------------------------------------------------------
# Cross-process determinism (phase-2 Requirement 6.6)
# ---------------------------------------------------------------------------

# Executed by BOTH this process and an independently spawned interpreter:
# identical inputs + identical composition parameters must produce the
# identical full result, byte-for-byte as canonical JSON.
_DETERMINISM_SCRIPT = """
import json
import sys

import spacy

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import load_lexicon_v2
from matchlayer_api.scoring.scorer import Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor
from matchlayer_api.scoring.versioning import semantic_scorer_version

resume_text, job_description = sys.argv[1], sys.argv[2]

lexicon = load_lexicon_v2()
scorer = Semantic_Match_Scorer(
    lexicon,
    Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=50),
    Semantic_Scorer(),
    w_similarity=0.6,
    w_keyword=0.4,
    max_suggestions=10,
    scorer_version=semantic_scorer_version(
        lexicon.lexicon_version, "stub-model", "stub-rev", "blank_en", "0.0.0"
    ),
)
service = Embedding_Service(Stub_Text_Encoder(dimension=384, max_tokens=64))
result = scorer.score(
    resume_text,
    job_description,
    service.embed(resume_text),
    service.embed(job_description),
)
print(
    json.dumps(
        {
            "score": result.score,
            "breakdown": {
                "similarity_component": result.breakdown.similarity_component,
                "keyword_coverage_component": result.breakdown.keyword_coverage_component,
                "weight_similarity": result.breakdown.weight_similarity,
                "weight_keyword": result.breakdown.weight_keyword,
                "final_score": result.breakdown.final_score,
                "similarity_method": result.breakdown.similarity_method,
            },
            "matched": [[kw.term, kw.weight] for kw in result.matched_keywords],
            "missing": [[kw.term, kw.weight] for kw in result.missing_keywords],
            "suggestions": [[s.keyword, s.text] for s in result.suggestions],
            "scorer_version": result.scorer_version,
        },
        sort_keys=True,
    )
)
"""


def test_cross_process_determinism_example() -> None:
    """A second interpreter reproduces the identical full result (Req 6.6).

    The child process constructs its own lexicon, extractor, scorer, and
    stub-encoder embedding service from scratch — nothing is shared with
    this process except the source code and the inputs — and both sides
    serialise the complete result to canonical JSON for comparison.
    """
    tests_dir = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = tests_dir + os.pathsep + env.get("PYTHONPATH", "")

    child = subprocess.run(
        [sys.executable, "-c", _DETERMINISM_SCRIPT, RESUME_TEXT, JOB_DESCRIPTION],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=180,
    )
    assert child.returncode == 0, f"child process failed: {child.stderr}"
    child_result = json.loads(child.stdout)

    # The identical computation, in this process, independently composed.
    lexicon = load_lexicon_v2()
    scorer = Semantic_Match_Scorer(
        lexicon,
        Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=50),
        Semantic_Scorer(),
        w_similarity=0.6,
        w_keyword=0.4,
        max_suggestions=10,
        scorer_version=semantic_scorer_version(
            lexicon.lexicon_version, "stub-model", "stub-rev", "blank_en", "0.0.0"
        ),
    )
    service = Embedding_Service(Stub_Text_Encoder(dimension=_VECTOR_DIMENSION, max_tokens=64))
    result: ScoreResult = scorer.score(
        RESUME_TEXT,
        JOB_DESCRIPTION,
        service.embed(RESUME_TEXT),
        service.embed(JOB_DESCRIPTION),
    )
    parent_result = {
        "score": result.score,
        "breakdown": {
            "similarity_component": result.breakdown.similarity_component,
            "keyword_coverage_component": result.breakdown.keyword_coverage_component,
            "weight_similarity": result.breakdown.weight_similarity,
            "weight_keyword": result.breakdown.weight_keyword,
            "final_score": result.breakdown.final_score,
            "similarity_method": result.breakdown.similarity_method,
        },
        "matched": [[kw.term, kw.weight] for kw in result.matched_keywords],
        "missing": [[kw.term, kw.weight] for kw in result.missing_keywords],
        "suggestions": [[s.keyword, s.text] for s in result.suggestions],
        "scorer_version": result.scorer_version,
    }

    assert child_result == parent_result


# ---------------------------------------------------------------------------
# DB/HTTP tests (gated on the docker-compose Postgres + Redis)
# ---------------------------------------------------------------------------


class _CountingEncoder(Stub_Text_Encoder):
    """A stub encoder that counts every ``encode`` call.

    The replay test's "without invoking the Phase 2 pipeline" assertion is
    grounded here: a replayed POST must add zero encode calls.
    """

    def __init__(self) -> None:
        super().__init__(dimension=_VECTOR_DIMENSION, max_tokens=64)
        self.encode_calls = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls += 1
        return super().encode(texts)


def _install_working_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SemanticPipeline, _CountingEncoder]:
    """Install a real Phase 2 composition (counting encoder) as module state."""
    encoder = _CountingEncoder()
    lexicon = load_lexicon_v2()
    scorer = Semantic_Match_Scorer(
        lexicon,
        Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=50),
        Semantic_Scorer(),
        w_similarity=0.6,
        w_keyword=0.4,
        max_suggestions=10,
        scorer_version=semantic_scorer_version(
            lexicon.lexicon_version, "stub-model", "stub-rev", "blank_en", "0.0.0"
        ),
    )
    pipeline = SemanticPipeline(
        embedding_service=Embedding_Service(encoder),
        scorer=scorer,
        model_name="stub-model",
        model_revision="stub-rev",
    )
    monkeypatch.setattr(semantic_adapter, "_pipeline", pipeline)
    return pipeline, encoder


ResumeFactory = Callable[..., Awaitable[Resume]]


@pytest_asyncio.fixture
async def factory_resume(db_session: AsyncSession) -> ResumeFactory:
    """Insert an extractable ``resumes`` row (mirrors test_matches_api)."""

    async def _build(*, user_id: object) -> Resume:
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
        db_session.add(resume)
        await db_session.flush()
        return resume

    return _build


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


# A frozen pre-Phase-2 stored row: breakdown JSONB with NO similarity_method
# key and a Phase 1 scorer_version stamp — exactly what Phase 1 wrote.
_PRE_PHASE2_BREAKDOWN = {
    "similarity_component": 0.42,
    "keyword_coverage_component": 0.5,
    "weight_similarity": 0.6,
    "weight_keyword": 0.4,
    "final_score": 45,
}
_PRE_PHASE2_KEYWORDS = [{"term": "python", "weight": 1.0}]
_PRE_PHASE2_MISSING = [{"term": "docker", "weight": 0.9}]
_PRE_PHASE2_SUGGESTIONS = [{"keyword": "docker", "text": "Add Docker experience."}]
_PRE_PHASE2_VERSION = "1.0.0+lex.v1"


@_requires_infra
@pytest.mark.asyncio
async def test_pre_phase2_match_result_returned_verbatim_and_never_rewritten(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """A stored Phase 1 row is served verbatim; its JSONB is never rewritten.

    Validates phase-2 Requirements 9.4, 6.4: every stored value comes back
    unchanged, the additive ``similarity_method`` is ``null`` (absence
    implies TF-IDF), and re-reading the row shows the stored breakdown
    still has no ``similarity_method`` key — retrieval does not migrate
    old rows.
    """
    user, token = await _make_user_and_token(factory_user, "prephase2")
    resume = await factory_resume(user_id=user.id)

    now = datetime.now(UTC)
    stored = MatchResult(
        id=uuid7(),
        user_id=user.id,
        resume_id=resume.id,
        job_description_text=JOB_DESCRIPTION,
        score=45,
        score_breakdown=dict(_PRE_PHASE2_BREAKDOWN),
        matched_keywords=list(_PRE_PHASE2_KEYWORDS),
        missing_keywords=list(_PRE_PHASE2_MISSING),
        suggestions=list(_PRE_PHASE2_SUGGESTIONS),
        scorer_version=_PRE_PHASE2_VERSION,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    db_session.add(stored)
    await db_session.flush()

    res = await client_with_session.get(f"/api/v1/matches/{stored.id}", headers=_auth(token))

    assert res.status_code == 200
    body = res.json()
    assert body["score"] == 45
    assert body["scorer_version"] == _PRE_PHASE2_VERSION
    assert body["matched_keywords"] == _PRE_PHASE2_KEYWORDS
    assert body["missing_keywords"] == _PRE_PHASE2_MISSING
    assert body["suggestions"] == _PRE_PHASE2_SUGGESTIONS
    # Every Phase 1 breakdown value verbatim; the addition is null.
    for field, value in _PRE_PHASE2_BREAKDOWN.items():
        assert body["score_breakdown"][field] == value
    assert body["score_breakdown"]["similarity_method"] is None

    # The stored JSONB was not rewritten by the read (Req 6.4, 9.4).
    row = (
        await db_session.execute(select(MatchResult).where(MatchResult.id == stored.id))
    ).scalar_one()
    assert row.score_breakdown == _PRE_PHASE2_BREAKDOWN
    assert "similarity_method" not in row.score_breakdown


@_requires_infra
@pytest.mark.asyncio
async def test_idempotency_replay_skips_the_phase2_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """A replayed POST returns the stored response with zero pipeline work.

    Validates phase-2 Requirements 9.6, 6.5: the second POST with the same
    Idempotency-Key returns the identical body, adds no encode calls at
    the pipeline's encoder, and creates no second Match_Result (and no
    second JD Embedding).
    """
    _pipeline, encoder = _install_working_pipeline(monkeypatch)
    user, token = await _make_user_and_token(factory_user, "replay")
    resume = await factory_resume(user_id=user.id)
    payload = {"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION}
    headers = {**_auth(token), "Idempotency-Key": f"contract-{uuid7()}"}

    first = await client_with_session.post("/api/v1/matches", headers=headers, json=payload)
    assert first.status_code == 201
    assert first.json()["scorer_version"].startswith("2.0.0")
    calls_after_first = encoder.encode_calls
    assert calls_after_first > 0  # the first request really used the pipeline

    replay = await client_with_session.post("/api/v1/matches", headers=headers, json=payload)

    assert replay.status_code == 201
    assert replay.json() == first.json()
    # Zero additional pipeline work on replay (Req 6.5).
    assert encoder.encode_calls == calls_after_first

    match_ids = (await db_session.execute(select(MatchResult.id))).scalars().all()
    assert len(match_ids) == 1
    jd_embeddings = (await db_session.execute(select(MatchEmbedding))).scalars().all()
    assert len(jd_embeddings) == 1


@_requires_infra
@pytest.mark.asyncio
async def test_soft_delete_retains_stored_embeddings(
    monkeypatch: pytest.MonkeyPatch,
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume: ResumeFactory,
    db_session: AsyncSession,
) -> None:
    """Soft-deleting a resume or match leaves its Embedding rows in place.

    Validates phase-2 Requirement 9.3: soft delete is a visibility flag,
    not a purge — stored vectors survive both deletes (hard deletion is a
    Phase 7 concern, per ``security.md`` data retention).
    """
    _install_working_pipeline(monkeypatch)
    user, token = await _make_user_and_token(factory_user, "softdel")
    resume = await factory_resume(user_id=user.id)

    created = await client_with_session.post(
        "/api/v1/matches",
        headers=_auth(token),
        json={"resume_id": str(resume.id), "job_description": JOB_DESCRIPTION},
    )
    assert created.status_code == 201
    match_id = created.json()["id"]

    # Both embeddings exist before any delete.
    assert len((await db_session.execute(select(ResumeEmbedding))).scalars().all()) == 1
    assert len((await db_session.execute(select(MatchEmbedding))).scalars().all()) == 1

    delete_match = await client_with_session.delete(
        f"/api/v1/matches/{match_id}", headers=_auth(token)
    )
    assert delete_match.status_code == 204
    delete_resume = await client_with_session.delete(
        f"/api/v1/resumes/{resume.id}", headers=_auth(token)
    )
    assert delete_resume.status_code == 204

    # Embeddings retained after both soft deletes (Req 9.3).
    resume_rows = (await db_session.execute(select(ResumeEmbedding))).scalars().all()
    match_rows = (await db_session.execute(select(MatchEmbedding))).scalars().all()
    assert len(resume_rows) == 1
    assert resume_rows[0].resume_id == resume.id
    assert len(match_rows) == 1
    assert str(match_rows[0].match_result_id) == match_id
