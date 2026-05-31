"""Match_Scorer: the deterministic, non-LLM scoring core (phase-1-matching, task 4.10).

The :class:`Match_Scorer` is the framework-free heart of Phase 1 scoring. Given a
resume text and a job-description text it returns an explainable 0..100 integer
score (Requirement 5.1, 5.3), a :class:`ScoreBreakdown` that makes the score
re-derivable without re-running the algorithm (Requirement 5.5), the matched /
missing keyword partition, the rule-based suggestions, and the ``Scorer_Version``
the result was produced under (Requirement 5.7).

Algorithm (design "Match_Scorer"):

1. **Normalize** both texts (case-fold + collapse whitespace). Alias
   canonicalization for the keyword-coverage half is applied inside the
   :class:`~matchlayer_api.scoring.keyword_analyzer.Keyword_Analyzer`; the
   similarity half operates on the case-folded, whitespace-normalized text and
   relies on scikit-learn's own tokenization (see ``_similarity``).
2. **Similarity component** — fit a :class:`TfidfVectorizer` on the two
   normalized documents and take the cosine similarity of the two vectors
   (``sim`` in ``[0, 1]``) (Requirement 5.1).
3. **Keyword-coverage component** — delegate to the ``Keyword_Analyzer`` and
   compute ``coverage = |matched| / |analyzed|`` (defined as ``0`` when the
   analyzed set is empty) (Requirement 5.2).
4. **Combine** — ``score = round(100 * (w_similarity * sim + w_keyword *
   coverage))`` clamped to ``[0, 100]`` (Requirement 5.3).
5. An **empty** resume or job description (after normalization) yields
   ``score == 0`` with both component values ``0`` and never raises
   (Requirement 5.6).
6. Every result is stamped with the lexicon-derived ``Scorer_Version``
   (Requirement 5.7).

Determinism (Requirement 5.4, 5.7): the vectorizer is deterministic, the
analyzer and generator order their outputs by descending weight with stable
tiebreaks, and no randomness or external state participates — identical inputs
under an identical ``Scorer_Version`` produce an identical :class:`ScoreResult`.

Import boundary (Requirement 5.8, 10.1): this module imports **only**
scikit-learn (``TfidfVectorizer``, ``cosine_similarity``), the Python standard
library, and its sibling ``scoring`` modules. It never imports FastAPI,
SQLAlchemy, ``matchlayer_api.config``, or any storage/web module. The configured
weights and caps are **injected** through the constructor by the ``ml/`` adapter
(a later task), keeping the scoring core free of settings access.

Design reference: "Match_Scorer". Requirements covered: 5.1 through 5.8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sklearn.feature_extraction.text import (  # type: ignore[import-untyped]
    TfidfVectorizer,  # scikit-learn ships no py.typed / stubs
)
from sklearn.metrics.pairwise import (  # type: ignore[import-untyped]
    cosine_similarity,  # scikit-learn ships no py.typed / stubs
)

from matchlayer_api.scoring.keyword_analyzer import (
    Keyword,
    Keyword_Analyzer,
    KeywordAnalysis,
)
from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.suggestions import Suggestion, Suggestion_Generator

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """The explainable breakdown behind a score (Requirement 5.5).

    Field names mirror the ``ScoreBreakdownOut`` Pydantic schema and the
    ``match_results.score_breakdown`` JSONB shape so the Scoring_Service can map
    this value straight onto the persisted column and the API response without
    renaming. ``final_score`` equals the enclosing :attr:`ScoreResult.score`;
    the similarity and keyword-coverage components are the raw ``[0, 1]`` values
    before weighting, so a reader can recompute
    ``round(100 * (weight_similarity * similarity_component +
    weight_keyword * keyword_coverage_component))`` and arrive at
    ``final_score``.
    """

    similarity_component: float
    keyword_coverage_component: float
    weight_similarity: float
    weight_keyword: float
    final_score: int


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """The complete, immutable output of one :meth:`Match_Scorer.score` call.

    Frozen so a produced score is a value that cannot be mutated after the fact,
    consistent with the sibling ``Keyword``/``Suggestion`` dataclasses. The
    keyword lists are ordered by descending weight and the suggestions by
    descending missing-keyword weight (Requirements 6.6, 7.2). ``scorer_version``
    is stamped from the :class:`Skill_Lexicon` (Requirement 5.7) so the
    Scoring_Service can persist a reproducible, auditable identifier.
    """

    score: int  # 0..100
    breakdown: ScoreBreakdown
    matched_keywords: list[Keyword]  # ordered by descending weight
    missing_keywords: list[Keyword]  # ordered by descending weight
    suggestions: list[Suggestion]  # ordered by descending missing-keyword weight
    scorer_version: str


def _normalize(text: str) -> str:
    """Case-fold ``text`` and collapse all whitespace runs to single spaces.

    This is the normalization gate used for the empty-input check (Requirement
    5.6) and to build the documents handed to the TF-IDF vectorizer. It mirrors
    the ``Keyword_Analyzer``'s whitespace/case handling; lexicon alias
    substitution (which only ever rewrites surface forms and can never empty a
    string) is applied by the analyzer for the coverage half.
    """
    return " ".join(text.casefold().split())


# ---------------------------------------------------------------------------
# Match_Scorer
# ---------------------------------------------------------------------------


class Match_Scorer:  # noqa: N801 -- design uses the underscored component name.
    """Deterministic TF-IDF-plus-keyword scorer producing a 0..100 score.

    Construct once with the loaded :class:`Skill_Lexicon`, the two scoring
    weights (which sum to ``1.0`` — enforced at the settings layer), and the
    keyword/suggestion caps, then reuse across requests; instances are immutable
    and hold only the lexicon-bound analyzer, the generator, the weights, and
    the stamped ``Scorer_Version``. All knobs are injected by the ``ml/`` adapter
    so the scoring core never reads ``matchlayer_api.config`` (Requirement 5.8,
    10.1).
    """

    def __init__(
        self,
        lexicon: Skill_Lexicon,
        *,
        w_similarity: float,
        w_keyword: float,
        max_keywords: int,
        max_suggestions: int,
    ) -> None:
        self._w_similarity: Final[float] = w_similarity
        self._w_keyword: Final[float] = w_keyword
        # The scoring core composes its own analyzer and generator from the
        # injected caps, so the adapter only needs to hand over the lexicon and
        # the configured knobs (Requirement 5.8 import boundary).
        self._analyzer: Final[Keyword_Analyzer] = Keyword_Analyzer(
            lexicon, max_keywords=max_keywords
        )
        self._generator: Final[Suggestion_Generator] = Suggestion_Generator(
            lexicon, max_suggestions=max_suggestions
        )
        self._scorer_version: Final[str] = lexicon.scorer_version

    @property
    def scorer_version(self) -> str:
        """The ``Scorer_Version`` every produced :class:`ScoreResult` is stamped with."""
        return self._scorer_version

    def score(self, resume_text: str, job_description: str) -> ScoreResult:
        """Score ``resume_text`` against ``job_description``.

        Returns a :class:`ScoreResult` whose ``score`` is an integer in
        ``[0, 100]``. When either text is empty or whitespace-only after
        normalization the score is ``0`` with both breakdown components ``0``,
        and the call never raises (Requirement 5.6). Deterministic for identical
        inputs under an identical ``Scorer_Version`` (Requirement 5.4).
        """
        resume_norm = _normalize(resume_text)
        jd_norm = _normalize(job_description)

        # Keyword-coverage half. The analyzer is empty-input safe (an empty JD
        # yields an empty analysis), so coverage is naturally 0 in the empty
        # cases below; computing it here keeps the matched/missing partition and
        # the suggestions well-formed regardless of emptiness.
        analysis = self._analyzer.analyze(resume_text, job_description)
        coverage = _coverage(analysis)

        # Similarity half. Forced to 0 when either document is empty so the
        # empty-input contract (Requirement 5.6) does not depend on TF-IDF
        # internals; otherwise the cosine similarity of the two TF-IDF vectors.
        similarity = _similarity(resume_norm, jd_norm) if resume_norm and jd_norm else 0.0

        final_score = self._combine(similarity, coverage)

        breakdown = ScoreBreakdown(
            similarity_component=similarity,
            keyword_coverage_component=coverage,
            weight_similarity=self._w_similarity,
            weight_keyword=self._w_keyword,
            final_score=final_score,
        )

        return ScoreResult(
            score=final_score,
            breakdown=breakdown,
            matched_keywords=list(analysis.matched),
            missing_keywords=list(analysis.missing),
            suggestions=self._generator.generate(analysis.missing),
            scorer_version=self._scorer_version,
        )

    def _combine(self, similarity: float, coverage: float) -> int:
        """Weight, scale to 0..100, round, and clamp (Requirement 5.3).

        ``round`` matches the design formula; the clamp guards against
        floating-point drift pushing a perfect match a hair above 100 (or a
        degenerate weight configuration below 0), so the result is always a
        valid integer percentage.
        """
        weighted = self._w_similarity * similarity + self._w_keyword * coverage
        scaled = round(100 * weighted)
        return max(0, min(100, scaled))


def _coverage(analysis: KeywordAnalysis) -> float:
    """``|matched| / |analyzed|``, defined as ``0`` for an empty analyzed set.

    The analyzed set is the disjoint union of ``matched`` and ``missing`` by the
    analyzer's construction, so its size is taken from ``analysis.analyzed``.
    """
    analyzed = len(analysis.analyzed)
    if analyzed == 0:
        return 0.0
    return len(analysis.matched) / analyzed


def _similarity(resume_norm: str, jd_norm: str) -> float:
    """Cosine similarity of the two documents' TF-IDF vectors, in ``[0, 1]``.

    Fits a single :class:`TfidfVectorizer` over the two normalized documents
    (Requirement 5.1). A document pair that contains no usable vocabulary (e.g.
    only single characters or punctuation that the default token pattern
    discards) makes the vectorizer raise ``ValueError`` ("empty vocabulary");
    that is treated as "no shared signal" → ``0.0`` so scoring never raises.
    TF-IDF vectors are non-negative, so the cosine lies in ``[0, 1]``; it is
    clamped defensively against floating-point drift.
    """
    vectorizer = TfidfVectorizer()
    try:
        matrix = vectorizer.fit_transform([resume_norm, jd_norm])
    except ValueError:
        return 0.0
    sim = float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
    return max(0.0, min(1.0, sim))
