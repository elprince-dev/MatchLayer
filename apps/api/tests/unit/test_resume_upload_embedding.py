"""Unit tests for best-effort embedding at resume upload (task 10.4).

Covers ``Resume_Service.create_resume`` step 9 (task 10.3, phase-2
Requirements 2.5, 2.11, 9.7):

* **Happy path**: extraction succeeded with non-empty text and the semantic
  pipeline is loaded → the resume Embedding is generated under the timeout
  and upserted once, stamped with the pipeline's model name + revision.
* **Upload response unchanged on any embedding failure**: a generation
  failure (timeout or runtime error) or a persistence failure leaves the
  returned Resume row — the source of the HTTP 201 response body — exactly
  as Phase 1 produced it, with ``extraction_status`` reflecting extraction
  alone; exactly one structured event from the documented set is logged
  and it never carries the extracted text or vector content.
* **Degraded_Mode / failed extraction**: no embedding is attempted at all
  (silent no-op — startup's ``model_load_failure`` covers Degraded_Mode;
  a failed extraction has no text to embed).

Test seams: ``detect_mime``, ``extract``, ``get_semantic_pipeline``,
``embed_with_timeout``, and ``upsert_resume_embedding`` are patched on the
``resumes`` module (its imported references); storage and audit are
injected fakes; the session is the minimal fake surface the service
touches. The service under test — orchestration order, the best-effort
swallow, and the savepoint discipline — runs for real.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import structlog
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.config import Settings
from matchlayer_api.db.models import Resume, User
from matchlayer_api.ml.semantic_adapter import EmbeddingTimeoutError, SemanticPipeline
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.scorer import Semantic_Match_Scorer
from matchlayer_api.services import resumes as resumes_module
from matchlayer_api.services.extraction import ExtractionOutcome
from matchlayer_api.services.resumes import Resume_Service

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

# Recognizable Restricted-PII sentinel: extracted resume text must never
# reach a log event (phase-2 Requirement 2.9).
_EXTRACTED_TEXT = "PII-EXTRACTED-SENTINEL python developer with kubernetes"

_VECTOR_MARKER = 0.987654321
_FAKE_VECTOR = [_VECTOR_MARKER, 0.0, 0.0, 0.0]

# The documented upload-embedding event categories (design fallback table).
_EMBED_EVENT_CATEGORIES = {
    "embedding_timeout",
    "embedding_runtime_error",
    "embedding_persist_failure",
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeStorage:
    """Records puts; never touches boto3."""

    def __init__(self) -> None:
        self.puts: list[str] = []

    async def put(self, *, key: str, data: bytes, content_type: str) -> None:
        self.puts.append(key)


class _RecordingAudit:
    """Records emitted audit events (event_type, payload)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        user_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> None:
        self.events.append((event_type, payload))


class _FakeSession:
    """The minimal AsyncSession surface ``create_resume`` touches."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush = AsyncMock()
        self.scalar = AsyncMock(return_value=0)  # today's upload count

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def begin_nested(self) -> Any:
        @asynccontextmanager
        async def _savepoint() -> AsyncIterator[None]:
            yield

        return _savepoint()


def _make_pipeline() -> SemanticPipeline:
    return SemanticPipeline(
        embedding_service=Embedding_Service(Stub_Text_Encoder(max_tokens=64)),
        scorer=cast(Semantic_Match_Scorer, object()),
        model_name="fake-model",
        model_revision="fake-rev",
    )


def _succeeded_extraction() -> ExtractionOutcome:
    return ExtractionOutcome(
        status="succeeded",
        text=_EXTRACTED_TEXT,
        char_count=len(_EXTRACTED_TEXT),
        failure_category=None,
    )


def _failed_extraction() -> ExtractionOutcome:
    return ExtractionOutcome(
        status="failed",
        text=None,
        char_count=None,
        failure_category="malformed_file",
    )


def _upload() -> UploadFile:
    return UploadFile(file=io.BytesIO(b"%PDF-1.4 fake resume bytes"), filename="cv.pdf")


@pytest.fixture
def service() -> Resume_Service:
    return Resume_Service(
        storage=cast(Any, _FakeStorage()),
        audit=cast(Any, _RecordingAudit()),
        settings=Settings(**_BASE_SETTINGS_KWARGS),
    )


@pytest.fixture
def session() -> AsyncSession:
    return cast(AsyncSession, _FakeSession())


@pytest.fixture
def user() -> User:
    return User(id=uuid.uuid4())


@pytest.fixture
def upsert_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch the seams shared by every test; return the upsert recorder.

    ``detect_mime`` reports PDF (magic-byte validation is unit-tested in
    its own suite) and ``extract`` succeeds by default — individual tests
    override the pieces under test.
    """
    monkeypatch.setattr(resumes_module, "detect_mime", lambda data: "pdf")
    monkeypatch.setattr(resumes_module, "extract", AsyncMock(return_value=_succeeded_extraction()))
    monkeypatch.setattr(resumes_module, "get_semantic_pipeline", lambda: _make_pipeline())
    monkeypatch.setattr(resumes_module, "embed_with_timeout", AsyncMock(return_value=_FAKE_VECTOR))
    upsert = AsyncMock()
    monkeypatch.setattr(resumes_module, "upsert_resume_embedding", upsert)
    return upsert


def _assert_phase1_upload_shape(resume: Resume) -> None:
    """The response-relevant fields the router serialises to the 201 body.

    Identical across the happy path and every embedding-failure path —
    the upload response never changes shape or content because of
    embedding behavior (phase-2 Requirements 2.11, 9.7).
    """
    assert resume.extraction_status == "succeeded"
    assert resume.extracted_text == _EXTRACTED_TEXT
    assert resume.extraction_char_count == len(_EXTRACTED_TEXT)
    assert resume.content_type == "application/pdf"
    assert resume.original_filename == "cv.pdf"
    assert resume.deleted_at is None


def _embed_events(captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in captured if event.get("event") in _EMBED_EVENT_CATEGORIES]


def _assert_no_pii(captured: list[dict[str, Any]]) -> None:
    dump = repr(captured)
    assert "PII-EXTRACTED-SENTINEL" not in dump
    assert str(_VECTOR_MARKER) not in dump


# ---------------------------------------------------------------------------
# Happy path: embedding generated and persisted (Requirement 2.5)
# ---------------------------------------------------------------------------


async def test_upload_persists_embedding_on_happy_path(
    service: Resume_Service,
    session: AsyncSession,
    user: User,
    upsert_mock: AsyncMock,
) -> None:
    """Successful extraction + loaded pipeline → one stamped upsert."""
    with structlog.testing.capture_logs() as captured:
        resume = await service.create_resume(session, user=user, upload=_upload())

    _assert_phase1_upload_shape(resume)
    upsert_mock.assert_awaited_once()
    kwargs = upsert_mock.await_args.kwargs
    assert kwargs["resume_id"] == resume.id
    assert kwargs["user_id"] == user.id
    assert kwargs["vector"] == _FAKE_VECTOR
    assert kwargs["model_name"] == "fake-model"
    assert kwargs["model_revision"] == "fake-rev"
    assert _embed_events(captured) == []
    _assert_no_pii(captured)


# ---------------------------------------------------------------------------
# Generation failures leave the upload response unchanged (Req 2.11, 9.7)
# ---------------------------------------------------------------------------


async def test_upload_unchanged_when_embedding_times_out(
    monkeypatch: pytest.MonkeyPatch,
    service: Resume_Service,
    session: AsyncSession,
    user: User,
    upsert_mock: AsyncMock,
) -> None:
    """Embed timeout → same 201 shape, no upsert, one ``embedding_timeout``."""
    monkeypatch.setattr(
        resumes_module,
        "embed_with_timeout",
        AsyncMock(side_effect=EmbeddingTimeoutError("exceeded the bound")),
    )

    with structlog.testing.capture_logs() as captured:
        resume = await service.create_resume(session, user=user, upload=_upload())

    _assert_phase1_upload_shape(resume)
    upsert_mock.assert_not_awaited()
    events = _embed_events(captured)
    assert len(events) == 1
    assert events[0]["event"] == "embedding_timeout"
    assert events[0]["resume_id"] == str(resume.id)
    _assert_no_pii(captured)


async def test_upload_unchanged_when_embedding_generation_errors(
    monkeypatch: pytest.MonkeyPatch,
    service: Resume_Service,
    session: AsyncSession,
    user: User,
    upsert_mock: AsyncMock,
) -> None:
    """Encode error → same 201 shape, no upsert, one ``embedding_runtime_error``."""
    monkeypatch.setattr(
        resumes_module,
        "embed_with_timeout",
        AsyncMock(side_effect=RuntimeError(f"encoder exploded over {_EXTRACTED_TEXT}")),
    )

    with structlog.testing.capture_logs() as captured:
        resume = await service.create_resume(session, user=user, upload=_upload())

    _assert_phase1_upload_shape(resume)
    upsert_mock.assert_not_awaited()
    events = _embed_events(captured)
    assert len(events) == 1
    assert events[0]["event"] == "embedding_runtime_error"
    # Exception CLASS only — the message could echo Restricted input.
    assert events[0]["error_type"] == "RuntimeError"
    _assert_no_pii(captured)


# ---------------------------------------------------------------------------
# Persistence failure leaves the upload response unchanged (Req 2.11, 9.7)
# ---------------------------------------------------------------------------


async def test_upload_unchanged_when_embedding_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    service: Resume_Service,
    session: AsyncSession,
    user: User,
    upsert_mock: AsyncMock,
) -> None:
    """Upsert rejection → same 201 shape, one ``embedding_persist_failure``."""
    monkeypatch.setattr(
        resumes_module,
        "upsert_resume_embedding",
        AsyncMock(side_effect=RuntimeError("dimension rejected by DDL")),
    )

    with structlog.testing.capture_logs() as captured:
        resume = await service.create_resume(session, user=user, upload=_upload())

    _assert_phase1_upload_shape(resume)
    events = _embed_events(captured)
    assert len(events) == 1
    assert events[0]["event"] == "embedding_persist_failure"
    assert events[0]["error_type"] == "RuntimeError"
    _assert_no_pii(captured)


# ---------------------------------------------------------------------------
# No-attempt paths: Degraded_Mode and failed extraction
# ---------------------------------------------------------------------------


async def test_degraded_mode_skips_embedding_silently(
    monkeypatch: pytest.MonkeyPatch,
    service: Resume_Service,
    session: AsyncSession,
    user: User,
    upsert_mock: AsyncMock,
) -> None:
    """No loaded pipeline → no embed call, no upsert, zero embed events.

    The resume is embedded lazily at match time instead (Requirement 2.7);
    per-request noise here would violate the one-event-per-occurrence
    discipline (startup already logged ``model_load_failure`` once).
    """
    monkeypatch.setattr(resumes_module, "get_semantic_pipeline", lambda: None)
    embed = AsyncMock(return_value=_FAKE_VECTOR)
    monkeypatch.setattr(resumes_module, "embed_with_timeout", embed)

    with structlog.testing.capture_logs() as captured:
        resume = await service.create_resume(session, user=user, upload=_upload())

    _assert_phase1_upload_shape(resume)
    embed.assert_not_awaited()
    upsert_mock.assert_not_awaited()
    assert _embed_events(captured) == []
    _assert_no_pii(captured)


async def test_failed_extraction_never_attempts_embedding(
    monkeypatch: pytest.MonkeyPatch,
    service: Resume_Service,
    session: AsyncSession,
    user: User,
    upsert_mock: AsyncMock,
) -> None:
    """A failed extraction has no text to embed: fail-soft row, no embed work."""
    monkeypatch.setattr(resumes_module, "extract", AsyncMock(return_value=_failed_extraction()))
    embed = AsyncMock(return_value=_FAKE_VECTOR)
    monkeypatch.setattr(resumes_module, "embed_with_timeout", embed)

    with structlog.testing.capture_logs() as captured:
        resume = await service.create_resume(session, user=user, upload=_upload())

    # Phase 1 fail-soft shape (Requirement 3.5): the row persists as failed.
    assert resume.extraction_status == "failed"
    assert resume.extracted_text is None
    assert resume.extraction_char_count is None
    embed.assert_not_awaited()
    upsert_mock.assert_not_awaited()
    assert _embed_events(captured) == []
    _assert_no_pii(captured)
