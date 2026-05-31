"""Feature: phase-1-matching — Property 3.

Property 3: Keyword-coverage equals the matched fraction and the breakdown is consistent.

**Validates: Requirements 5.2, 5.5**

This module is the universal companion to the concrete-example coverage of the
``Match_Scorer``. Where unit examples pin down specific scores for hand-picked
pairs, this file asserts that the ``ScoreBreakdown`` is internally consistent —
and that its keyword-coverage component is exactly the matched fraction — across
a wide, generated input space using Hypothesis (>=100 examples).

The :class:`Match_Scorer` is framework-free (Requirement 10.1): this test
constructs it directly from the committed :class:`Skill_Lexicon` and the
configured weights/caps, never touching FastAPI, the database, or storage. The
weights mirror the ``MATCHLAYER_SCORE_WEIGHT_*`` defaults (0.6 / 0.4) and the
caps mirror the ``MATCHLAYER_MATCH_MAX_*`` defaults (50 / 10).

Four complementary assertions encode the property:

* **Keyword-coverage equals the matched fraction (Requirement 5.2).**
  ``breakdown.keyword_coverage_component == |matched| / |analyzed|``, where the
  analyzed set is the union of the result's ``matched_keywords`` and
  ``missing_keywords`` lists. When that union is empty the coverage is exactly
  ``0``. A float tolerance absorbs the division's rounding.

* **The breakdown's final score is the result's score (Requirement 5.5).**
  ``breakdown.final_score == result.score`` — the breakdown reports the same
  number the caller acts on, not a stale or recomputed copy.

* **The breakdown re-derives the final score (Requirement 5.5).** Recomputing
  ``round(100 * (weight_similarity * similarity_component + weight_keyword *
  keyword_coverage_component)))`` clamped to ``[0, 100]`` reproduces
  ``final_score`` exactly, so a reader can verify the score from the breakdown
  alone without re-running the algorithm.

* **Components are in ``[0, 1]`` and the weights are the configured pair.** Both
  the similarity and keyword-coverage components are valid fractions, and the
  reported weights equal the pair the scorer was constructed with — so the
  breakdown is a faithful, self-describing record of the computation.

Inputs are arbitrary text drawn from a deliberately mixed vocabulary — real
``Skill_Lexicon`` surface forms (canonical terms and aliases), generic
non-stop-word filler so scikit-learn's TF-IDF yields a non-empty vocabulary,
and free random tokens — joined into resume and job-description documents. The
shared vocabulary makes the matched/missing split frequently populated on both
sides, so coverage takes values strictly between 0 and 1. Empty and
whitespace-only documents are admitted by construction and satisfy the
breakdown contract trivially (both components 0, score 0).
"""

from __future__ import annotations

import math
from string import ascii_lowercase, digits

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer

# ---------------------------------------------------------------------------
# Scorer under test
# ---------------------------------------------------------------------------

# The committed runtime lexicon — immutable and cached, safe to share.
_LEXICON: Skill_Lexicon = load_lexicon()

# Configured weights and caps mirror the production defaults
# (MATCHLAYER_SCORE_WEIGHT_SIMILARITY/KEYWORD = 0.6/0.4, which sum to 1.0, and
# MATCHLAYER_MATCH_MAX_KEYWORDS/SUGGESTIONS = 50/10). The breakdown-consistency
# property is independent of the particular weight values, but using the real
# defaults keeps the generated scores realistic.
_W_SIMILARITY = 0.6
_W_KEYWORD = 0.4
_MAX_KEYWORDS = 50
_MAX_SUGGESTIONS = 10

_SCORER = Match_Scorer(
    _LEXICON,
    w_similarity=_W_SIMILARITY,
    w_keyword=_W_KEYWORD,
    max_keywords=_MAX_KEYWORDS,
    max_suggestions=_MAX_SUGGESTIONS,
)

# Absolute tolerance for comparing the reported coverage against the
# independently recomputed matched fraction. Both are IEEE-754 doubles produced
# by the same ``len / len`` division, so they should agree to within rounding.
_COVERAGE_TOL = 1e-9

# ---------------------------------------------------------------------------
# Vocabulary for generated text
# ---------------------------------------------------------------------------

# Real surface forms the lexicon knows: every canonical term plus all aliases.
# Feeding these into generated documents makes the analyzed set frequently
# non-empty and drives both matched (resume/JD overlap) and missing terms.
_LEXICON_SURFACES: list[str] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic, non-stop-word filler. scikit-learn's TfidfVectorizer strips English
# stop words, so this pool ensures a document of filler still yields TF-IDF
# terms rather than an empty vocabulary.
_GENERIC_WORDS: list[str] = [
    "developer",
    "engineer",
    "experience",
    "senior",
    "team",
    "build",
    "design",
    "systems",
    "cloud",
    "data",
    "platform",
    "scalable",
    "production",
    "knowledge",
    "required",
    "preferred",
    "years",
    "strong",
    "pipeline",
    "services",
]

# A single generated token: a known lexicon surface form, generic filler, or a
# free random alphanumeric token (which may coincide with neither).
_token = st.one_of(
    st.sampled_from(_LEXICON_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# A document built from tokens joined by single spaces. ``min_size=0`` admits
# the empty document, exercising the empty-input branch (both components 0).
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Also throw genuinely arbitrary text (unicode, punctuation, odd whitespace) at
# the scorer so the breakdown claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)


@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
@example(resume_text="", job_description="")
@example(resume_text="python developer", job_description="python java sql")
@example(
    resume_text="python java sql docker kubernetes",
    job_description="python java sql docker kubernetes",
)
def test_breakdown_is_consistent_and_coverage_is_matched_fraction(
    resume_text: str, job_description: str
) -> None:
    """The breakdown re-derives the score and coverage is the matched fraction.

    Property 3 (Requirements 5.2, 5.5): for arbitrary resume and job-description
    text, the keyword-coverage component equals ``|matched| / |analyzed|`` (0
    when the analyzed set is empty), the breakdown's ``final_score`` equals the
    result's ``score``, recomputing the weighted/clamped formula from the
    breakdown reproduces ``final_score``, and both components lie in ``[0, 1]``
    with the reported weights equal to the configured pair.
    """
    result = _SCORER.score(resume_text, job_description)
    breakdown = result.breakdown

    # The analyzed set is the union of the matched and missing lists, which are
    # a disjoint partition by the analyzer's construction (Property 6); its
    # size is therefore the sum of the two list lengths.
    matched_count = len(result.matched_keywords)
    missing_count = len(result.missing_keywords)
    analyzed_count = matched_count + missing_count

    # (a) Keyword-coverage equals the matched fraction, with 0 for an empty
    # analyzed set (Requirement 5.2).
    expected_coverage = (matched_count / analyzed_count) if analyzed_count else 0.0
    assert math.isclose(
        breakdown.keyword_coverage_component, expected_coverage, abs_tol=_COVERAGE_TOL
    )

    # (d) Components are valid fractions and the weights are the configured pair
    # (Requirement 5.5 — the breakdown is a faithful record of the computation).
    assert 0.0 <= breakdown.similarity_component <= 1.0
    assert 0.0 <= breakdown.keyword_coverage_component <= 1.0
    assert breakdown.weight_similarity == _W_SIMILARITY
    assert breakdown.weight_keyword == _W_KEYWORD

    # (b) The breakdown reports the same score the caller acts on
    # (Requirement 5.5).
    assert breakdown.final_score == result.score

    # (c) The breakdown re-derives the final score: recompute the documented
    # formula from the breakdown's own component/weight fields and confirm it
    # reproduces ``final_score`` (Requirement 5.5).
    recomputed = round(
        100
        * (
            breakdown.weight_similarity * breakdown.similarity_component
            + breakdown.weight_keyword * breakdown.keyword_coverage_component
        )
    )
    recomputed_clamped = max(0, min(100, recomputed))
    assert recomputed_clamped == breakdown.final_score

    # The final score is always a valid integer percentage (a blunt tripwire
    # that complements Property 1's dedicated boundedness check).
    assert isinstance(breakdown.final_score, int)
    assert 0 <= breakdown.final_score <= 100
