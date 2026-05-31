"""Feature: phase-1-matching — Property 4.

Property 4: Empty resume or empty job description scores zero without error.

    *For any* resume text that is empty or whitespace-only, or *any*
    job-description text that is empty or whitespace-only (after normalization),
    ``Match_Scorer.score`` returns ``score == 0`` with both ``score_breakdown``
    component values equal to ``0``, and does not raise.

**Validates: Requirements 5.6**

This module is the universal companion to the eyeball-example coverage of the
``Match_Scorer``. Where unit examples pin down concrete scores for hand-picked
pairs, this file asserts the *empty-input* contract holds across a wide,
generated input space using Hypothesis (>=100 examples), driving
:class:`Match_Scorer` directly — framework-free (Requirement 10.1): only the
scoring core and its committed :class:`Skill_Lexicon` are touched, never
FastAPI, the database, storage, or settings.

The scorer normalizes both texts by case-folding and collapsing whitespace runs
(``" ".join(text.casefold().split())``). A text is therefore "empty after
normalization" exactly when it is the empty string or made only of whitespace
(spaces, tabs, newlines, carriage returns, form feeds, vertical tabs). For such
a text:

* the similarity half is forced to ``0.0`` because one of the two normalized
  documents is empty (the scorer guards the TF-IDF path with
  ``if resume_norm and jd_norm``), and
* the keyword-coverage half is ``0.0`` because either the analyzed set is empty
  (empty JD ⇒ no analyzed terms ⇒ coverage defined as ``0``) or no analyzed
  term can be present in an empty resume (empty resume ⇒ ``matched`` is empty ⇒
  ``0 / |analyzed| == 0``).

The blended, clamped score ``round(100 * (w_similarity * 0 + w_keyword * 0))``
is then exactly ``0`` under *any* valid weight pair. The property is asserted
across the three empty configurations the requirement enumerates — empty resume
with an arbitrary JD, an arbitrary resume with an empty JD, and both empty — and
under several weight pairs that each sum to ``1.0`` (the ``Settings`` validator's
contract), so the guarantee does not depend on a particular weighting.

The "without error" clause is checked structurally: each test calls
:meth:`Match_Scorer.score` and asserts on its result, so any exception raised by
the empty-input path would fail the example rather than be swallowed.
"""

from __future__ import annotations

from string import ascii_lowercase, digits

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer, ScoreResult

# ---------------------------------------------------------------------------
# Scorers under test
# ---------------------------------------------------------------------------

# The committed runtime lexicon — the real vocabulary the scorer matches
# against. Loaded once; instances are immutable and safe to share across
# examples and across the several weight-pair scorers below.
_LEXICON: Skill_Lexicon = load_lexicon()

# Caps mirror the production defaults (``MATCHLAYER_MATCH_MAX_KEYWORDS`` = 50,
# ``MATCHLAYER_MATCH_MAX_SUGGESTIONS`` = 10). The empty-input invariant is
# independent of the caps' values, but the defaults keep the (non-empty) JD's
# analyzed set realistically sized.
_MAX_KEYWORDS = 50
_MAX_SUGGESTIONS = 10

# Valid weight pairs, each summing to 1.0 as the ``Settings`` validator enforces
# (Requirement 5.3): the production default, an even split, and the two
# single-component extremes. ``score == 0`` for an empty input must hold under
# every one (``round(100 * (w_sim * 0 + w_kw * 0)) == 0`` for any pair).
_WEIGHT_PAIRS: list[tuple[float, float]] = [
    (0.6, 0.4),
    (0.5, 0.5),
    (1.0, 0.0),
    (0.0, 1.0),
]

# One scorer per weight pair, built once. Each holds only immutable state
# (lexicon-bound analyzer + generator, weights, stamped Scorer_Version) and is
# safe to reuse across every generated example.
_SCORERS: dict[tuple[float, float], Match_Scorer] = {
    (w_sim, w_kw): Match_Scorer(
        _LEXICON,
        w_similarity=w_sim,
        w_keyword=w_kw,
        max_keywords=_MAX_KEYWORDS,
        max_suggestions=_MAX_SUGGESTIONS,
    )
    for (w_sim, w_kw) in _WEIGHT_PAIRS
}

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# The whitespace characters that ``str.split()`` collapses away. A string drawn
# only from these (including the empty string at ``min_size=0``) normalizes to
# the empty string, i.e. it is "empty after normalization" in the precise sense
# Requirement 5.6 means.
_WHITESPACE_CHARS = " \t\n\r\f\v"


def _normalizes_to_empty(text: str) -> bool:
    """True iff ``text`` is empty after the scorer's normalization.

    Mirrors :func:`matchlayer_api.scoring.scorer._normalize` (case-fold +
    whitespace-collapse) so the test's "empty after normalization" precondition
    is stated against the exact rule the scorer applies, not a private import.
    """
    return " ".join(text.casefold().split()) == ""


# Empty-or-whitespace-only text: the input space Requirement 5.6 governs. Every
# value normalizes to "" (asserted as a precondition in each test).
_blank_text = st.text(alphabet=_WHITESPACE_CHARS, min_size=0, max_size=24)

# Real surface forms the lexicon knows: every canonical term plus all aliases.
# Seeding the *arbitrary* (non-empty) side with these makes the JD's analyzed
# set frequently populated, so the empty-resume case genuinely exercises the
# "0 / |analyzed|" coverage branch rather than the trivial empty-analyzed one.
_LEXICON_SURFACES: list[str] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic, non-stop-word filler. scikit-learn's TfidfVectorizer strips English
# stop words, so this pool ensures a filler-only document still yields TF-IDF
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

# An arbitrary document built from tokens joined by single spaces, unioned with
# genuinely arbitrary unicode text. ``min_size=1`` on the token list keeps this
# side typically non-empty, but an arbitrary value that happens to normalize to
# "" simply degenerates into a both-empty case (still score 0), so no example is
# ever invalid.
_arbitrary_document = st.one_of(
    st.lists(_token, min_size=1, max_size=40).map(" ".join),
    st.text(min_size=1, max_size=200),
)


def _assert_zero_without_error(result: ScoreResult) -> None:
    """Assert the empty-input contract on a produced :class:`ScoreResult`.

    Factored out so all three configurations check exactly the same clauses of
    Property 4 (Requirement 5.6): a final ``score`` of ``0`` and both breakdown
    components equal to ``0``. The mere fact that ``result`` exists means
    ``Match_Scorer.score`` returned rather than raised — the "without error"
    half of the property.
    """
    assert result.score == 0
    assert result.breakdown.final_score == 0
    assert result.breakdown.similarity_component == 0.0
    assert result.breakdown.keyword_coverage_component == 0.0


@pytest.mark.parametrize("weights", _WEIGHT_PAIRS)
@settings(max_examples=200, deadline=None)
@given(resume_text=_blank_text, job_description=_arbitrary_document)
@example(resume_text="", job_description="python developer")
@example(resume_text="   ", job_description="python java sql kubernetes")
@example(resume_text="\t\n", job_description="senior backend engineer with cloud experience")
def test_empty_resume_with_arbitrary_jd_scores_zero(
    weights: tuple[float, float],
    resume_text: str,
    job_description: str,
) -> None:
    """An empty/whitespace-only resume scores 0 against any JD (Requirement 5.6).

    The analyzed set comes from the (arbitrary) JD and may be non-empty, but no
    term can be present in an empty resume, so ``matched`` is empty and coverage
    is ``0``; the similarity half is ``0`` because the resume document is empty.
    The blended score is therefore ``0`` under any weight pair, and the call
    does not raise.
    """
    # Precondition: the resume is empty after the scorer's normalization.
    assert _normalizes_to_empty(resume_text)

    result = _SCORERS[weights].score(resume_text, job_description)

    _assert_zero_without_error(result)


@pytest.mark.parametrize("weights", _WEIGHT_PAIRS)
@settings(max_examples=200, deadline=None)
@given(resume_text=_arbitrary_document, job_description=_blank_text)
@example(resume_text="python developer", job_description="")
@example(resume_text="python java sql kubernetes", job_description="   ")
@example(resume_text="senior backend engineer with cloud experience", job_description="\n\t ")
def test_arbitrary_resume_with_empty_jd_scores_zero(
    weights: tuple[float, float],
    resume_text: str,
    job_description: str,
) -> None:
    """An empty/whitespace-only JD scores 0 against any resume (Requirement 5.6).

    With an empty JD the analyzed keyword set is empty, so coverage is defined as
    ``0``; the similarity half is ``0`` because the JD document is empty. The
    blended score is therefore ``0`` under any weight pair, and the call does not
    raise.
    """
    # Precondition: the JD is empty after the scorer's normalization.
    assert _normalizes_to_empty(job_description)

    result = _SCORERS[weights].score(resume_text, job_description)

    _assert_zero_without_error(result)


@pytest.mark.parametrize("weights", _WEIGHT_PAIRS)
@settings(max_examples=200, deadline=None)
@given(resume_text=_blank_text, job_description=_blank_text)
@example(resume_text="", job_description="")
@example(resume_text="   ", job_description="\t\n")
@example(resume_text="\n\n\t", job_description="  \r ")
def test_both_empty_scores_zero(
    weights: tuple[float, float],
    resume_text: str,
    job_description: str,
) -> None:
    """Two empty/whitespace-only texts score 0 (Requirement 5.6).

    Both halves are ``0`` (empty similarity documents and an empty analyzed
    set), so the blended score is ``0`` under any weight pair and the call does
    not raise.
    """
    # Precondition: both sides are empty after the scorer's normalization.
    assert _normalizes_to_empty(resume_text)
    assert _normalizes_to_empty(job_description)

    result = _SCORERS[weights].score(resume_text, job_description)

    _assert_zero_without_error(result)
