"""Feature: phase-1-matching — Property 11.

Property 11: Empty missing set yields exactly one affirmative suggestion.

    *For any* invocation in which ``missing_keywords`` is empty,
    ``Suggestion_Generator.generate`` returns exactly one affirmative
    suggestion (not an empty list).

**Validates: Requirements 7.3**

This is the universal companion to the concrete-example coverage in
``tests/unit/test_suggestions.py`` (which pins the affirmative case for a
single fixed generator). Here the claim is exercised across the full
generated input space with Hypothesis (>=100 examples): for *any*
non-negative ``max_suggestions`` — crucially including ``0``, which would
otherwise slice every suggestion away — and for *any* lexicon, an empty
missing set still yields exactly one suggestion, and that suggestion is the
affirmative "you already cover everything" one (it addresses no specific
missing keyword).

Two independent dimensions are varied to make the property robust:

* **``max_suggestions``** is drawn from the non-negative integers, including
  the boundary ``0``. Requirement 7.3 mandates the affirmative suggestion
  regardless of the cap, so the generator must honor the empty-missing case
  *before* applying the ``[: max_suggestions]`` slice. ``0`` is pinned with an
  explicit ``@example`` so the boundary is always checked.
* **the lexicon** is varied (small generated lexicons plus the real shipped
  artifact). The affirmative path consults no per-term metadata, so the
  property is expected to hold independently of lexicon content — varying it
  guards against a future change that accidentally couples the two.

The empty input is also varied across container types (``list``/``tuple``)
since the generator's contract keys off emptiness, not the concrete type.
"""

from __future__ import annotations

import string
from collections.abc import Sequence
from typing import Any

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.suggestions import (
    KeywordLike,
    Suggestion,
    Suggestion_Generator,
)

# The lexicon ``category`` values the generator's templates recognize, plus an
# unrecognized one to exercise the default-template fallback. The affirmative
# case never reaches a template, but generating a realistic spread keeps the
# "independent of lexicon content" claim honest.
_CATEGORIES: list[str] = [
    "language",
    "framework",
    "library",
    "database",
    "cloud",
    "devops",
    "data",
    "tool",
    "testing",
    "practice",
    "soft_skill",
    "unrecognized_category",
]

# Canonical terms are single lower-case tokens: already normalized (no
# case-folding or whitespace collapsing changes them), so ``unique=True`` on the
# raw strings is sufficient to avoid the loader's duplicate-canonical rejection.
_term = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=12)


@st.composite
def _lexicon_documents(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a valid :class:`Skill_Lexicon` artifact document."""
    canonicals = draw(st.lists(_term, min_size=0, max_size=8, unique=True))
    skills: list[dict[str, Any]] = [
        {
            "canonical": canonical,
            "display": canonical.capitalize(),
            "category": draw(st.sampled_from(_CATEGORIES)),
            "weight": draw(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
            ),
            "aliases": [],
        }
        for canonical in canonicals
    ]
    version = draw(st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True))
    return {"schema_version": 1, "lexicon_version": version, "skills": skills}


# Either a freshly generated small lexicon or the real shipped artifact. The
# real lexicon is loaded once (it is process-cached) and shared read-only.
_lexicons = st.one_of(
    st.builds(Skill_Lexicon, _lexicon_documents()),
    st.just(load_lexicon()),
)

# Empty inputs of both supported container shapes. ``generate`` keys off
# truthiness (``if not missing``), so an empty list and an empty tuple must
# behave identically.
_empty_missing: st.SearchStrategy[Sequence[KeywordLike]] = st.sampled_from([[], ()])


@settings(max_examples=200, deadline=None)
@example(lexicon=load_lexicon(), max_suggestions=0, missing=[])
@given(
    lexicon=_lexicons,
    max_suggestions=st.integers(min_value=0, max_value=10_000),
    missing=_empty_missing,
)
def test_empty_missing_yields_exactly_one_affirmative_suggestion(
    lexicon: Skill_Lexicon,
    max_suggestions: int,
    missing: Sequence[KeywordLike],
) -> None:
    """Empty ``missing`` → exactly one affirmative suggestion, for any cap/lexicon.

    Property 11 (Requirement 7.3). The affirmative suggestion is mandated even
    when ``max_suggestions == 0``, so the result is never an empty list and the
    single element addresses no specific missing keyword (its ``keyword`` is
    the empty string) and reads as the affirmative "already covers" message.
    """
    generator = Suggestion_Generator(lexicon, max_suggestions=max_suggestions)

    result = generator.generate(missing)

    # Exactly one suggestion — never an empty list, never more than one.
    assert len(result) == 1

    only = result[0]
    assert isinstance(only, Suggestion)
    # Affirmative: it references no specific missing keyword and phrases the
    # "you already cover the keywords" guidance.
    assert only.keyword == ""
    assert "already covers" in only.text.lower()
