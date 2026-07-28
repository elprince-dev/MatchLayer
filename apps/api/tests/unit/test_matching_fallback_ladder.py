"""Unit tests for the Scoring_Service fallback ladder + event discipline (task 10.2).

Covers the design "Fallback decision tree" as implemented by
``Scoring_Service._score_with_fallback`` (task 10.1):

* **Every rung completes the request** with the untouched Phase 1 engine —
  the outcome carries the Phase 1 ``scorer_version`` stamp and the
  ``"tfidf"`` similarity method, and no exception escapes (so the router
  can never turn a fallback into a 5xx) — with no Embedding persisted for
  the request (``jd_vector``/``pipeline`` both ``None``) (phase-2
  Requirements 7.3, 2.12).
* **Exactly one structured event per occurrence**, from the documented
  category set — ``embedding_timeout``, ``embedding_runtime_error``,
  ``embedding_geometry_error``, ``skill_extraction_empty``,
  ``skill_extraction_error`` — carrying ``request_id`` (via structlog
  contextvars, exactly as the request-id middleware binds it in
  production) and internal ids only: zero input text, zero vector content
  (phase-2 Requirements 7.4, 2.9).
* **Degraded_Mode is silent per-request**: no loaded pipeline means the
  Phase 1 engine with **no** per-request event — the once-at-startup
  ``model_load_failure`` already covers it (phase-2 Requirements 7.2, 7.6).
* **Per-request fallbacks never flip the process into Degraded_Mode**:
  after repeated fallbacks the real ``semantic_available()`` still reports
  available and the next request scores through the Phase 2 pipeline
  (phase-2 Requirement 7.3).

Test seams: the loaded pipeline is injected by setting the semantic
adapter's real module state (``semantic_adapter._pipeline``) to a fake
``SemanticPipeline`` — so ``get_semantic_pipeline()`` is the *real*
function and the "normal mode intact" assertions are about genuine process
state, not a patched stub. ``embed_with_timeout`` and the Vector_Store
functions are patched on the ``matching`` module (its imported
references). The Phase 1 fallback engine is the real
``scorer_adapter.score`` — its stamp is whatever the production Phase 1
path produces, computed here rather than hard-coded.

Log capture uses a manual ``structlog.configure`` with
``merge_contextvars`` + ``LogCapture`` (instead of ``capture_logs``,
which replaces the processor chain wholesale and would drop the
contextvars-bound ``request_id`` these tests must assert on).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.config import Settings
from matchlayer_api.ml import scorer_adapter, semantic_adapter
from matchlayer_api.ml.semantic_adapter import EmbeddingTimeoutError, SemanticPipeline
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.scorer import (
    EmptyAnalyzedSetError,
    ScoreBreakdown,
    ScoreResult,
    Semantic_Match_Scorer,
)
from matchlayer_api.scoring.semantic import EmbeddingGeometryError
from matchlayer_api.services import matching
from matchlayer_api.services.matching import Scoring_Service

# 33 bytes UTF-8 — clears the 32-byte floor in
# ``Settings._jwt_secret_min_length``. Same synthetic constant the sibling
# unit suites use so the value is recognizably a test fixture.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "environment": "development",
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    "jwt_secret": _TEST_SECRET,
}

# Recognizable Restricted-PII sentinels: if any log event ever carries
# either string (or the vector value below), the no-PII discipline is broken.
_RESUME_SENTINEL = "PII-RESUME-SENTINEL python developer with kubernetes"
_JD_SENTINEL = "PII-JD-SENTINEL seeking python and terraform experience"

# A distinctive component value for every fake vector, so vector content
# leaking into any event is detectable by substring.
_VECTOR_MARKER = 0.987654321
_FAKE_VECTOR = [_VECTOR_MARKER, 0.0, 0.0, 0.0]

_REQUEST_ID = "req-fallback-test-123"

# The documented per-request fallback category set (design fallback table).
_FALLBACK_CATEGORIES = {
    "embedding_timeout",
    "embedding_runtime_error",
    "embedding_geometry_error",
    "skill_extraction_empty",
    "skill_extraction_error",
}


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------


class _FakeSemanticScorer:
    """A Semantic_Match_Scorer stand-in with scripted score() behavior."""

    def __init__(self, behavior: ScoreResult | Exception) -> None:
        self._behavior = behavior
        self.calls = 0

    def score(
        self,
        resume_text: str,
        job_description: str,
        resume_embedding: list[float],
        jd_embedding: list[float],
    ) -> ScoreResult:
        self.calls += 1
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return self._behavior


class _FakeSession:
    """The minimal AsyncSession surface the fallback path touches."""

    def __init__(self) -> None:
        self.flush = AsyncMock()

    def begin_nested(self) -> Any:
        @asynccontextmanager
        async def _savepoint() -> AsyncIterator[None]:
            yield

        return _savepoint()


def _phase2_result() -> ScoreResult:
    """A v2-stamped result the fake scorer returns on the success path."""
    return ScoreResult(
        score=77,
        breakdown=ScoreBreakdown(
            similarity_component=0.8,
            keyword_coverage_component=0.65,
            weight_similarity=0.6,
            weight_keyword=0.4,
            final_score=77,
            similarity_method="semantic-embedding",
        ),
        matched_keywords=[],
        missing_keywords=[],
        suggestions=[],
        scorer_version="2.0.0+lex.v2+emb.fake-model@fake-rev+spacy.fake@0.0.0",
    )


def _make_pipeline(scorer: _FakeSemanticScorer) -> SemanticPipeline:
    """A fake loaded pipeline over the deterministic stub encoder."""
    return SemanticPipeline(
        embedding_service=Embedding_Service(Stub_Text_Encoder(max_tokens=64)),
        scorer=cast(Semantic_Match_Scorer, scorer),
        model_name="fake-model",
        model_revision="fake-rev",
    )


@pytest.fixture
def log_capture() -> Iterator[structlog.testing.LogCapture]:
    """Capture events *with* contextvars merged, unlike ``capture_logs``.

    Production binds ``request_id`` into structlog contextvars in the
    request-id middleware; the fallback events must carry it (phase-2
    Requirement 7.4). ``structlog.testing.capture_logs`` replaces the
    processor chain wholesale (dropping ``merge_contextvars``), so this
    fixture configures the chain manually and binds the test request id.
    """
    capture = structlog.testing.LogCapture()
    previous = structlog.get_config()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, capture])
    structlog.contextvars.bind_contextvars(request_id=_REQUEST_ID)
    yield capture
    structlog.contextvars.clear_contextvars()
    structlog.configure(**previous)


@pytest.fixture
def service() -> Scoring_Service:
    """A Scoring_Service over synthetic settings (no repo .env dependency)."""
    return Scoring_Service(settings=Settings(**_BASE_SETTINGS_KWARGS))


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, _FakeSession())


@pytest.fixture
def _no_stored_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here starts with no stored resume Embedding."""
    monkeypatch.setattr(matching, "get_resume_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(matching, "upsert_resume_embedding", AsyncMock())


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch, scorer: _FakeSemanticScorer
) -> SemanticPipeline:
    """Set the semantic adapter's REAL module state to a fake pipeline.

    ``matching`` reads the pipeline through the real
    ``get_semantic_pipeline()``, so the "normal mode intact" assertions
    below are about genuine process state.
    """
    pipeline = _make_pipeline(scorer)
    monkeypatch.setattr(semantic_adapter, "_pipeline", pipeline)
    return pipeline


async def _run(service: Scoring_Service, session: AsyncSession) -> matching._ScoreOutcome:
    return await service._score_with_fallback(
        session,
        user_id=uuid.uuid4(),
        resume_id=uuid.uuid4(),
        resume_text=_RESUME_SENTINEL,
        job_description=_JD_SENTINEL,
    )


def _phase1_version() -> str:
    """The stamp the real Phase 1 engine produces (never hard-coded)."""
    return scorer_adapter.score("python developer", "python engineer").scorer_version


def _assert_phase1_fallback_outcome(outcome: matching._ScoreOutcome) -> None:
    """The outcome is Phase 1-stamped with no vectors to persist (Req 7.3, 6.2)."""
    assert outcome.result.scorer_version == _phase1_version()
    assert not outcome.result.scorer_version.startswith("2.0.0")
    assert outcome.result.breakdown.similarity_method == "tfidf"
    assert outcome.jd_vector is None
    assert outcome.pipeline is None


def _assert_one_event(capture: structlog.testing.LogCapture, category: str) -> dict[str, Any]:
    """Exactly one documented-category event was emitted; return it (Req 7.4)."""
    fallback_events = [
        entry for entry in capture.entries if entry.get("event") in _FALLBACK_CATEGORIES
    ]
    assert len(fallback_events) == 1, (
        "exactly one fallback event per occurrence",
        capture.entries,
    )
    event = fallback_events[0]
    assert event["event"] == category
    assert event["request_id"] == _REQUEST_ID
    return event


def _assert_no_pii(capture: structlog.testing.LogCapture) -> None:
    """No captured event carries input text or vector content (Req 2.9)."""
    dump = repr(capture.entries)
    assert "PII-RESUME-SENTINEL" not in dump
    assert "PII-JD-SENTINEL" not in dump
    assert str(_VECTOR_MARKER) not in dump


# ---------------------------------------------------------------------------
# Degraded_Mode: Phase 1 engine, NO per-request event (Req 7.2, 7.6)
# ---------------------------------------------------------------------------


async def test_degraded_mode_scores_phase1_with_no_per_request_event(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
) -> None:
    """No loaded pipeline → Phase 1 result, no Embedding, zero events."""
    monkeypatch.setattr(semantic_adapter, "_pipeline", None)

    outcome = await _run(service, session)

    _assert_phase1_fallback_outcome(outcome)
    fallback_events = [
        entry for entry in log_capture.entries if entry.get("event") in _FALLBACK_CATEGORIES
    ]
    assert fallback_events == []  # startup's model_load_failure already covered it
    _assert_no_pii(log_capture)


# ---------------------------------------------------------------------------
# The per-request ladder rungs (Req 2.8, 3.10, 4.10, 4.12, 7.3, 7.4)
# ---------------------------------------------------------------------------


async def test_embedding_timeout_falls_back_with_one_event(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
    _no_stored_embedding: None,
) -> None:
    """An embed timeout completes via Phase 1 with one ``embedding_timeout``."""
    _install_pipeline(monkeypatch, _FakeSemanticScorer(_phase2_result()))
    monkeypatch.setattr(
        matching,
        "embed_with_timeout",
        AsyncMock(side_effect=EmbeddingTimeoutError("exceeded the bound")),
    )

    outcome = await _run(service, session)

    _assert_phase1_fallback_outcome(outcome)
    event = _assert_one_event(log_capture, "embedding_timeout")
    assert "resume_id" in event
    _assert_no_pii(log_capture)


async def test_embedding_runtime_error_falls_back_with_one_event(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
    _no_stored_embedding: None,
) -> None:
    """Any other embed-phase failure → Phase 1 + one ``embedding_runtime_error``."""
    _install_pipeline(monkeypatch, _FakeSemanticScorer(_phase2_result()))
    monkeypatch.setattr(
        matching,
        "embed_with_timeout",
        AsyncMock(side_effect=RuntimeError(f"encoder exploded over {_RESUME_SENTINEL}")),
    )

    outcome = await _run(service, session)

    _assert_phase1_fallback_outcome(outcome)
    event = _assert_one_event(log_capture, "embedding_runtime_error")
    # Exception CLASS only — the message could echo Restricted input.
    assert event["error_type"] == "RuntimeError"
    _assert_no_pii(log_capture)


async def test_embedding_geometry_error_falls_back_with_one_event(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
    _no_stored_embedding: None,
) -> None:
    """Undefined cosine geometry → Phase 1 + one ``embedding_geometry_error``."""
    _install_pipeline(monkeypatch, _FakeSemanticScorer(EmbeddingGeometryError("zero magnitude")))
    monkeypatch.setattr(matching, "embed_with_timeout", AsyncMock(return_value=_FAKE_VECTOR))

    outcome = await _run(service, session)

    _assert_phase1_fallback_outcome(outcome)
    _assert_one_event(log_capture, "embedding_geometry_error")
    _assert_no_pii(log_capture)


async def test_empty_analyzed_set_falls_back_with_one_event(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
    _no_stored_embedding: None,
) -> None:
    """Empty analyzed skill set → Phase 1 + one ``skill_extraction_empty`` (D8)."""
    _install_pipeline(
        monkeypatch, _FakeSemanticScorer(EmptyAnalyzedSetError("no skills extracted"))
    )
    monkeypatch.setattr(matching, "embed_with_timeout", AsyncMock(return_value=_FAKE_VECTOR))

    outcome = await _run(service, session)

    _assert_phase1_fallback_outcome(outcome)
    _assert_one_event(log_capture, "skill_extraction_empty")
    _assert_no_pii(log_capture)


async def test_other_scoring_failure_falls_back_with_one_event(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
    _no_stored_embedding: None,
) -> None:
    """Any other Phase 2 scoring failure → Phase 1 + one ``skill_extraction_error``."""
    _install_pipeline(
        monkeypatch,
        _FakeSemanticScorer(ValueError(f"matcher blew up on {_JD_SENTINEL}")),
    )
    monkeypatch.setattr(matching, "embed_with_timeout", AsyncMock(return_value=_FAKE_VECTOR))

    outcome = await _run(service, session)

    _assert_phase1_fallback_outcome(outcome)
    event = _assert_one_event(log_capture, "skill_extraction_error")
    assert event["error_type"] == "ValueError"
    _assert_no_pii(log_capture)


# ---------------------------------------------------------------------------
# Per-request fallbacks never flip the process into Degraded_Mode (Req 7.3)
# ---------------------------------------------------------------------------


async def test_repeated_fallbacks_leave_normal_mode_intact(
    monkeypatch: pytest.MonkeyPatch,
    log_capture: structlog.testing.LogCapture,
    service: Scoring_Service,
    session: AsyncSession,
    _no_stored_embedding: None,
) -> None:
    """Three consecutive fallbacks, then a clean request scores via Phase 2.

    The pipeline is installed as the semantic adapter's REAL module state,
    so this asserts genuine process behavior: repeated per-request
    fallbacks mutate nothing, ``semantic_available()`` keeps reporting
    available, and the next request completes through the Phase 2 scorer
    with the v2 stamp and vectors to persist.
    """
    scorer = _FakeSemanticScorer(_phase2_result())
    pipeline = _install_pipeline(monkeypatch, scorer)

    failing_embed = AsyncMock(side_effect=EmbeddingTimeoutError("exceeded the bound"))
    monkeypatch.setattr(matching, "embed_with_timeout", failing_embed)
    for _ in range(3):
        outcome = await _run(service, session)
        _assert_phase1_fallback_outcome(outcome)
        # Still normal mode after every fallback.
        assert semantic_adapter.semantic_available() is True

    # Exactly one event per occurrence: three occurrences, three events.
    timeout_events = [
        entry for entry in log_capture.entries if entry.get("event") == "embedding_timeout"
    ]
    assert len(timeout_events) == 3

    # A clean request now scores through the Phase 2 pipeline.
    monkeypatch.setattr(matching, "embed_with_timeout", AsyncMock(return_value=_FAKE_VECTOR))
    outcome = await _run(service, session)
    assert outcome.result.scorer_version.startswith("2.0.0")
    assert outcome.result.breakdown.similarity_method == "semantic-embedding"
    assert outcome.jd_vector == _FAKE_VECTOR
    assert outcome.pipeline is pipeline
    assert scorer.calls == 1  # the ladder rungs above never reached the scorer
    _assert_no_pii(log_capture)
