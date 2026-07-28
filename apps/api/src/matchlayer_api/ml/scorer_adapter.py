"""The ``ml/`` scorer adapter — the bridge from the API into ``scoring/``.

This module is the thin marshalling layer required by Requirement 10.2 and the
design section "ml/ adapter". It is the *only* place the framework world
(settings, services) reaches into the framework-free ``matchlayer_api.scoring``
package. It performs **no scoring arithmetic** of its own:

* :func:`get_scorer` constructs — once, process-wide — a single
  :class:`~matchlayer_api.scoring.scorer.Match_Scorer` from the loaded
  :class:`~matchlayer_api.scoring.lexicon.Skill_Lexicon` and the configured
  weights and caps read from :func:`~matchlayer_api.config.get_settings`.
* :func:`score` simply delegates to that cached scorer and returns its
  :class:`~matchlayer_api.scoring.scorer.ScoreResult` unchanged.
* :func:`get_semantic_scorer` (Phase 2, phase-2-nlp-embeddings task 8.4)
  returns the composed
  :class:`~matchlayer_api.scoring.scorer.Semantic_Match_Scorer` from the
  pipeline that :func:`~matchlayer_api.ml.semantic_adapter.load_semantic_pipeline`
  loaded at startup — or ``None`` in Degraded_Mode. Pure lookup: the scorer
  itself was already constructed by the semantic adapter; this function
  computes no similarity, coverage, or score value (Requirements 12.1, 12.2).

Import-boundary direction (Requirement 10.1, 10.2): this adapter imports from
``matchlayer_api.scoring`` and ``matchlayer_api.config`` — never the reverse.
The scoring package stays free of FastAPI, SQLAlchemy, and ``config``; the
``ml/`` layer is the sanctioned boundary that reads settings and injects the
configured knobs into the otherwise settings-agnostic scorer (see the
``Match_Scorer`` constructor, which takes ``w_similarity``/``w_keyword``/
``max_keywords``/``max_suggestions`` as explicit arguments for exactly this
reason).

Design reference: "ml/ adapter". Requirements covered: 10.2.
"""

from __future__ import annotations

from functools import lru_cache

from matchlayer_api.config import get_settings
from matchlayer_api.ml.semantic_adapter import get_semantic_pipeline
from matchlayer_api.scoring.lexicon import load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer, ScoreResult, Semantic_Match_Scorer

__all__ = ["get_scorer", "get_semantic_scorer", "score"]


@lru_cache(maxsize=1)
def get_scorer() -> Match_Scorer:
    """Return the process-wide :class:`Match_Scorer`.

    Cached like :func:`~matchlayer_api.config.get_settings` and
    :func:`~matchlayer_api.core.storage.get_resume_storage`: the scorer is
    immutable for the life of the process (it binds the cached
    :class:`~matchlayer_api.scoring.lexicon.Skill_Lexicon` and the configured
    weights/caps), so it is built once and reused across requests and worker
    threads rather than reconstructed per call.

    The configured weights and caps are read here, in the adapter layer, and
    injected into the scorer's constructor. This keeps the scoring core free of
    any ``matchlayer_api.config`` import (Requirement 10.1) while still honoring
    the operator-configured ``MATCHLAYER_SCORE_WEIGHT_*`` /
    ``MATCHLAYER_MATCH_MAX_*`` values (Requirements 5.3, 6.1, 7.2). The weights
    are validated to sum to ``1.0`` at settings-construction time, so no
    arithmetic or re-validation happens here.

    Tests that need a scorer bound to a different lexicon or different knobs
    construct :class:`Match_Scorer` directly rather than mutating this cache.
    """
    settings = get_settings()
    return Match_Scorer(
        load_lexicon(),
        w_similarity=settings.score_weight_similarity,
        w_keyword=settings.score_weight_keyword,
        max_keywords=settings.match_max_keywords,
        max_suggestions=settings.match_max_suggestions,
    )


def get_semantic_scorer() -> Semantic_Match_Scorer | None:
    """Return the composed Phase 2 scorer, or ``None`` in Degraded_Mode.

    The returned :class:`Semantic_Match_Scorer` is the exact instance the
    semantic adapter composed inside
    :func:`~matchlayer_api.ml.semantic_adapter.load_semantic_pipeline` at
    startup — this function is a pure lookup over that module state via
    :func:`~matchlayer_api.ml.semantic_adapter.get_semantic_pipeline`. It
    constructs nothing, reads no settings, and computes no similarity,
    coverage, or score value (Requirement 12.2); every scoring computation
    stays in the framework-free ``matchlayer_api.scoring`` core (Requirement
    12.1).

    The ``None`` contract mirrors ``get_semantic_pipeline()``: ``None`` means
    Degraded_Mode — either the lifespan's ``load_semantic_pipeline()`` has not
    run yet or an artifact failed to load (Requirement 7.1). Callers (the
    Scoring_Service) must fall back to the Phase 1 :func:`get_scorer` path in
    that case; the only way out of Degraded_Mode is a process restart
    (Requirement 7.7).
    """
    pipeline = get_semantic_pipeline()
    if pipeline is None:
        return None
    return pipeline.scorer


def score(resume_text: str, job_description: str) -> ScoreResult:
    """Score ``resume_text`` against ``job_description`` via the cached scorer.

    Pure marshalling: the two plain strings go in, the cached
    :class:`Match_Scorer` does all the work, and its
    :class:`~matchlayer_api.scoring.scorer.ScoreResult` comes back out
    unchanged. No scoring arithmetic, normalization, or result reshaping happens
    in this layer — that all lives in the framework-free scoring core
    (Requirement 10.2). The Scoring_Service calls this function with the
    resume's extracted text and the request's job-description text.
    """
    return get_scorer().score(resume_text, job_description)
