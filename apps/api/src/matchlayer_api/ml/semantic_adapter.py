"""The ``ml/`` semantic adapter — Phase 2 artifact loading + availability.

This module is the ML_Adapter half that owns the Phase 2 artifacts (design §5,
phase-2-nlp-embeddings task 8.3). It is the **only** API layer that reads the
Phase 2 scoring configuration, loads the SentenceTransformer Embedding_Model
and the spaCy pipeline, and constructs the composed
:class:`~matchlayer_api.scoring.scorer.Semantic_Match_Scorer` (Requirement
12.2). Like its Phase 1 sibling :mod:`matchlayer_api.ml.scorer_adapter`, it
computes **no similarity, coverage, or score value** — every scoring
computation lives in the framework-free ``matchlayer_api.scoring`` core, which
this adapter feeds via constructor injection (Requirement 12.1).

Responsibilities
----------------

* :func:`load_semantic_pipeline` — called **once** from the FastAPI lifespan.
  Loads the SentenceTransformer from the configured local path (never the
  Hugging Face hub — Requirement 10.2, 10.6), loads the configured spaCy
  pipeline (version read from installed package metadata), wraps the model in
  the concrete :class:`Text_Encoder` implementation, composes the v2
  ``scorer_version``, and stores the resulting :class:`SemanticPipeline` in
  module state. Returns ``None`` on **any** load failure, logging exactly one
  ``model_load_failure`` structured event per occurrence — with the failure
  category and no PII (Requirements 7.1, 7.4). Startup then proceeds in
  Degraded_Mode.
* **Startup dimension check** — when the model *does* load, a mismatch between
  the loaded encoder's output dimension and the configured
  ``MATCHLAYER_EMBEDDING_DIMENSION`` (which must equal the pgvector DDL
  literal) raises :class:`EmbeddingDimensionMismatchError` and **fails startup
  fast** (Requirements 1.6, 1.7). This is deliberately distinct from the
  return-``None`` degraded path: a dimension mismatch means every generated
  vector would be rejected by the Vector_Store, which is a deployment bug to
  surface immediately, not a runtime condition to degrade around.
* :func:`semantic_available` — whether the pipeline loaded; backs the
  ``/healthz`` ``semantic_scoring`` field (Requirement 7.5).
* :func:`embed_with_timeout` — embeds one text on a worker thread under
  ``asyncio.wait_for`` (design D7, Requirement 2.8), raising
  :class:`EmbeddingTimeoutError` on timeout and propagating encode errors
  unchanged for the Scoring_Service's per-request fallback ladder.

PII discipline (Requirement 2.9, ``security.md``): nothing in this module logs
input text, embedding values, or any user data. The only values that reach a
log line are artifact identity (model name/revision, spaCy pipeline name, the
configured model path — operator configuration, not user data) and exception
metadata from artifact loading, which happens at startup before any user input
exists.

Import-boundary direction (Requirement 12.1, 12.2): this adapter imports from
``matchlayer_api.scoring`` and ``matchlayer_api.config`` — never the reverse.

Design reference: §5 "ML_Adapter extensions". Requirements covered: 7.1, 7.4,
2.8, 1.7, 10.2, 12.2.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import metadata
from typing import Final

import spacy
import structlog

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import load_lexicon_v2
from matchlayer_api.scoring.scorer import Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor
from matchlayer_api.scoring.versioning import semantic_scorer_version

__all__ = [
    "EmbeddingDimensionMismatchError",
    "EmbeddingTimeoutError",
    "SemanticPipeline",
    "SemanticPipelineUnavailableError",
    "embed_with_timeout",
    "get_semantic_pipeline",
    "load_semantic_pipeline",
    "semantic_available",
]

_log = structlog.get_logger(__name__)

# The structured event name for every artifact-load failure (Requirement 7.4,
# design failure table). Exactly one event is emitted per failed load attempt;
# the ``category`` field names which artifact or composition step failed.
_LOAD_FAILURE_EVENT: Final[str] = "model_load_failure"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EmbeddingTimeoutError(TimeoutError):
    """Embedding generation exceeded its configured wall-clock bound.

    Raised by :func:`embed_with_timeout` when ``asyncio.wait_for`` expires
    (Requirement 2.8). The Scoring_Service catches it and completes the
    affected request via the per-request Phase 1 fallback (Requirement 7.3),
    logging the ``embedding_timeout`` event — never a 5xx.
    """


class EmbeddingDimensionMismatchError(RuntimeError):
    """The loaded encoder's dimension differs from the configured dimension.

    Raised by :func:`load_semantic_pipeline` when the model loads but its
    output dimension is not ``MATCHLAYER_EMBEDDING_DIMENSION`` (the pgvector
    DDL literal). Deliberately **not** swallowed into the return-``None``
    Degraded_Mode path: this is a deployment misconfiguration that must fail
    startup fast (Requirements 1.6, 1.7) — propagating out of the lifespan
    makes uvicorn exit non-zero before a port is bound.
    """


class SemanticPipelineUnavailableError(RuntimeError):
    """:func:`embed_with_timeout` was called with no loaded semantic pipeline.

    A programming error, not a runtime condition: callers must gate Phase 2
    work on :func:`semantic_available` (Degraded_Mode never reaches the
    embedding path).
    """


# ---------------------------------------------------------------------------
# Concrete Text_Encoder over the loaded SentenceTransformer
# ---------------------------------------------------------------------------


class _Sentence_Transformer_Encoder:  # noqa: N801 -- matches the design's naming style.
    """The production :class:`~matchlayer_api.scoring.embedding.Text_Encoder`.

    Wraps the loaded ``SentenceTransformer`` and its tokenizer so the
    framework-free :class:`Embedding_Service` can chunk and aggregate without
    knowing anything about sentence-transformers (design D4, D6). Token
    operations use the model's own tokenizer, so chunk boundaries are exactly
    the boundaries the model would tokenize at; ``encode`` requests
    L2-normalized vectors per the protocol contract.
    """

    def __init__(self, model: object) -> None:
        # ``model`` is a ``sentence_transformers.SentenceTransformer``. It is
        # typed ``object`` and accessed dynamically because the transformers /
        # torch stack is excluded from strict typing (see the mypy override in
        # pyproject.toml) — the values extracted here are immediately
        # re-validated into plain ints below.
        self._model: Final = model
        tokenizer = model.tokenizer  # type: ignore[attr-defined]
        self._tokenizer: Final = tokenizer

        # sentence-transformers renamed ``get_sentence_embedding_dimension``
        # to ``get_embedding_dimension``; the old name still resolves but
        # emits a FutureWarning and is slated for removal. Prefer the new
        # name and fall back to the old one so the adapter works across the
        # whole pinned range (>=5.0,<6.0) — early 5.x only has the old name —
        # and keeps working when the ceiling is lifted. Both are accessed via
        # getattr because the torch/transformers stack is untyped here.
        get_dimension = getattr(model, "get_embedding_dimension", None) or getattr(
            model, "get_sentence_embedding_dimension", None
        )
        if get_dimension is None:
            msg = "the loaded model exposes no embedding-dimension accessor"
            raise ValueError(msg)
        dimension = get_dimension()
        if not isinstance(dimension, int) or dimension <= 0:
            msg = f"the loaded model reports no usable embedding dimension: {dimension!r}"
            raise ValueError(msg)
        self._dimension: Final[int] = dimension

        # ``max_seq_length`` counts the special tokens ([CLS]/[SEP]) the model
        # prepends/appends per sequence. Reserve room for them so a chunk of
        # exactly ``max_tokens`` content tokens re-tokenizes within the model's
        # limit instead of being silently truncated at encode time.
        max_seq_length = int(model.max_seq_length)  # type: ignore[attr-defined]
        specials = int(tokenizer.num_special_tokens_to_add(pair=False))
        self._max_tokens: Final[int] = max(1, max_seq_length - specials)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def count_tokens(self, text: str) -> int:
        ids = self._tokenizer.encode(text, add_special_tokens=False)
        return len(ids)

    def split_tokens(self, text: str, max_tokens: int) -> list[str]:
        # Non-overlapping windows over the model tokenizer's token ids,
        # decoded back to text: consecutive, at most ``max_tokens`` tokens
        # each, jointly covering the full document (protocol contract).
        ids = self._tokenizer.encode(text, add_special_tokens=False)
        step = max(1, max_tokens)
        return [
            str(self._tokenizer.decode(ids[start : start + step], skip_special_tokens=True))
            for start in range(0, len(ids), step)
        ]

    def encode(self, texts: list[str]) -> list[list[float]]:
        embeddings = self._model.encode(  # type: ignore[attr-defined]
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(component) for component in row] for row in embeddings]


# ---------------------------------------------------------------------------
# SemanticPipeline + module state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticPipeline:
    """Everything the Phase 2 scoring path needs, loaded once at startup.

    ``model_name`` / ``model_revision`` are the configured artifact identity
    that produced every embedding this pipeline generates — persisted alongside
    each vector so the reuse-versus-regenerate decision (Requirement 2.7,
    2.10) is decidable from stored data.
    """

    embedding_service: Embedding_Service
    scorer: Semantic_Match_Scorer
    model_name: str
    model_revision: str


# The process-wide loaded pipeline. ``None`` means Degraded_Mode: either
# ``load_semantic_pipeline`` has not run yet or an artifact failed to load
# (Requirement 7.1). Set exactly once by the lifespan's load call; the only
# way out of Degraded_Mode is a process restart (Requirement 7.7).
_pipeline: SemanticPipeline | None = None


def _log_load_failure(category: str, settings: Settings, exc: Exception) -> None:
    """Emit the single ``model_load_failure`` event for one failed load.

    Carries the failure category and artifact identity only — never input
    text, embedding values, or any user data (Requirement 7.4). The
    configured model path is operator configuration, not PII.
    """
    _log.error(
        _LOAD_FAILURE_EVENT,
        category=category,
        embedding_model_name=settings.embedding_model_name,
        embedding_model_revision=settings.embedding_model_revision,
        embedding_model_path=settings.embedding_model_path,
        spacy_pipeline=settings.spacy_pipeline,
        error_type=type(exc).__name__,
        error=str(exc),
    )


def load_semantic_pipeline() -> SemanticPipeline | None:
    """Load the Phase 2 artifacts and compose the semantic pipeline.

    Called once from the FastAPI lifespan. On success the composed
    :class:`SemanticPipeline` is stored in module state (backing
    :func:`semantic_available` and :func:`embed_with_timeout`) and returned.
    On **any** load failure the return value is ``None`` — Degraded_Mode —
    and exactly one ``model_load_failure`` structured event is logged with
    the failure category and no PII (Requirements 7.1, 7.4).

    The one deliberate exception to the degrade-on-failure rule: when the
    model loads but its output dimension differs from the configured
    ``MATCHLAYER_EMBEDDING_DIMENSION``,
    :class:`EmbeddingDimensionMismatchError` propagates and fails startup
    fast (Requirements 1.6, 1.7) — see the class docstring for why that is
    not a degraded condition.

    Computes no similarity, coverage, or score value (Requirement 12.2); it
    only loads artifacts and injects them into the Scoring_Core constructors.
    """
    # The single sanctioned module-state write: the lifespan calls this once.
    global _pipeline
    _pipeline = None
    settings = get_settings()

    # --- Embedding_Model (SentenceTransformer) ---------------------------
    try:
        # Imported lazily so a broken/missing ML stack degrades instead of
        # crashing app import; the loaded model is process-wide state.
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(
            settings.embedding_model_path,
            device="cpu",
            # Never contact the model hub at runtime (HF_HUB_OFFLINE
            # semantics — Requirements 10.2, 10.6): the artifact was baked
            # into the image at build time from the pinned name+revision.
            local_files_only=True,
        )
        encoder = _Sentence_Transformer_Encoder(model)
    except Exception as exc:  # Requirement 7.1: ANY load failure degrades.
        _log_load_failure("embedding_model_load_failure", settings, exc)
        return None

    # --- Startup dimension check (only when the model loads) -------------
    if encoder.dimension != settings.embedding_dimension:
        msg = (
            f"loaded embedding model produces dimension {encoder.dimension}, but "
            f"MATCHLAYER_EMBEDDING_DIMENSION={settings.embedding_dimension} (the "
            f"pgvector DDL literal); refusing to start — every generated vector "
            f"would be rejected by the Vector_Store (Requirements 1.6, 1.7)"
        )
        raise EmbeddingDimensionMismatchError(msg)

    # --- spaCy pipeline ---------------------------------------------------
    try:
        nlp = spacy.load(settings.spacy_pipeline)
        # The pipeline package's installed version, from package metadata —
        # stamped into the v2 Scorer_Version (design §5, Requirement 6.1).
        spacy_version = metadata.version(settings.spacy_pipeline)
    except Exception as exc:  # Requirement 4.12: spaCy load failure degrades.
        _log_load_failure("spacy_pipeline_load_failure", settings, exc)
        return None

    # --- Composition (v2 lexicon + scorer_version + Scoring_Core wiring) --
    try:
        lexicon = load_lexicon_v2()
        scorer_version = semantic_scorer_version(
            lexicon.lexicon_version,
            settings.embedding_model_name,
            settings.embedding_model_revision,
            settings.spacy_pipeline,
            spacy_version,
        )
        skill_extractor = Skill_Extractor(
            nlp,
            lexicon,
            max_keywords=settings.match_max_keywords,
        )
        scorer = Semantic_Match_Scorer(
            lexicon,
            skill_extractor,
            Semantic_Scorer(),
            w_similarity=settings.score_weight_similarity,
            w_keyword=settings.score_weight_keyword,
            max_suggestions=settings.match_max_suggestions,
            scorer_version=scorer_version,
        )
    except Exception as exc:  # Requirement 7.1: ANY load failure degrades.
        _log_load_failure("pipeline_composition_failure", settings, exc)
        return None

    _pipeline = SemanticPipeline(
        embedding_service=Embedding_Service(encoder),
        scorer=scorer,
        model_name=settings.embedding_model_name,
        model_revision=settings.embedding_model_revision,
    )
    return _pipeline


def get_semantic_pipeline() -> SemanticPipeline | None:
    """The loaded pipeline, or ``None`` in Degraded_Mode.

    Read by the scorer adapter's ``get_semantic_scorer()`` (task 8.4) and the
    Scoring_Service; never constructs anything itself.
    """
    return _pipeline


def semantic_available() -> bool:
    """Whether the Phase 2 semantic pipeline loaded (Requirement 7.5).

    Backs the ``/healthz`` ``semantic_scoring`` field: ``True`` maps to
    ``"available"``, ``False`` to ``"unavailable"`` (Degraded_Mode).
    """
    return _pipeline is not None


# ---------------------------------------------------------------------------
# Timeout-bounded embedding
# ---------------------------------------------------------------------------


async def embed_with_timeout(
    text: str,
    timeout_seconds: float,
    *,
    embedding_service: Embedding_Service | None = None,
) -> list[float]:
    """Embed ``text`` on a worker thread under a wall-clock bound (Req 2.8, D7).

    Runs the synchronous, pure :meth:`Embedding_Service.embed` via
    ``asyncio.to_thread`` wrapped in ``asyncio.wait_for``. Raises
    :class:`EmbeddingTimeoutError` when the bound expires (the worker thread
    finishes in the background and its result is discarded — CPython threads
    are not forcibly cancellable) and propagates encode errors unchanged so
    the Scoring_Service can apply the ``embedding_runtime_error`` fallback.

    ``embedding_service`` defaults to the loaded pipeline's service; tests
    inject a fake slow encoder's service here. Computes no similarity,
    coverage, or score value (Requirement 12.2). Neither the input text nor
    the vector appears in any error message or log line (Requirement 2.9).
    """
    service = embedding_service
    if service is None:
        pipeline = _pipeline
        if pipeline is None:
            msg = (
                "embed_with_timeout called while the semantic pipeline is "
                "unavailable; callers must gate on semantic_available()"
            )
            raise SemanticPipelineUnavailableError(msg)
        service = pipeline.embedding_service

    try:
        return await asyncio.wait_for(asyncio.to_thread(service.embed, text), timeout_seconds)
    except TimeoutError as exc:
        msg = f"embedding generation exceeded the {timeout_seconds}s wall-clock bound"
        raise EmbeddingTimeoutError(msg) from exc
