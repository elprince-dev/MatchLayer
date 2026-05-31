"""Unit tests for the Suggestion_Generator (task 4.6).

Concrete-example coverage for
:mod:`matchlayer_api.scoring.suggestions` — the rule-based, non-LLM suggestion
generator behind Requirement 7. The property tests (tasks 4.7 through 4.9) pin
the universal invariants across many inputs; these examples pin the specific
contracts and the exact phrasing decisions:

* **7.1 / 7.5** Each suggestion is derived from one missing keyword and the
  lexicon metadata for that term, phrased as a *conditional user action* — it
  never asserts experience, employers, dates, or credentials.
* **7.2** At most ``max_suggestions``, ordered by descending missing-keyword
  weight.
* **7.3** Empty missing set → exactly one affirmative suggestion.

References: Requirements 7.1, 7.2, 7.3, 7.5; design §"Suggestion_Generator".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.suggestions import Suggestion, Suggestion_Generator

# ---------------------------------------------------------------------------
# Fixtures: a small lexicon and a minimal Keyword stand-in
# ---------------------------------------------------------------------------

# Skills spanning several categories so the category-keyed templates are
# exercised, plus a soft skill (the most fabrication-prone category — guidance
# must still avoid claiming the user *has* the trait).
_SKILLS: list[dict[str, Any]] = [
    {
        "canonical": "python",
        "display": "Python",
        "category": "language",
        "weight": 1.0,
        "aliases": ["py"],
    },
    {
        "canonical": "react",
        "display": "React",
        "category": "framework",
        "weight": 0.95,
        "aliases": ["reactjs"],
    },
    {
        "canonical": "leadership",
        "display": "Leadership",
        "category": "soft_skill",
        "weight": 0.6,
        "aliases": ["tech lead"],
    },
]


@dataclass(frozen=True)
class _Keyword:
    """Minimal stand-in for the analyzer's ``Keyword`` (structural ``KeywordLike``)."""

    term: str
    weight: float


@pytest.fixture
def lexicon() -> Skill_Lexicon:
    return Skill_Lexicon({"schema_version": 1, "lexicon_version": "1.0.0", "skills": _SKILLS})


@pytest.fixture
def generator(lexicon: Skill_Lexicon) -> Suggestion_Generator:
    return Suggestion_Generator(lexicon, max_suggestions=10)


# Phrases that would indicate the generator fabricated history about the user.
# Requirement 7.5: suggestions must be actions for the user, never assertions
# that the user already has the experience.
_FABRICATION_MARKERS = (
    "you have experience in",
    "you are an expert",
    "you worked at",
    "in 20",  # a fabricated year/date
    "years of experience",
)


# ---------------------------------------------------------------------------
# Requirement 7.3 — empty missing set
# ---------------------------------------------------------------------------


def test_empty_missing_yields_exactly_one_affirmative_suggestion(
    generator: Suggestion_Generator,
) -> None:
    """Empty ``missing`` → exactly one affirmative suggestion, not an empty list."""
    result = generator.generate([])

    assert len(result) == 1
    only = result[0]
    assert isinstance(only, Suggestion)
    # The affirmative suggestion addresses no specific missing keyword.
    assert only.keyword == ""
    assert "already covers" in only.text.lower()


def test_empty_missing_affirmative_even_when_cap_is_zero(lexicon: Skill_Lexicon) -> None:
    """The affirmative suggestion is mandated by 7.3 regardless of the cap."""
    generator = Suggestion_Generator(lexicon, max_suggestions=0)
    assert len(generator.generate([])) == 1


# ---------------------------------------------------------------------------
# Requirement 7.1 / 7.5 — provenance and phrasing
# ---------------------------------------------------------------------------


def test_each_suggestion_references_exactly_one_missing_keyword(
    generator: Suggestion_Generator,
) -> None:
    """Every suggestion's ``keyword`` is one of the supplied missing terms."""
    missing = [_Keyword("python", 1.0), _Keyword("react", 0.95)]
    result = generator.generate(missing)

    assert [s.keyword for s in result] == ["python", "react"]
    assert all(s.keyword in {"python", "react"} for s in result)


def test_suggestion_uses_lexicon_display_name(generator: Suggestion_Generator) -> None:
    """The template renders the term's lexicon display name, not the raw term."""
    [suggestion] = generator.generate([_Keyword("python", 1.0)])
    assert "Python" in suggestion.text  # display name, not the lower-cased canonical


def test_suggestions_never_fabricate_experience(generator: Suggestion_Generator) -> None:
    """No suggestion asserts the user already has the experience (Requirement 7.5)."""
    missing = [_Keyword("python", 1.0), _Keyword("react", 0.95), _Keyword("leadership", 0.6)]
    result = generator.generate(missing)

    for suggestion in result:
        lowered = suggestion.text.lower()
        for marker in _FABRICATION_MARKERS:
            assert marker not in lowered, f"fabrication marker {marker!r} in {suggestion.text!r}"
        # Guidance is phrased conditionally / as an action, not as a fact.
        assert lowered.startswith(("if you", "your resume already"))


def test_free_text_term_without_lexicon_entry_uses_generic_template(
    generator: Suggestion_Generator,
) -> None:
    """A missing term absent from the lexicon still gets a (generic) suggestion.

    Free-text TF-IDF terms have no lexicon entry; the generator falls back to
    the default template and renders the term verbatim, still referencing that
    single keyword (Requirement 7.1).
    """
    [suggestion] = generator.generate([_Keyword("kubernetes orchestration", 0.4)])
    assert suggestion.keyword == "kubernetes orchestration"
    assert "kubernetes orchestration" in suggestion.text


# ---------------------------------------------------------------------------
# Requirement 7.2 — boundedness and ordering
# ---------------------------------------------------------------------------


def test_at_most_max_suggestions(lexicon: Skill_Lexicon) -> None:
    """The list is capped at ``max_suggestions`` (Requirement 7.2)."""
    generator = Suggestion_Generator(lexicon, max_suggestions=2)
    missing = [_Keyword("python", 1.0), _Keyword("react", 0.95), _Keyword("leadership", 0.6)]

    result = generator.generate(missing)

    assert len(result) == 2
    # The two highest-weighted terms are kept.
    assert [s.keyword for s in result] == ["python", "react"]


def test_ordered_by_descending_missing_keyword_weight(
    generator: Suggestion_Generator,
) -> None:
    """Suggestions come back ordered by descending weight even if input is not."""
    missing = [_Keyword("leadership", 0.6), _Keyword("python", 1.0), _Keyword("react", 0.95)]

    result = generator.generate(missing)

    assert [s.keyword for s in result] == ["python", "react", "leadership"]


def test_equal_weight_ties_preserve_input_order(generator: Suggestion_Generator) -> None:
    """Ties keep the analyzer's input order (stable sort → determinism, 7.4)."""
    missing = [_Keyword("react", 0.8), _Keyword("python", 0.8)]
    result = generator.generate(missing)
    assert [s.keyword for s in result] == ["react", "python"]


def test_generation_is_deterministic(generator: Suggestion_Generator) -> None:
    """Identical input produces an identical ordered list (Requirement 7.4)."""
    missing = [_Keyword("python", 1.0), _Keyword("react", 0.95)]
    assert generator.generate(missing) == generator.generate(list(missing))


def test_negative_cap_is_rejected(lexicon: Skill_Lexicon) -> None:
    """A negative cap is a programming error and fails fast at construction."""
    with pytest.raises(ValueError, match="non-negative"):
        Suggestion_Generator(lexicon, max_suggestions=-1)
