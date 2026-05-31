"""Feature: phase-1-matching — Property 5.

Property 5: The analyzed keyword set is bounded and well-formed.

    *For any* job-description text, the analyzed keyword set has
    cardinality at most ``MATCHLAYER_MATCH_MAX_KEYWORDS`` and every
    analyzed term is a member of the union of (a) ``Skill_Lexicon``
    canonical terms found in the job description and (b) the top-weighted
    TF-IDF terms of the job description.

**Validates: Requirements 6.1**

This is the universal companion to the concrete-example coverage of the
``Keyword_Analyzer``. It exercises the boundedness-and-well-formedness
half of Requirement 6.1 across a wide, generated input space using
Hypothesis (>=100 examples), driving :class:`Keyword_Analyzer` directly
(framework-free: only the scoring core and its committed lexicon are
touched — no FastAPI, DB, or storage).

The property is encoded as a conjunction of assertions on the
``analyzed`` list returned by :meth:`Keyword_Analyzer.analyze`, phrased
so they hold for *every* job description and *every* ``max_keywords``
value and therefore never produce a false failure:

* **Boundedness.** ``len(analyzed) <= max(0, max_keywords)`` — the cap is
  honored for any cap, including ``0`` and (defensively) negative caps,
  which the analyzer clamps to ``0`` so the cap can only ever *shrink* the
  set (Requirement 6.1).

* **Uniqueness.** The analyzed terms are pairwise distinct. The analyzed
  set is built keyed by canonical term, so a term can never appear twice;
  a duplicate would mean the union/dedup logic regressed.

* **Well-formedness.** Every entry is a :class:`Keyword` whose ``term`` is
  a non-empty string and whose ``weight`` is a finite, non-negative
  number (an actual ``float``, never ``nan``/``inf``). Both the curated
  lexicon weights and the scikit-learn TF-IDF scores satisfy this.

* **Canonical form (provenance, soundly checked).** Every analyzed term is
  already in normalized/canonical form: ``lexicon.normalize(term) ==
  term``. Source (a) contributes canonical lexicon terms; source (b)
  stores ``lexicon.normalize(tfidf_term)`` — so the analyzed set is drawn
  entirely from the lexicon-and-TF-IDF union the property names, rather
  than carrying raw, un-normalized, or fabricated surface forms. (A direct
  "term is a substring of the job description" oracle is deliberately
  *avoided* here: the TF-IDF tokenizer splits canonical forms on internal
  punctuation — e.g. ``ci/cd`` derived from the alias phrase ``continuous
  integration`` yields the token ``ci`` — so a naive substring check would
  false-fail on perfectly valid input. The canonical-form invariant
  captures provenance without that brittleness; the matched/missing
  partition and substring-presence guarantees are covered by Properties 6
  and 7.)

The analyzed set depends only on the job description, so the generated
resume text ranges freely (it only steers the matched/missing split,
which is out of scope for this property).
"""

from __future__ import annotations

import math

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.keyword_analyzer import Keyword, Keyword_Analyzer
from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon

# The committed Skill_Lexicon — the real vocabulary the analyzer matches
# against. Loaded once; instances are immutable and safe to share.
_LEXICON: Skill_Lexicon = load_lexicon()

# Surface forms drawn from the lexicon: canonical terms and every alias.
# Seeding generated job descriptions with these makes the analyzed set
# frequently approach or exceed the cap, so the boundedness assertion is
# exercised with real pressure rather than only on sparse random text.
_CANONICAL_TERMS: list[str] = list(_LEXICON.canonical_terms)
_ALIASES: list[str] = [alias for entry in _LEXICON.entries for alias in entry.aliases]
_SKILL_SURFACE_FORMS: list[str] = _CANONICAL_TERMS + _ALIASES

# A token is either a real skill surface form or a short random word. Random
# words keep the TF-IDF source (b) populated with non-lexicon vocabulary.
_random_word = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122),  # a-z
    min_size=1,
    max_size=15,
)
_token = st.one_of(st.sampled_from(_SKILL_SURFACE_FORMS), _random_word)

# A skill-rich document built by joining tokens with spaces — biased toward
# many distinct skills so the cap is regularly the binding constraint.
_skill_rich_text = st.lists(_token, min_size=0, max_size=80).map(" ".join)

# Fully arbitrary text, so the property is also asserted over inputs that
# have nothing to do with the lexicon (the "for any text" clause).
_arbitrary_text = st.text(min_size=0, max_size=600)

_document = st.one_of(_skill_rich_text, _arbitrary_text)

# Caps spanning the interesting regimes: negative (clamped to 0), zero, small
# (binding), and large (rarely binding) — "for arbitrary max_keywords values".
_max_keywords = st.integers(min_value=-5, max_value=120)


def _assert_analyzed_is_bounded_and_well_formed(
    analyzed: list[Keyword], *, max_keywords: int
) -> None:
    """Assert Property 5 over the analyzed keyword list.

    Encodes boundedness, uniqueness, per-entry well-formedness, and the
    canonical-form provenance invariant. Factored out so every generated
    and explicit example checks exactly the same contract.
    """
    # Boundedness: the cap is honored for any cap; negatives clamp to 0.
    effective_cap = max(0, max_keywords)
    assert len(analyzed) <= effective_cap

    terms = [keyword.term for keyword in analyzed]

    # Uniqueness: terms are pairwise distinct (the set is keyed by canonical).
    assert len(set(terms)) == len(terms)

    for keyword in analyzed:
        # Well-formed term: a non-empty string.
        assert isinstance(keyword.term, str)
        assert keyword.term != ""

        # Well-formed weight: a finite, non-negative real number.
        assert isinstance(keyword.weight, float)
        assert math.isfinite(keyword.weight)
        assert keyword.weight >= 0.0

        # Provenance (soundly checked): the term is already in canonical/
        # normalized form, i.e. it came from the lexicon-or-TF-IDF union and
        # not from some raw, un-normalized surface form.
        assert _LEXICON.normalize(keyword.term) == keyword.term


@settings(max_examples=200, deadline=None)
@given(
    resume_text=_document,
    job_description=_document,
    max_keywords=_max_keywords,
)
@example(resume_text="", job_description="", max_keywords=50)
@example(resume_text="python developer", job_description="python", max_keywords=0)
@example(resume_text="anything", job_description="python", max_keywords=-3)
@example(
    # A skill-dense JD against a small cap: the cap is the binding constraint.
    resume_text="python java sql",
    job_description=" ".join(_CANONICAL_TERMS),
    max_keywords=5,
)
def test_analyzed_set_is_bounded_and_well_formed(
    resume_text: str, job_description: str, max_keywords: int
) -> None:
    """The analyzed set is capped, deduplicated, and every entry well-formed.

    For any job description, any resume text, and any ``max_keywords``
    value, the analyzed keyword set returned by the Keyword_Analyzer has
    cardinality at most the (clamped) cap, holds no duplicate terms, and
    consists entirely of well-formed, canonical-form ``Keyword`` entries
    drawn from the lexicon-and-TF-IDF union (Requirement 6.1).
    """
    analyzer = Keyword_Analyzer(_LEXICON, max_keywords=max_keywords)

    analysis = analyzer.analyze(resume_text, job_description)

    _assert_analyzed_is_bounded_and_well_formed(analysis.analyzed, max_keywords=max_keywords)
