"""Suggestion_Generator: rule-based improvement suggestions (phase-1-matching, task 4.6).

The ``Suggestion_Generator`` is the part of the framework-free ``Match_Scorer``
that turns the *missing* keyword set into concrete, plain-text improvement
suggestions. Phase 1 is explicitly **non-LLM** (``product.md`` "infrastructure
before intelligence", the $20/month ceiling): every suggestion comes from a
fixed template keyed off the missing term and its :class:`Skill_Lexicon`
metadata (display name + category). No LLM, embedding model, or external
service participates (Requirement 7.1).

Behavior (Requirement 7):

* **7.1** Suggestions are derived *solely* from the missing-keyword set and the
  lexicon metadata for those terms, using fixed rules/templates.
* **7.2** At most ``max_suggestions`` suggestions, ordered by descending weight
  of the missing keyword each one addresses. ``max_suggestions`` is supplied by
  the caller (the configured ``MATCHLAYER_MATCH_MAX_SUGGESTIONS``); it is a
  constructor parameter rather than read from config here, because ``scoring/``
  imports only scikit-learn and the standard library (Requirement 10.1 import
  boundary) and never ``matchlayer_api.config``.
* **7.3** An empty missing set yields exactly **one** affirmative suggestion
  (never an unexplained empty list).
* **7.4** Deterministic: identical input + identical lexicon (hence
  Scorer_Version) produce an identical ordered list. Templates are fixed and
  the ordering uses a stable sort, so ties keep the analyzer's input order.
* **7.5** No suggestion fabricates experience, employers, dates, or
  credentials. Each suggestion references exactly one missing keyword and
  phrases its guidance conditionally ("If you've used X, ...") as an action for
  the *user* to take — never as an assertion about the user's history.

Decoupling note: the design types ``generate``'s input as ``list[Keyword]``,
where ``Keyword`` (``{term, weight}``) belongs to the sibling
``keyword_analyzer`` module owned by a parallel task. To avoid a hard import
across that boundary (and the list-invariance friction it would create for the
``Match_Scorer`` caller), this module accepts a ``Sequence`` of the structural
:class:`KeywordLike` protocol — anything carrying ``term: str`` and
``weight: float`` matches, including the analyzer's concrete ``Keyword``.

Design reference: "Suggestion_Generator". Requirements covered: 7.1, 7.2, 7.3,
7.5 (and 7.4 determinism, exercised by the property tests in task 4.9).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from matchlayer_api.scoring.lexicon import Skill_Lexicon

# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


@runtime_checkable
class KeywordLike(Protocol):
    """Structural type for a weighted keyword (the analyzer's ``Keyword``).

    A read-only view of the two fields the generator needs: the normalized
    ``term`` and its ``weight``. Declared as a :class:`~typing.Protocol` so the
    generator does not import the ``keyword_analyzer`` module (owned by a
    parallel task) — any object exposing ``term``/``weight`` satisfies it.
    """

    @property
    def term(self) -> str: ...

    @property
    def weight(self) -> float: ...


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One rule-based improvement suggestion.

    ``keyword`` is the missing term this suggestion addresses (empty only for
    the affirmative "you already cover everything" suggestion, which addresses
    no missing term). ``text`` is the plain-text, user-facing guidance. Frozen
    so a produced suggestion is an immutable value, consistent with the
    ``ScoreResult`` dataclass in ``scorer.py``.
    """

    keyword: str
    text: str


# ---------------------------------------------------------------------------
# Templates (fixed rules — Requirement 7.1, 7.5)
# ---------------------------------------------------------------------------

# Each template is phrased conditionally as an action for the user to take, so
# the generator never asserts the user has experience it cannot know about
# (Requirement 7.5: no fabricated experience/employers/dates/credentials).
# Keyed by the Skill_Lexicon ``category`` of the missing term; ``{name}`` is the
# term's lexicon display name (or the raw term when it is a free-text TF-IDF
# term not present in the lexicon).
_CATEGORY_TEMPLATES: Final[dict[str, str]] = {
    "language": (
        "If you've written code in {name}, list it in your skills section and "
        "point to a project or role where you used it."
    ),
    "framework": (
        "If you've built anything with {name}, mention it alongside the "
        "projects where you applied it."
    ),
    "library": (
        "If you've used {name} in your work, call it out where you describe the relevant project."
    ),
    "database": (
        "If you've worked with {name}, note it in your skills and tie it to a "
        "system you built or maintained."
    ),
    "cloud": (
        "If you've deployed or operated services on {name}, highlight that in "
        "your most relevant role."
    ),
    "devops": (
        "If you've used {name} in your delivery pipeline, describe where it fit into your workflow."
    ),
    "data": (
        "If you've applied {name} in your work, point to a concrete task or "
        "project where you used it."
    ),
    "tool": (
        "If you're familiar with {name}, add it to your skills and reference where you've used it."
    ),
    "testing": (
        "If you write tests with {name}, mention it where you describe your testing practices."
    ),
    "practice": (
        "If you've followed {name} on a team, describe how you applied it in a relevant role."
    ),
    "soft_skill": (
        "If you've demonstrated {name}, back it up with a concrete example in "
        "your experience bullets."
    ),
}

# Fallback for a missing term with no lexicon entry (a free-text TF-IDF term) or
# an unrecognized category.
_DEFAULT_TEMPLATE: Final[str] = (
    "If you have experience with {name}, consider adding it to your resume "
    "where it's relevant to this role."
)

# Requirement 7.3: the single affirmative suggestion for an empty missing set.
_AFFIRMATIVE_TEXT: Final[str] = (
    "Your resume already covers the key keywords identified for this job. Keep "
    "tailoring it to the specific role to keep the match strong."
)


class Suggestion_Generator:  # noqa: N801 -- design uses the underscored component name.
    """Produce deterministic, rule-based suggestions from missing keywords.

    Construct with the loaded :class:`Skill_Lexicon` (for per-term display
    names and categories) and the maximum number of suggestions to emit. Mirrors
    the ``Match_Scorer`` constructor shape (lexicon + configured knobs) so the
    ``ml/`` adapter can build both from the same inputs.
    """

    def __init__(self, lexicon: Skill_Lexicon, *, max_suggestions: int) -> None:
        self._lexicon: Final[Skill_Lexicon] = lexicon
        # The cap is a plain parameter (Requirement 7.2); validated as a
        # non-negative bound so slicing below is well-defined. The configured
        # source value (MATCHLAYER_MATCH_MAX_SUGGESTIONS, default 10) is already
        # constrained at the settings layer.
        if max_suggestions < 0:
            raise ValueError("max_suggestions must be non-negative")
        self._max_suggestions: Final[int] = max_suggestions

    def generate(self, missing: Sequence[KeywordLike]) -> list[Suggestion]:
        """Return suggestions for ``missing``, ordered by descending weight.

        * Empty ``missing`` → exactly one affirmative suggestion (Requirement
          7.3). The affirmative case is honored independently of
          ``max_suggestions`` because the spec mandates exactly one suggestion
          there.
        * Otherwise → one suggestion per missing keyword (each referencing that
          single keyword), capped at ``max_suggestions``, ordered by descending
          missing-keyword weight with a stable tiebreak (Requirements 7.1, 7.2,
          7.5).
        """
        if not missing:
            return [Suggestion(keyword="", text=_AFFIRMATIVE_TEXT)]

        # Stable, descending-weight sort. The analyzer already orders ``missing``
        # by descending weight (Requirement 6.6); sorting here makes the
        # ordering contract local and deterministic regardless of input order,
        # and stability preserves the analyzer's tiebreak for equal weights
        # (Requirement 7.4).
        ordered = sorted(missing, key=lambda kw: kw.weight, reverse=True)

        suggestions = [self._suggestion_for(kw) for kw in ordered]
        return suggestions[: self._max_suggestions]

    def _suggestion_for(self, keyword: KeywordLike) -> Suggestion:
        """Build the fixed-template suggestion for a single missing keyword."""
        entry = self._lexicon.entry(keyword.term)
        if entry is not None:
            name = entry.display
            template = _CATEGORY_TEMPLATES.get(entry.category, _DEFAULT_TEMPLATE)
        else:
            # A free-text TF-IDF term not in the lexicon: use the term verbatim
            # (it is already normalized/case-folded) with the generic template.
            name = keyword.term
            template = _DEFAULT_TEMPLATE
        return Suggestion(keyword=keyword.term, text=template.format(name=name))
