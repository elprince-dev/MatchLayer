"""Feature: phase-1-matching — Property 2.

Property 2: Scoring is deterministic.

    *For any* resume text and *any* job-description text, two invocations of
    ``Match_Scorer.score`` with the same inputs and the same ``Scorer_Version``
    produce identical ``score``, identical ``score_breakdown``, identical
    ``matched_keywords``, identical ``missing_keywords``, and identical
    ``suggestions``.

**Validates: Requirements 5.4, 5.7**

The :class:`Match_Scorer` is a pure, non-LLM transform: a deterministic
scikit-learn ``TfidfVectorizer`` for the similarity half, a lexicon-bound
``Keyword_Analyzer`` whose analyzed set and matched/missing partition are
ordered by a *stable* descending-weight sort, and a ``Suggestion_Generator``
built from fixed templates. No randomness, clock, or external state
participates, and every result is stamped with the lexicon-derived
``Scorer_Version`` (Requirement 5.7). Those design choices are exactly what
make the produced :class:`ScoreResult` a deterministic function of (resume
text, job-description text, ``Scorer_Version``). This module pins that
contract across a wide generated input space using Hypothesis (>=100
examples), complementing the concrete eyeball-example coverage in
``tests/unit`` and the component-level determinism check in
``tests/property/test_suggestion_determinism.py``.

Two complementary assertions encode the property:

* **Repeated calls on one scorer.** For any ``(resume, jd)`` pair, calling
  :meth:`Match_Scorer.score` twice on the *same* scorer yields ``ScoreResult``
  values that are equal field-for-field — equal ``score``, equal ``breakdown``,
  equal ``matched_keywords`` / ``missing_keywords`` lists in the same order,
  equal ``suggestions`` in the same order, and an equal ``scorer_version``.
  This is the core "identical input -> identical result" claim.

* **Distinct scorers sharing a Scorer_Version.** Two separately-constructed
  ``Match_Scorer`` instances bound to two distinct :class:`Skill_Lexicon`
  instances that carry the *same* ``lexicon_version`` (hence the same
  ``Scorer_Version``), built with the same weights and caps, produce an
  identical ``ScoreResult`` for the same input. This shows the result depends
  only on the inputs and the ``Scorer_Version`` — never on object identity or
  scorer instance (Requirement 5.4's "identical Scorer_Version" clause).

Generators mix real lexicon surface forms (canonical terms and aliases, which
drive the keyword-coverage half and the category suggestion templates) with
free-text words (which populate the TF-IDF vocabulary and the default
template), plus fully arbitrary text, so determinism is asserted across the
whole meaningful input space rather than only on skill-dense documents.
"""

from __future__ import annotations

import json
from importlib import resources

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer, ScoreResult

# Scorer configuration mirrors the documented ``MATCHLAYER_*`` defaults
# (``score_weight_similarity`` 0.6, ``score_weight_keyword`` 0.4 — which sum to
# 1.0 — ``match_max_keywords`` 50, ``match_max_suggestions`` 10). The values are
# injected here exactly as the ``ml/`` adapter injects them from ``Settings``;
# the scoring core never reads config itself (Requirement 5.8 import boundary).
_W_SIMILARITY = 0.6
_W_KEYWORD = 0.4
_MAX_KEYWORDS = 50
_MAX_SUGGESTIONS = 10


def _load_fresh_lexicon() -> Skill_Lexicon:
    """Construct a *distinct* Skill_Lexicon from the same committed artifact.

    ``load_lexicon`` is process-cached and returns one shared instance; this
    parses the same package-data JSON into a separate object so the
    "two scorers, same Scorer_Version" assertion is non-trivial (distinct
    lexicon objects whose ``lexicon_version`` — and therefore
    ``scorer_version`` — are equal).
    """
    text = (
        resources.files("matchlayer_api.scoring.data")
        .joinpath("skill_lexicon.v1.json")
        .read_text(encoding="utf-8")
    )
    return Skill_Lexicon(json.loads(text))


# The shared, cached runtime lexicon and a distinct sibling with an identical
# version (hence an identical Scorer_Version).
_LEXICON = load_lexicon()
_LEXICON_SIBLING = _load_fresh_lexicon()


def _build_scorer(lexicon: Skill_Lexicon) -> Match_Scorer:
    """Build a Match_Scorer from ``lexicon`` with the documented defaults."""
    return Match_Scorer(
        lexicon,
        w_similarity=_W_SIMILARITY,
        w_keyword=_W_KEYWORD,
        max_keywords=_MAX_KEYWORDS,
        max_suggestions=_MAX_SUGGESTIONS,
    )


# Surface forms drawn from the lexicon: canonical terms and every alias. Seeding
# generated text with these makes the keyword-coverage half and the category
# suggestion templates fire on real vocabulary rather than only free text.
_LEXICON_TERMS: list[str] = sorted(
    set(_LEXICON.canonical_terms) | {alias for entry in _LEXICON.entries for alias in entry.aliases}
)

# A token is either a real skill surface form or a short random a-z word; random
# words keep the TF-IDF vocabulary populated with non-lexicon terms.
_random_word = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122),  # a-z
    min_size=1,
    max_size=15,
)
_token = st.one_of(st.sampled_from(_LEXICON_TERMS), _random_word)

# A skill-rich document built by joining tokens with spaces (drives matched /
# missing keywords and the coverage component).
_skill_rich_text = st.lists(_token, min_size=0, max_size=60).map(" ".join)

# Fully arbitrary text, so determinism also holds for inputs unrelated to the
# lexicon (the "for any text" clause), including empty / whitespace-only ones.
_arbitrary_text = st.text(min_size=0, max_size=400)

_document = st.one_of(_skill_rich_text, _arbitrary_text)


def _assert_results_identical(first: ScoreResult, second: ScoreResult) -> None:
    """Assert two ``ScoreResult`` values are equal field-for-field.

    ``ScoreResult``, ``ScoreBreakdown``, ``Keyword`` and ``Suggestion`` are all
    frozen dataclasses, so ``first == second`` already compares every field
    (and list order). The per-field assertions below spell out each clause of
    Property 2 explicitly so a regression names the exact part that drifted.
    Float components are compared for exact equality on purpose: a deterministic
    scorer runs the identical code path on identical inputs, so the bits must
    match — any difference is precisely the non-determinism this property
    forbids.
    """
    # score
    assert first.score == second.score

    # score_breakdown (all components + weights + final score)
    assert first.breakdown == second.breakdown
    assert first.breakdown.similarity_component == second.breakdown.similarity_component
    assert first.breakdown.keyword_coverage_component == second.breakdown.keyword_coverage_component
    assert first.breakdown.weight_similarity == second.breakdown.weight_similarity
    assert first.breakdown.weight_keyword == second.breakdown.weight_keyword
    assert first.breakdown.final_score == second.breakdown.final_score

    # matched_keywords — same values in the same order
    assert first.matched_keywords == second.matched_keywords
    assert [kw.term for kw in first.matched_keywords] == [kw.term for kw in second.matched_keywords]
    assert [kw.weight for kw in first.matched_keywords] == [
        kw.weight for kw in second.matched_keywords
    ]

    # missing_keywords — same values in the same order
    assert first.missing_keywords == second.missing_keywords
    assert [kw.term for kw in first.missing_keywords] == [kw.term for kw in second.missing_keywords]
    assert [kw.weight for kw in first.missing_keywords] == [
        kw.weight for kw in second.missing_keywords
    ]

    # suggestions — same values in the same order
    assert first.suggestions == second.suggestions
    assert [s.keyword for s in first.suggestions] == [s.keyword for s in second.suggestions]
    assert [s.text for s in first.suggestions] == [s.text for s in second.suggestions]

    # scorer_version
    assert first.scorer_version == second.scorer_version

    # Whole-object equality as the final, exhaustive check.
    assert first == second


@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
def test_score_is_deterministic_for_repeated_calls(resume_text: str, job_description: str) -> None:
    """Two calls on the same scorer return an identical ``ScoreResult``.

    The core of Property 2: identical inputs through a single scorer yield a
    result that is equal in ``score``, ``breakdown``, ``matched_keywords``,
    ``missing_keywords``, ``suggestions``, and ``scorer_version`` (Requirements
    5.4, 5.7).
    """
    scorer = _build_scorer(_LEXICON)

    first = scorer.score(resume_text, job_description)
    second = scorer.score(resume_text, job_description)

    _assert_results_identical(first, second)


@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
def test_score_is_deterministic_across_instances_with_same_scorer_version(
    resume_text: str, job_description: str
) -> None:
    """Distinct scorers sharing a ``Scorer_Version`` produce identical results.

    The result depends only on the inputs and the ``Scorer_Version``, not on
    object identity. Two scorers bound to two distinct lexicon instances with
    the same ``scorer_version`` (asserted below) and built with the same weights
    and caps produce the same ``ScoreResult`` for the same input (Requirement
    5.4's "identical Scorer_Version" clause; Requirement 5.7's stamp).
    """
    # Precondition of the property: the two lexicons share a Scorer_Version.
    assert _LEXICON.scorer_version == _LEXICON_SIBLING.scorer_version

    scorer_a = _build_scorer(_LEXICON)
    scorer_b = _build_scorer(_LEXICON_SIBLING)

    result_a = scorer_a.score(resume_text, job_description)
    result_b = scorer_b.score(resume_text, job_description)

    # Both must carry the shared Scorer_Version stamp ...
    assert result_a.scorer_version == _LEXICON.scorer_version
    assert result_b.scorer_version == _LEXICON_SIBLING.scorer_version
    # ... and the full results must be identical.
    _assert_results_identical(result_a, result_b)
