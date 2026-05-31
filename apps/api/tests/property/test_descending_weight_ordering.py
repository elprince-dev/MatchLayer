"""Feature: phase-1-matching — Property 9.

Property 9: Keyword and suggestion lists are ordered by descending weight.

    *For any* resume text and *any* job-description text, ``matched_keywords``
    and ``missing_keywords`` are each ordered by non-increasing keyword weight,
    and ``suggestions`` are ordered by non-increasing weight of the missing
    keyword each addresses.

**Validates: Requirements 6.6, 7.2**

This module is the universal companion to the concrete-example coverage of the
``Match_Scorer``. Where unit examples pin specific orderings for hand-picked
resume/JD pairs, this file asserts the *descending-weight ordering* invariant
holds across a wide, generated input space using Hypothesis (>=100 examples),
driving :class:`Match_Scorer` directly (framework-free: only the scoring core
and its committed lexicon are touched — no FastAPI, DB, storage, or settings
access).

The :class:`Match_Scorer` is constructed from the committed
:class:`Skill_Lexicon` and the production-default weights/caps
(``MATCHLAYER_SCORE_WEIGHT_*`` = 0.6/0.4, ``MATCHLAYER_MATCH_MAX_KEYWORDS`` =
50, ``MATCHLAYER_MATCH_MAX_SUGGESTIONS`` = 10). The ordering invariant is
independent of the weight values, but the defaults keep the analyzed set and
the suggestion list realistically sized so the cap is genuinely exercised.

Three complementary assertions encode the property:

* **``matched_keywords`` is non-increasing in weight (Requirement 6.6).** Each
  adjacent pair satisfies ``weight[i] >= weight[i + 1]`` — the matched list is
  carved out of the analyzer's already-sorted analyzed set, so it must stay
  ordered.

* **``missing_keywords`` is non-increasing in weight (Requirement 6.6).** Same
  pairwise check for the missing partition.

* **``suggestions`` correspond, in order, to the missing keywords by
  descending weight (Requirement 7.2).** On the non-empty-missing path the
  suggestion keywords are exactly the leading prefix of the missing terms
  (truncated to the ``max_suggestions`` cap), and the weight of the missing
  keyword each suggestion addresses is itself non-increasing. On the
  empty-missing path the generator emits exactly one affirmative suggestion
  whose ``keyword`` is the empty string (Requirement 7.3's affirmative case),
  which the ordering claim satisfies trivially.

Inputs are arbitrary text drawn from a deliberately mixed vocabulary — real
``Skill_Lexicon`` surface forms (canonical terms and aliases), generic
non-stop-word filler so scikit-learn's TF-IDF yields a non-empty vocabulary,
and free random tokens — joined into resume and job-description documents. The
shared vocabulary makes the matched/missing split frequently populated on both
sides (so both ordered lists are non-trivial), while the explicit examples pin
the empty-input, perfect-overlap (empty missing → affirmative), and
keyword-stuffed adversarial corners.
"""

from __future__ import annotations

from itertools import pairwise
from string import ascii_lowercase, digits

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.keyword_analyzer import Keyword
from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer
from matchlayer_api.scoring.suggestions import Suggestion

# ---------------------------------------------------------------------------
# Scorer under test
# ---------------------------------------------------------------------------

# The committed runtime lexicon — the real vocabulary the scorer matches
# against. Loaded once; instances are immutable and safe to share across every
# generated example.
_LEXICON: Skill_Lexicon = load_lexicon()

# Configured weights and caps mirror the production defaults. The ordering
# invariant is independent of the weight values, but ``MAX_SUGGESTIONS`` = 10
# (smaller than ``MAX_KEYWORDS`` = 50) means a richly-missing JD genuinely
# exercises the suggestion cap, so the prefix-correspondence assertion below is
# not vacuous.
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

# ---------------------------------------------------------------------------
# Vocabulary for generated text
# ---------------------------------------------------------------------------

# Real surface forms the lexicon knows: every canonical term plus all of its
# aliases. Feeding these into the generated documents makes resume/JD overlap
# (populating ``matched``) and lexicon-weighted ``missing`` terms both occur
# across the example space, so the two ordered lists are frequently non-trivial.
_LEXICON_SURFACES: list[str] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic, non-stop-word filler. scikit-learn's TfidfVectorizer strips English
# stop words, so this pool ensures a document made of filler still yields TF-IDF
# terms (with their own weights) rather than an empty vocabulary.
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
# the empty document, exercising the empty-input branch (which yields the
# affirmative suggestion).
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Also throw genuinely arbitrary text (unicode, punctuation, odd whitespace) at
# the scorer so the ordering claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)


def _assert_non_increasing_weight(keywords: list[Keyword]) -> None:
    """Assert ``keywords`` is ordered by non-increasing weight (Requirement 6.6).

    A pairwise ``weight[i] >= weight[i + 1]`` check over adjacent elements.
    Weights are finite (curated lexicon weights or non-negative TF-IDF scores —
    never NaN/inf), so the ``>=`` comparison is total and the check can never
    raise a false failure.
    """
    weights = [kw.weight for kw in keywords]
    for earlier, later in pairwise(weights):
        assert earlier >= later, f"weights not non-increasing: {weights!r}"


@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
@example(resume_text="", job_description="")
@example(resume_text="python developer", job_description="python java sql kubernetes docker")
@example(
    resume_text="python java sql docker kubernetes react aws",
    job_description="python java sql docker kubernetes react aws",
)
@example(
    resume_text="totally unrelated prose about gardening and cooking",
    job_description="python python python java java sql sql kubernetes docker aws react node",
)
def test_keyword_and_suggestion_lists_are_descending_by_weight(
    resume_text: str, job_description: str
) -> None:
    """Matched/missing lists and suggestions are ordered by descending weight.

    Property 9 (Requirements 6.6, 7.2): for arbitrary resume and job-description
    text, ``matched_keywords`` and ``missing_keywords`` are each non-increasing
    in weight, and ``suggestions`` correspond — in order — to the missing
    keywords by descending weight (truncated to ``max_suggestions``), with the
    empty-missing case yielding exactly one affirmative suggestion.
    """
    result = _SCORER.score(resume_text, job_description)

    # (a) + (b) Both partition lists are ordered by non-increasing weight.
    _assert_non_increasing_weight(result.matched_keywords)
    _assert_non_increasing_weight(result.missing_keywords)

    suggestions = result.suggestions

    if not result.missing_keywords:
        # Empty-missing path: exactly one affirmative suggestion addressing no
        # specific missing keyword (Requirement 7.3). The descending-weight
        # ordering claim holds trivially for a single-element list.
        assert len(suggestions) == 1
        assert isinstance(suggestions[0], Suggestion)
        assert suggestions[0].keyword == ""
        return

    # Non-empty-missing path. The suggestion keywords are exactly the leading
    # prefix of the missing terms (already ordered by descending weight),
    # truncated to the configured cap (Requirement 7.2): one suggestion per
    # missing keyword in the same order, capped at ``max_suggestions``.
    missing_terms = [kw.term for kw in result.missing_keywords]
    suggestion_keywords = [s.keyword for s in suggestions]
    assert suggestion_keywords == missing_terms[:_MAX_SUGGESTIONS]

    # And the weight of the missing keyword each suggestion addresses is itself
    # non-increasing — the literal Property 9 statement for suggestions. Missing
    # terms are unique (the analyzed set is keyed by canonical term), so the
    # term→weight lookup is unambiguous.
    missing_weight = {kw.term: kw.weight for kw in result.missing_keywords}
    addressed_weights = [missing_weight[s.keyword] for s in suggestions]
    for earlier, later in pairwise(addressed_weights):
        assert earlier >= later, f"suggestion weights not non-increasing: {addressed_weights!r}"
