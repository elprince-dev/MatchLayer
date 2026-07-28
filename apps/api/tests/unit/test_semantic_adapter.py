"""Unit tests for the ``ml/`` semantic adapter (task 8.5).

Deterministic tests over the three adapter behaviors the task names,
complementing the task 8.3 implementation:

* **Broken model path degrades** (Requirements 7.1, 7.4):
  ``load_semantic_pipeline()`` returns ``None`` when the configured
  ``embedding_model_path`` does not hold a loadable model, logging exactly
  one ``model_load_failure`` structured event whose payload carries
  artifact identity only — no PII fields exist at startup, and the
  key-set assertion pins that nothing beyond the documented operator
  configuration ever lands on the event.
* **Dimension mismatch fails startup fast** (Requirements 1.6, 1.7): when
  the model *loads* but reports an output dimension different from the
  configured ``MATCHLAYER_EMBEDDING_DIMENSION`` (the pgvector DDL
  literal), :class:`EmbeddingDimensionMismatchError` propagates instead of
  degrading — a deployment bug must surface before a port is bound.
* **Timeout wrapper raises on a slow encoder** (Requirement 2.8):
  :func:`embed_with_timeout` raises :class:`EmbeddingTimeoutError` when a
  fake slow encoder exceeds the wall-clock bound, and the error message
  carries neither the input text nor any vector content (Requirement 2.9).

The real SentenceTransformer artifact is never loaded here (per the
design's test strategy the real model is exercised only by the Eval_Runner
and integration tests): the broken-path case points at an empty temp
directory, and the mismatch case injects a fake ``sentence_transformers``
module via ``sys.modules`` so the adapter's lazy in-function import
resolves to a controllable stand-in.

Module-state discipline: ``load_semantic_pipeline`` writes the process-wide
``_pipeline``; the autouse fixture snapshots and restores it so these tests
can never leak Degraded_Mode (or a fake pipeline) into sibling tests.
"""

from __future__ import annotations

import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog

from matchlayer_api.config import Settings
from matchlayer_api.ml import semantic_adapter
from matchlayer_api.ml.semantic_adapter import (
    EmbeddingDimensionMismatchError,
    EmbeddingTimeoutError,
    embed_with_timeout,
    get_semantic_pipeline,
    load_semantic_pipeline,
    semantic_available,
)
from matchlayer_api.scoring.embedding import Embedding_Service

# 33 bytes UTF-8 — clears the 32-byte floor in
# ``Settings._jwt_secret_min_length``. Same synthetic constant the sibling
# unit suites use so the value is recognizably a test fixture.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

# Every field ``Settings`` requires, with placeholder values that pass
# Pydantic validation without touching the repo's ``.env``. The Phase 2
# fields keep their defaults; each test overrides only the knob under test.
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


@pytest.fixture(autouse=True)
def _restore_pipeline_state() -> Iterator[None]:
    """Snapshot and restore the adapter's process-wide ``_pipeline``.

    ``load_semantic_pipeline`` intentionally mutates module state (the
    lifespan calls it once per process); tests that invoke it must not
    leave Degraded_Mode behind for unrelated suites.
    """
    saved = semantic_adapter._pipeline
    yield
    semantic_adapter._pipeline = saved


def _settings_with(**overrides: Any) -> Settings:
    """A valid :class:`Settings` with the given Phase 2 overrides."""
    return Settings(**{**_BASE_SETTINGS_KWARGS, **overrides})


# ---------------------------------------------------------------------------
# Broken model path → Degraded_Mode with exactly one no-PII event
# (Requirements 7.1, 7.4)
# ---------------------------------------------------------------------------


def test_broken_model_path_returns_none_with_one_load_failure_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unloadable model path degrades: ``None``, one event, no PII.

    The configured path is an empty temp directory, so the
    SentenceTransformer load fails. ``load_semantic_pipeline()`` must
    return ``None`` (Degraded_Mode — Requirement 7.1), leave the adapter
    reporting unavailable, and emit exactly one ``model_load_failure``
    event (Requirement 7.4) whose key set is pinned to artifact identity
    and error metadata only.
    """
    broken_path = tmp_path / "missing-model"
    broken_path.mkdir()
    settings = _settings_with(embedding_model_path=str(broken_path))
    monkeypatch.setattr(semantic_adapter, "get_settings", lambda: settings)

    with structlog.testing.capture_logs() as captured:
        pipeline = load_semantic_pipeline()

    assert pipeline is None
    assert get_semantic_pipeline() is None
    assert semantic_available() is False

    failures = [event for event in captured if event.get("event") == "model_load_failure"]
    assert len(failures) == 1, ("exactly one model_load_failure event per occurrence", captured)

    event = failures[0]
    assert event["category"] == "embedding_model_load_failure"
    # No-PII discipline (Requirement 7.4): the payload carries operator
    # configuration (artifact identity) and error metadata only. Pinning
    # the full key set means no future field can smuggle anything else in
    # without this test noticing. ``log_level`` is capture_logs bookkeeping.
    assert set(event) == {
        "event",
        "log_level",
        "category",
        "embedding_model_name",
        "embedding_model_revision",
        "embedding_model_path",
        "spacy_pipeline",
        "error_type",
        "error",
    }
    assert event["embedding_model_name"] == settings.embedding_model_name
    assert event["embedding_model_revision"] == settings.embedding_model_revision
    assert event["embedding_model_path"] == str(broken_path)


# ---------------------------------------------------------------------------
# Dimension mismatch fails startup fast (Requirements 1.6, 1.7)
# ---------------------------------------------------------------------------


class _FakeTokenizer:
    """The minimal tokenizer surface ``_Sentence_Transformer_Encoder`` reads."""

    def num_special_tokens_to_add(self, *, pair: bool) -> int:
        return 2

    def encode(self, text: str, add_special_tokens: bool) -> list[int]:
        return list(range(len(text.split())))

    def decode(self, ids: list[int], skip_special_tokens: bool) -> str:
        return " ".join(str(i) for i in ids)


class _FakeSentenceTransformerModel:
    """A loadable fake model reporting a controllable output dimension."""

    def __init__(self, dimension: int) -> None:
        self._dimension = dimension
        self.tokenizer = _FakeTokenizer()
        self.max_seq_length = 16

    def get_sentence_embedding_dimension(self) -> int:
        return self._dimension


def test_dimension_mismatch_fails_startup_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """A loaded model with the wrong dimension raises, never degrades.

    A fake ``sentence_transformers`` module is injected so the adapter's
    lazy import resolves to a model that loads successfully but reports
    ``configured + 1`` as its output dimension. The adapter must raise
    :class:`EmbeddingDimensionMismatchError` (failing startup fast —
    Requirements 1.6, 1.7) rather than returning ``None``: every vector
    such a pipeline generated would be rejected by the pgvector DDL.
    """
    settings = _settings_with()
    monkeypatch.setattr(semantic_adapter, "get_settings", lambda: settings)

    wrong_dimension = settings.embedding_dimension + 1
    fake_module = types.ModuleType("sentence_transformers")

    def _fake_loader(path: str, **kwargs: Any) -> _FakeSentenceTransformerModel:
        return _FakeSentenceTransformerModel(wrong_dimension)

    fake_module.SentenceTransformer = _fake_loader  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    with pytest.raises(EmbeddingDimensionMismatchError) as excinfo:
        load_semantic_pipeline()

    # The message names both dimensions so the deployment bug is diagnosable
    # from the crash line alone.
    assert str(wrong_dimension) in str(excinfo.value)
    assert str(settings.embedding_dimension) in str(excinfo.value)
    # Startup failed — nothing was stored; the adapter is not "available".
    assert semantic_available() is False


# ---------------------------------------------------------------------------
# Timeout wrapper raises on a fake slow encoder (Requirements 2.8, 2.9)
# ---------------------------------------------------------------------------


class _SlowEncoder:
    """A ``Text_Encoder`` whose ``encode`` sleeps past any test timeout."""

    def __init__(self, sleep_seconds: float) -> None:
        self._sleep_seconds = sleep_seconds

    @property
    def dimension(self) -> int:
        return 4

    @property
    def max_tokens(self) -> int:
        return 128

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def split_tokens(self, text: str, max_tokens: int) -> list[str]:
        return [text]

    def encode(self, texts: list[str]) -> list[list[float]]:
        time.sleep(self._sleep_seconds)
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


async def test_embed_with_timeout_raises_on_slow_encoder() -> None:
    """A slow encoder trips the wall-clock bound (Requirement 2.8).

    The fake encoder sleeps well past the configured bound, so
    :func:`embed_with_timeout` must raise :class:`EmbeddingTimeoutError`
    — the signal the Scoring_Service maps onto the ``embedding_timeout``
    fallback rung — and the message must carry neither the input text nor
    vector content (Requirement 2.9).
    """
    service = Embedding_Service(_SlowEncoder(sleep_seconds=1.0))
    sentinel_text = "RESUME-PII-SENTINEL python developer"

    with pytest.raises(EmbeddingTimeoutError) as excinfo:
        await embed_with_timeout(sentinel_text, 0.05, embedding_service=service)

    assert "RESUME-PII-SENTINEL" not in str(excinfo.value)


async def test_embed_with_timeout_returns_vector_within_bound() -> None:
    """A fast encoder completes under the bound and returns its vector.

    The complement of the timeout case: the wrapper is a bound, not a
    delay — a within-budget embed returns the encoder's (L2-normalized)
    vector unchanged.
    """
    service = Embedding_Service(_SlowEncoder(sleep_seconds=0.0))

    vector = await embed_with_timeout("python developer", 5.0, embedding_service=service)

    assert vector == [1.0, 0.0, 0.0, 0.0]
