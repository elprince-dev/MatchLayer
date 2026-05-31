"""Feature: phase-1-matching — Property 12.

Property 12: Suggestion generation is deterministic.

    *For any* ``missing_keywords`` set, two invocations of
    ``Suggestion_Generator.generate`` with the same input and the same
    ``Scorer_Version`` produce an identical ordered suggestion list.

**Validates: Requirements 7.4**

The ``Suggestion_Generator`` is a pure, non-LLM transform: fixed templates
keyed off each missing term's :class:`Skill_Lexicon` metadata, ordered by a
*stable* descending-weight sort. Both of those design choices — fixed
templates and a stable sort — are what make the output a deterministic
function of (input list, lexicon). This module pins that contract across a
wide generated input space using Hypothesis (≥100 examples), complementing
the concrete-example coverage in ``tests/unit/test_suggestions.py``.

Two complementary assertions encode the property:

* **Repeated calls on one generator.** For any ``missing`` list and any
  ``max_suggestions`` cap, calling :meth:`Suggestion_Generator.generate`
  twice on the *same* generator yields lists that are element-for-element
  equal (same length, same order, same ``Suggestion`` values). This is the
  core "identical input → identical ordered list" claim.

* **Distinct generators sharing a Scorer_Version, with a rebuilt input.**
  Two separately-constructed generators bound to two separate
  :class:`Skill_Lexicon` instances that carry the *same* ``lexicon_version``
  (hence the same ``Scorer_Version``) produce identical output for an input
  list that is rebuilt from the same ``(term, weight)`` pairs in the same
  order. This shows the result depends only on the input *values* and the
  Scorer_Version — never on object identity or generator instance.

Generators intentionally exercise the stable tiebreak: weights are drawn
mostly from a small finite pool so equal-weight ties are common, and terms
mix real lexicon canonicals/aliases (which hit the category templates) with
free-text terms (which hit the default template). Duplicate terms are
allowed so the same multiset, in the same order, is reproduced faithfully.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.suggestions import Suggestion_Generator


@dataclass(frozen=True, slots=True)
class _Kw:
    """Minimal weighted keyword satisfying the generator's ``KeywordLike``.

    The generator accepts anything exposing ``term: str`` and ``weight:
    float`` (a structural Protocol), so this stand-in avoids importing the
    sibling ``keyword_analyzer`` module owned by a parallel task.
    """

    term: str
    weight: float


def _load_fresh_lexicon() -> Skill_Lexicon:
    """Construct a *distinct* Skill_Lexicon from the same committed artifact.

    ``load_lexicon`` is process-cached and returns one shared instance; this
    parses the same package-data JSON into a separate object so the
    "two generators, same Scorer_Version" assertion is non-trivial (distinct
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
# version (hence identical Scorer_Version).
_LEXICON = load_lexicon()
_LEXICON_SIBLING = _load_fresh_lexicon()

# Term pool: real canonical terms and their aliases (drive the category
# templates and alias normalization) plus arbitrary free-text (drives the
# default template). ``sorted(set(...))`` keeps the sampled_from domain
# stable across runs.
_LEXICON_TERMS: list[str] = sorted(
    {term for term in _LEXICON.canonical_terms}
    | {alias for entry in _LEXICON.entries for alias in entry.aliases}
)

_terms = st.one_of(
    st.sampled_from(_LEXICON_TERMS),
    st.text(min_size=1, max_size=24),
)

# Weights: a small finite pool makes equal-weight ties frequent (so the
# stable tiebreak is genuinely exercised), widened by bounded arbitrary
# floats. NaN/infinity are excluded — real lexicon weights are finite — so
# the strategy stays inside the meaningful input space.
_weights = st.one_of(
    st.sampled_from([0.0, 0.25, 0.5, 0.7, 0.9, 1.0]),
    st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
)

_keywords = st.builds(_Kw, term=_terms, weight=_weights)

# Include the empty list (the affirmative-suggestion case) and duplicate
# terms (a multiset) so reproduction of "the same input order" is tested.
_missing_lists = st.lists(_keywords, min_size=0, max_size=40)

# Span max_suggestions=0 (everything sliced away for a non-empty input) up
# through more than the lexicon's size, so the cap's effect is also pinned.
_max_suggestions = st.integers(min_value=0, max_value=50)


@settings(max_examples=200, deadline=None)
@given(missing=_missing_lists, max_suggestions=_max_suggestions)
def test_generate_is_deterministic_for_repeated_calls(
    missing: list[_Kw], max_suggestions: int
) -> None:
    """Two calls on the same generator return an identical ordered list.

    The core of Property 12: identical input through a single generator
    yields byte-for-byte identical suggestions — same length, same order,
    and equal ``Suggestion`` values (keyword + text).
    """
    generator = Suggestion_Generator(_LEXICON, max_suggestions=max_suggestions)

    first = generator.generate(missing)
    second = generator.generate(missing)

    assert first == second
    assert len(first) == len(second)
    assert [s.keyword for s in first] == [s.keyword for s in second]
    assert [s.text for s in first] == [s.text for s in second]


@settings(max_examples=200, deadline=None)
@given(missing=_missing_lists, max_suggestions=_max_suggestions)
def test_generate_is_deterministic_across_instances_and_rebuilt_input(
    missing: list[_Kw], max_suggestions: int
) -> None:
    """Distinct generators + a rebuilt-but-equal input produce identical output.

    The output depends only on the input values and the Scorer_Version, not
    on object identity. Two generators bound to two distinct lexicon
    instances with the same ``scorer_version`` (asserted below) produce the
    same ordered list when fed the same ``(term, weight)`` pairs in the same
    order.
    """
    # Same multiset, same order, fresh objects — proves identity-independence.
    rebuilt = [_Kw(term=kw.term, weight=kw.weight) for kw in missing]

    # Precondition of the property: the two lexicons share a Scorer_Version.
    assert _LEXICON.scorer_version == _LEXICON_SIBLING.scorer_version

    generator_a = Suggestion_Generator(_LEXICON, max_suggestions=max_suggestions)
    generator_b = Suggestion_Generator(_LEXICON_SIBLING, max_suggestions=max_suggestions)

    out_a = generator_a.generate(missing)
    out_b = generator_b.generate(rebuilt)

    assert out_a == out_b
    assert [s.keyword for s in out_a] == [s.keyword for s in out_b]
    assert [s.text for s in out_a] == [s.text for s in out_b]
