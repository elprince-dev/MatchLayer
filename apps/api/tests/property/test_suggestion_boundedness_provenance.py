"""Feature: phase-1-matching — Property 10.

Property 10: Suggestions are bounded and derived only from missing keywords.

    *For any* ``missing_keywords`` set, ``Suggestion_Generator.generate``
    returns at most ``MATCHLAYER_MATCH_MAX_SUGGESTIONS`` suggestions and
    every suggestion references a keyword that is a member of the
    ``missing_keywords`` set (no fabricated or unrelated terms).

**Validates: Requirements 7.1, 7.2, 7.5**

This module is the universal companion to the concrete-example coverage of
the ``Suggestion_Generator``. Where unit tests pin down specific suggestion
text, this file asserts the boundedness + provenance property holds across a
wide, generated input space using Hypothesis (>=100 examples).

Scope: the **non-empty** missing-keyword case. The affirmative behaviour for
an *empty* missing set (exactly one suggestion) is Property 11 (task 4.8) and
is deliberately out of scope here; every generated ``missing`` list has at
least one element.

The ``max_suggestions`` cap is a constructor parameter (the configured
``MATCHLAYER_MATCH_MAX_SUGGESTIONS``, default 10); the generator validates it
as non-negative, so the generated bound ranges over ``0..N`` — ``0`` is the
strongest boundedness edge (the result must then be empty) and provenance
holds vacuously there.

Three complementary assertions encode the property robustly:

* **Boundedness (Requirement 7.2).** ``len(result) <= max_suggestions`` for
  every input — the generator slices its per-keyword suggestions down to the
  configured cap.

* **Provenance, as sub-multiset containment (Requirements 7.1, 7.5).** The
  multiset of keywords the result references is contained in the multiset of
  supplied missing terms. This single ``Counter`` comparison captures three
  things at once: no fabricated term (every result keyword came from the
  input), no unrelated term, and no keyword emitted more times than it was
  supplied. It is robust to duplicate terms in the input.

* **Exactly one non-empty missing keyword per suggestion (Requirement 7.5).**
  Each suggestion carries a single ``keyword`` that is a non-empty member of
  the supplied missing set. The empty-string sentinel that marks the
  affirmative suggestion never appears on the non-empty path.

Inputs are built without importing the sibling ``keyword_analyzer`` module
(owned by a parallel task): the generator accepts the structural
``KeywordLike`` protocol (anything with ``term: str`` + ``weight: float``), so
the tiny :class:`_FakeKeyword` dataclass below satisfies it directly. Terms
are drawn from both arbitrary non-empty text and real ``Skill_Lexicon`` terms
(canonical forms and their aliases) so the lexicon-backed and free-text
(TF-IDF) template branches are both exercised.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.suggestions import Suggestion, Suggestion_Generator

# The committed runtime lexicon. Immutable and cached; shared across examples.
_LEXICON: Skill_Lexicon = load_lexicon()


def _real_lexicon_terms() -> list[str]:
    """Canonical terms plus every alias from the committed lexicon.

    Sampling real terms exercises the lexicon-backed template branch (and its
    per-category copy + display-name lookup), complementing the free-text
    branch driven by arbitrary generated text.
    """
    terms: list[str] = []
    for entry in _LEXICON.entries:
        terms.append(entry.canonical)
        terms.extend(entry.aliases)
    return terms


_REAL_TERMS: list[str] = _real_lexicon_terms()


@dataclass(frozen=True, slots=True)
class _FakeKeyword:
    """Minimal structural stand-in for the analyzer's ``Keyword``.

    Carries exactly the two fields the ``KeywordLike`` protocol requires, so
    the generator treats it identically to a real analyzed keyword without a
    cross-module import.
    """

    term: str
    weight: float


# A keyword term: either a real lexicon term (canonical or alias) or arbitrary
# non-empty text. ``min_size=1`` keeps terms non-empty, modelling the analyzed
# set the generator actually receives (the Keyword_Analyzer never emits an
# empty term) and keeping the empty-string affirmative sentinel out of the
# non-empty input space.
_terms = st.one_of(
    st.sampled_from(_REAL_TERMS),
    st.text(min_size=1, max_size=40),
)

# Weights are finite floats so the generator's descending-weight sort is total
# (NaN/inf would make the ordering ill-defined — irrelevant to boundedness and
# provenance, and covered separately by the ordering/determinism properties).
_weights = st.floats(allow_nan=False, allow_infinity=False)

_keywords = st.builds(_FakeKeyword, term=_terms, weight=_weights)

# A NON-EMPTY missing-keyword list (the empty case is Property 11 / task 4.8).
_missing_lists = st.lists(_keywords, min_size=1, max_size=40)

# Arbitrary configured cap, including 0 (the strongest boundedness edge — the
# constructor accepts 0 and rejects only negatives).
_max_suggestions = st.integers(min_value=0, max_value=50)


@settings(max_examples=200, deadline=None)
@given(missing=_missing_lists, max_suggestions=_max_suggestions)
def test_suggestions_are_bounded_and_derived_only_from_missing(
    missing: list[_FakeKeyword],
    max_suggestions: int,
) -> None:
    """Suggestions stay within the cap and reference only supplied missing terms.

    Property 10, non-empty case (Requirements 7.1, 7.2, 7.5): for any
    non-empty missing-keyword list and any non-negative ``max_suggestions``,
    :meth:`Suggestion_Generator.generate` returns at most ``max_suggestions``
    suggestions, and every suggestion references exactly one non-empty keyword
    that was supplied in the missing set (no fabricated or unrelated terms).
    """
    generator = Suggestion_Generator(_LEXICON, max_suggestions=max_suggestions)

    result = generator.generate(missing)

    # Boundedness (Requirement 7.2): never more than the configured cap.
    assert len(result) <= max_suggestions

    supplied = Counter(kw.term for kw in missing)
    produced = Counter(s.keyword for s in result)

    # Provenance (Requirements 7.1, 7.5): the produced keywords are a
    # sub-multiset of the supplied missing terms — nothing fabricated, nothing
    # unrelated, nothing emitted more often than it was supplied.
    assert produced <= supplied

    # Each suggestion references exactly one non-empty missing keyword
    # (Requirement 7.5). The empty-string affirmative sentinel must not appear
    # on the non-empty path.
    for suggestion in result:
        assert isinstance(suggestion, Suggestion)
        assert suggestion.keyword != ""
        assert suggestion.keyword in supplied
