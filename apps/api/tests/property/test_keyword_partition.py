"""Feature: phase-1-matching — Property 6.

Property 6: Matched and missing partition the analyzed set.

    *For any* resume text and *any* job-description text, ``matched_keywords``
    and ``missing_keywords`` are disjoint and their union equals the analyzed
    keyword set.

**Validates: Requirements 6.3, 6.4**

This is the universal companion to the concrete-example coverage of the
Keyword_Analyzer. Where unit examples pin down specific matched/missing
verdicts for hand-picked pairs, this module asserts the *partition* invariant
holds across a wide, generated input space using Hypothesis (>=100 examples).

The Keyword_Analyzer walks the analyzed list exactly once and routes each
``Keyword`` into ``matched`` (term present in the normalized resume) or
``missing`` (term absent). A genuine partition therefore demands three things,
all checked here against the ``(term, weight)`` identity of every analyzed
keyword:

* **Disjointness** — no term appears in both ``matched`` and ``missing``
  (Requirement 6.4).
* **Covering union** — the union of ``matched`` and ``missing`` is exactly the
  analyzed set, with nothing invented that was not analyzed (Requirements 6.3,
  6.4).
* **Conservation** — no analyzed keyword is lost or duplicated across the
  split: ``|matched| + |missing| == |analyzed|`` and each analyzed keyword
  lands in exactly one side, weight intact.

Inputs are arbitrary text drawn from a deliberately mixed vocabulary — real
Skill_Lexicon surface forms (canonical terms and aliases), generic
non-stop-word filler so scikit-learn's TF-IDF yields a non-empty vocabulary,
and free random tokens — joined into resume and job-description documents. The
shared vocabulary makes the resume frequently cover some JD terms, so the split
is exercised with both sides populated rather than always landing entirely on
one side. The empty / whitespace-only job description (which yields an empty
analysis) is included by construction and satisfies the partition trivially.

The Keyword_Analyzer is framework-free (Requirement 10.1): this test
constructs it directly from the committed Skill_Lexicon and an injected
``max_keywords`` cap, never touching settings, FastAPI, or the database.
"""

from __future__ import annotations

from collections import Counter
from string import ascii_lowercase, digits

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.keyword_analyzer import Keyword, Keyword_Analyzer
from matchlayer_api.scoring.lexicon import load_lexicon

# ---------------------------------------------------------------------------
# Analyzer under test
# ---------------------------------------------------------------------------

# One shared analyzer, built from the real committed lexicon. The cap mirrors
# the ``MATCHLAYER_MATCH_MAX_KEYWORDS`` default (50); the partition invariant
# is independent of the cap's value, but using the production default keeps the
# analyzed set realistically sized. The analyzer holds only immutable state
# (lexicon, cap, precompiled regexes) and is safe to reuse across examples.
_LEXICON = load_lexicon()
_MAX_KEYWORDS = 50
_ANALYZER = Keyword_Analyzer(_LEXICON, max_keywords=_MAX_KEYWORDS)

# ---------------------------------------------------------------------------
# Vocabulary for generated text
# ---------------------------------------------------------------------------

# Real surface forms the lexicon knows: every canonical term plus all of its
# aliases. Feeding these into the generated documents guarantees the analyzed
# set is frequently non-empty and that resume/JD overlap (matched terms) and
# divergence (missing terms) both occur across the example space.
_LEXICON_SURFACES: list[str] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic, non-stop-word filler. scikit-learn's TfidfVectorizer strips English
# stop words, so this pool ensures a JD made of filler still yields TF-IDF
# terms (source (b) of the analyzed set) rather than an empty vocabulary.
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
# the empty document, exercising the empty-analysis branch (Requirement 5.6's
# foundation), which must still satisfy the partition trivially.
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Also throw genuinely arbitrary text (unicode, punctuation, odd whitespace)
# at the analyzer so the partition claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)


def _term_weight_counter(keywords: list[Keyword]) -> Counter[tuple[str, float]]:
    """Multiset of ``(term, weight)`` identities for a keyword list.

    Using a :class:`~collections.Counter` over the full ``(term, weight)``
    identity (not just the term) lets the conservation assertion catch a
    keyword that was dropped, duplicated, or had its weight mutated as it was
    routed into ``matched`` / ``missing``.
    """
    return Counter((kw.term, kw.weight) for kw in keywords)


@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
def test_matched_and_missing_partition_the_analyzed_set(
    resume_text: str, job_description: str
) -> None:
    """matched and missing are a true partition of the analyzed set.

    Property 6 (Requirements 6.3, 6.4): for arbitrary resume and
    job-description text the two output lists are disjoint and together cover
    the analyzed set exactly — every analyzed keyword lands in precisely one
    side with its weight intact, and neither side invents a keyword that was
    not analyzed.
    """
    result = _ANALYZER.analyze(resume_text, job_description)

    matched_terms = {kw.term for kw in result.matched}
    missing_terms = {kw.term for kw in result.missing}
    analyzed_terms = {kw.term for kw in result.analyzed}

    # Disjointness (6.4): no term is reported as both matched and missing.
    assert matched_terms.isdisjoint(missing_terms)

    # Covering union (6.3, 6.4): the two sides together are exactly the
    # analyzed set — nothing missing, nothing fabricated.
    assert matched_terms | missing_terms == analyzed_terms

    # Conservation by full (term, weight) identity: every analyzed keyword
    # appears exactly once across the split, with no duplication, no loss, and
    # no weight mutation. This subsumes the set-level checks above and also
    # rules out a duplicate keyword hiding within a single side.
    analyzed_counts = _term_weight_counter(result.analyzed)
    split_counts = _term_weight_counter(result.matched) + _term_weight_counter(result.missing)
    assert split_counts == analyzed_counts

    # Cardinality follows from conservation but is asserted explicitly as a
    # blunt, fast tripwire: the partition can never change the element count.
    assert len(result.matched) + len(result.missing) == len(result.analyzed)
