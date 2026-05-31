"""Feature: phase-1-matching — Property 1.

Property 1: Score is always a bounded integer.

    *For any* resume text and *any* job-description text,
    ``Match_Scorer.score`` returns a ``score`` that is an integer in the
    inclusive range ``0..100``.

**Validates: Requirements 5.1, 5.3**

This is the universal companion to the eyeball-example coverage of the
``Match_Scorer``. Where unit examples pin down specific scores for hand-picked
resume/JD pairs, this module asserts the *boundedness* invariant holds across a
wide, generated input space using Hypothesis (>=100 examples), driving
:class:`Match_Scorer` directly (framework-free: only the scoring core and its
committed lexicon are touched — no FastAPI, DB, storage, or settings access).

The score is the convex blend ``round(100 * (w_similarity * sim + w_keyword *
coverage))`` clamped to ``[0, 100]`` (Requirement 5.3), where ``sim`` is a TF-IDF
cosine similarity in ``[0, 1]`` and ``coverage`` is a matched/analyzed fraction
in ``[0, 1]`` (Requirement 5.1, 5.2). The property therefore asserts three
things about the returned ``score``, phrased so they hold for *every* input pair
and can never produce a false failure:

* **Integer type.** ``score`` is a genuine :class:`int` (and not a ``bool``,
  which is an ``int`` subclass) — the result of ``round`` + ``min``/``max``,
  never a float, string, or other type.
* **Lower bound.** ``score >= 0`` — the clamp floor guards against a degenerate
  weighting driving the blend below zero.
* **Upper bound.** ``score <= 100`` — the clamp ceiling guards against
  floating-point drift pushing a perfect match a hair above 100.

To keep the property anchored to Requirement 5.3's "weights sum to 1.0"
contract (enforced by the ``Settings`` validator), the scorer is exercised under
several valid weight pairs that each sum to ``1.0`` — the production default
(0.6/0.4), an even split, and the two degenerate single-component ends (1.0/0.0
and 0.0/1.0) that isolate the similarity-only and coverage-only blends. The
boundedness guarantee must hold under every such configuration.

Inputs are arbitrary text drawn from a deliberately mixed vocabulary — real
``Skill_Lexicon`` surface forms (canonical terms and aliases), generic
non-stop-word filler so scikit-learn's TF-IDF yields a non-empty vocabulary,
and free random tokens — joined into resume and job-description documents. The
shared vocabulary makes the resume frequently cover some JD terms, so the blend
is exercised across the full ``[0, 1]`` range of both components rather than
always landing at one extreme. The empty / whitespace-only pair (which must
score ``0`` per Requirement 5.6) is included by construction and satisfies the
bound trivially.
"""

from __future__ import annotations

from string import ascii_lowercase, digits

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer

# ---------------------------------------------------------------------------
# Scorers under test
# ---------------------------------------------------------------------------

# The committed runtime lexicon — the real vocabulary the scorer matches
# against. Loaded once; instances are immutable and safe to share across
# examples and across the several weight-pair scorers below.
_LEXICON: Skill_Lexicon = load_lexicon()

# Caps mirror the production defaults (``MATCHLAYER_MATCH_MAX_KEYWORDS`` = 50,
# ``MATCHLAYER_MATCH_MAX_SUGGESTIONS`` = 10). The boundedness invariant is
# independent of the caps' values, but the defaults keep the analyzed set and
# suggestions realistically sized.
_MAX_KEYWORDS = 50
_MAX_SUGGESTIONS = 10

# Valid weight pairs, each summing to 1.0 as the ``Settings`` validator
# enforces (Requirement 5.3): the production default, an even split, and the
# two single-component extremes. Boundedness must hold under every one.
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
# Vocabulary for generated text
# ---------------------------------------------------------------------------

# Real surface forms the lexicon knows: every canonical term plus all of its
# aliases. Feeding these into the generated documents makes resume/JD overlap
# (driving coverage up) and the TF-IDF cosine both occur across the example
# space, so the blend is exercised across its full range rather than pinned at
# an extreme.
_LEXICON_SURFACES: list[str] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic, non-stop-word filler. scikit-learn's TfidfVectorizer strips English
# stop words, so this pool ensures a document made of filler still yields TF-IDF
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

# A single generated token: a known lexicon surface form, a generic filler
# word, or a free random alphanumeric token (which may coincide with neither).
_token = st.one_of(
    st.sampled_from(_LEXICON_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# A document built from tokens joined by single spaces. ``min_size=0`` admits
# the empty document, exercising the empty-input branch (Requirement 5.6),
# which must still score within bounds (indeed, 0).
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Also throw genuinely arbitrary text (unicode, punctuation, odd whitespace)
# at the scorer so the boundedness claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)


def _assert_score_is_bounded_integer(score: object) -> None:
    """Assert ``score`` is a genuine integer in the inclusive range 0..100.

    Factored out so every generated and explicit example checks exactly the
    same contract (Property 1, Requirements 5.1, 5.3).
    """
    # A genuine ``int`` — never a float, string, or other type, and not a
    # ``bool`` (which is an ``int`` subclass and would slip past a bare
    # ``isinstance(score, int)``).
    assert isinstance(score, int)
    assert not isinstance(score, bool)

    # Inclusive 0..100 (Requirement 5.3): the clamp floor and ceiling.
    assert 0 <= score <= 100


@pytest.mark.parametrize("weights", _WEIGHT_PAIRS)
@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
@example(resume_text="", job_description="")
@example(resume_text="python developer", job_description="python developer")
@example(resume_text="", job_description="python developer")
@example(resume_text="totally unrelated prose", job_description="python java sql kubernetes")
def test_score_is_a_bounded_integer(
    weights: tuple[float, float],
    resume_text: str,
    job_description: str,
) -> None:
    """``Match_Scorer.score`` returns an integer in ``[0, 100]`` for any input.

    Property 1 (Requirements 5.1, 5.3): for arbitrary resume and job-description
    text, and under any valid weight pair that sums to 1.0, the returned
    ``score`` is a genuine integer bounded inclusively between 0 and 100.
    """
    scorer = _SCORERS[weights]

    result = scorer.score(resume_text, job_description)

    _assert_score_is_bounded_integer(result.score)
