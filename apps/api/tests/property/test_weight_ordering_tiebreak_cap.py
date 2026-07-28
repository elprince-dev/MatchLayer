"""Feature: phase-2-nlp-embeddings — Property 11.

Property 11: Skill sets are weight-ordered, tie-broken lexicographically,
and capped.

    *For any* input text and configured ``max_keywords``, the analyzed,
    matched, and missing sets are ordered by descending lexicon weight with
    equal-weight ties broken by ascending lexicographic canonical term, and
    the analyzed set contains at most ``max_keywords`` terms consisting of
    the highest-weighted candidates.

**Validates: Requirements 4.5**

Requirement 4.5 says the Skill_Extractor orders the analyzed, matched, and
missing sets by descending skill weight from the Skill_Lexicon, breaking
equal-weight ties by ascending lexicographic order of the canonical term,
and caps the analyzed set at ``max_keywords`` terms by retaining the
highest-weighted terms — preserving the Phase 1 ordering and cap contracts.
This module asserts all of that across a generated input space:

* **Total order everywhere.** For arbitrary documents, ``extract(text)``
  and every ``analyze()`` output set (``analyzed`` / ``matched`` /
  ``missing``) are sorted by the strict key ``(-weight, canonical)``. The
  synthetic lexicon below packs multiple entries at each weight level, so
  the lexicographic tie-break is exercised constantly, not incidentally.
* **Weights are the lexicon's.** Every emitted ``Keyword.weight`` equals
  the lexicon weight of its canonical term, grounding "descending *lexicon*
  weight" in the artifact rather than in whatever number the extractor
  chose to emit.
* **Cap retains the highest-weighted candidates.** ``analyzed`` has at most
  ``max_keywords`` terms and equals the ``max_keywords``-prefix of the
  *uncapped* ordered extraction of the same job description — so the terms
  that survive the cap are exactly the highest-weighted ones, with the
  lexicographic tie-break deciding among equal-weight terms at the cut
  line. A cap of 0 (the defensive floor) yields an empty analyzed set.

Generated documents mix lexicon surface forms (canonical terms *and*
aliases, including multi-token and punctuation-bearing forms), generic
job-posting words, and free random tokens — plus genuinely arbitrary
unicode text — so the ordering claim is not limited to tidy skill-bearing
input.

Per the design's PBT strategy, extractors are built once at module scope
over a small synthetic lexicon (constructing a spaCy ``PhraseMatcher`` per
Hypothesis example would dominate the runtime budget) and the pipeline is
``spacy.blank("en")`` — tokenizer-only, no model artifact. A blank pipeline
assigns no POS, so the POS gate passes candidates through and the ordering
and cap logic — the behavior under test — is exercised in isolation.

The Skill_Extractor is framework-free (Requirement 12.1): this test
constructs it directly from a synthetic lexicon document and injected
``max_keywords`` caps, never touching settings, FastAPI, or the database.
"""

from __future__ import annotations

from string import ascii_lowercase, digits
from typing import Final

import spacy
from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.keyword_analyzer import Keyword
from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Synthetic lexicon and module-scoped extractors
# ---------------------------------------------------------------------------

# Deliberately tie-heavy: two entries at weight 1.0, three at 0.9, three at
# 0.8, and two at 0.7, so almost every non-trivial extraction forces the
# lexicographic tie-break — including at the cap cut line. The set also
# covers single-token aliases ("py", "js", "k8s"), multi-token surface forms
# ("node js", "machine learning"), and punctuation-bearing canonicals
# ("node.js", "ci/cd", "c++").
_LEXICON: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop11",
        "skills": [
            {
                "canonical": "python",
                "display": "Python",
                "category": "language",
                "weight": 1.0,
                "aliases": ["py", "py3"],
            },
            {
                "canonical": "rust",
                "display": "Rust",
                "category": "language",
                "weight": 1.0,
                "aliases": [],
            },
            {
                "canonical": "javascript",
                "display": "JavaScript",
                "category": "language",
                "weight": 0.9,
                "aliases": ["js"],
            },
            {
                "canonical": "kubernetes",
                "display": "Kubernetes",
                "category": "platform",
                "weight": 0.9,
                "aliases": ["k8s"],
            },
            {
                "canonical": "node.js",
                "display": "Node.js",
                "category": "runtime",
                "weight": 0.9,
                "aliases": ["node js", "nodejs"],
            },
            {
                "canonical": "c++",
                "display": "C++",
                "category": "language",
                "weight": 0.8,
                "aliases": ["cpp"],
            },
            {
                "canonical": "ci/cd",
                "display": "CI/CD",
                "category": "practice",
                "weight": 0.8,
                "aliases": ["cicd"],
            },
            {
                "canonical": "sql",
                "display": "SQL",
                "category": "language",
                "weight": 0.8,
                "aliases": [],
            },
            {
                "canonical": "go",
                "display": "Go",
                "category": "language",
                "weight": 0.7,
                "aliases": ["golang"],
            },
            {
                "canonical": "machine learning",
                "display": "Machine Learning",
                "category": "domain",
                "weight": 0.7,
                "aliases": ["ml"],
            },
        ],
    }
)

_WEIGHT_BY_TERM: Final[dict[str, float]] = {
    entry.canonical: entry.weight for entry in _LEXICON.entries
}

# Built once at module scope; instances hold only immutable state (pipeline,
# lexicon, prebuilt matcher) and are safe to share across examples. Caps
# chosen to exercise: no effective cap (50 > lexicon size), a cut line in
# the middle of the 0.9 tie group (3), the tightest non-trivial cap (1),
# and the defensive floor (0 → empty analyzed set).
_CAPS: Final[tuple[int, ...]] = (50, 3, 1, 0)
_EXTRACTORS: Final[dict[int, Skill_Extractor]] = {
    cap: Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=cap) for cap in _CAPS
}

# The uncapped reference extractor: its extract() over the same lexicon is
# the full ordered candidate list the cap must take a prefix of.
_UNCAPPED: Final[Skill_Extractor] = _EXTRACTORS[50]

_cap = st.sampled_from(_CAPS)

# ---------------------------------------------------------------------------
# Vocabulary for generated text
# ---------------------------------------------------------------------------

_SURFACES: Final[list[str]] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic job-posting words that are not skills in the lexicon above.
_GENERIC_WORDS: Final[list[str]] = [
    "check",
    "selection",
    "experience",
    "team",
    "developer",
    "required",
    "preferred",
    "work",
    "process",
    "strong",
    "years",
    "communication",
    "responsibilities",
    "candidate",
]

# A single generated token: a lexicon surface form, a generic job-posting
# word, or a free random alphanumeric token (which may coincide with a
# surface form like "go" or "js"; that only makes the mix richer — the
# assertions are on the *output* ordering, not the input).
_token = st.one_of(
    st.sampled_from(_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# A document of tokens joined by single spaces; min_size=0 admits the empty
# document (empty extraction, the ordering holds vacuously).
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Genuinely arbitrary text (unicode, punctuation, odd whitespace) so the
# claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)

# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------


def _assert_weight_ordered(keywords: list[Keyword], label: str) -> None:
    """``keywords`` is sorted by descending weight, lexicographic tie-break.

    The sort key ``(-weight, term)`` is a strict total order over distinct
    canonical terms, so equality with its own sorted image pins the exact
    required ordering (Requirement 4.5) — not merely a weakly descending
    weight sequence.
    """
    keys = [(-keyword.weight, keyword.term) for keyword in keywords]
    assert keys == sorted(keys), (f"{label} set is not weight-ordered with lex tie-break", keys)


def _assert_lexicon_weights(keywords: list[Keyword], label: str) -> None:
    """Every emitted weight is the lexicon weight of its canonical term."""
    for keyword in keywords:
        assert keyword.weight == _WEIGHT_BY_TERM[keyword.term], (
            f"{label} keyword weight differs from the lexicon weight",
            keyword,
        )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(text=_document)
@example(text="")
@example(
    # Every 0.9-weight skill (via aliases) plus both 1.0 skills, in an order
    # that disagrees with the required output order on both weight and
    # lexicographic axes.
    text="nodejs then k8s then js then rust then py"
)
@example(
    # The full lexicon through mixed surface forms: all four weight levels
    # and every tie group at once.
    text="ml golang sql cicd cpp node js kubernetes javascript rust python"
)
def test_extract_is_weight_ordered_with_lexicographic_tiebreak(text: str) -> None:
    """``extract(text)`` is ordered by ``(-weight, term)`` (Req 4.5).

    For arbitrary input text, the extracted skills are sorted by descending
    lexicon weight with equal-weight ties broken by ascending lexicographic
    canonical term, and every emitted weight is the lexicon's weight for
    that term.
    """
    extracted = _UNCAPPED.extract(text)
    _assert_weight_ordered(extracted, "extract")
    _assert_lexicon_weights(extracted, "extract")


@settings(max_examples=200, deadline=None)
@given(cap=_cap, resume_text=_document, job_description=_document)
@example(cap=50, resume_text="", job_description="")
@example(
    # Cap 3 cuts inside the 0.9 tie group: analyzed must be python, rust,
    # then the lexicographically first 0.9 skill present ("javascript"),
    # dropping kubernetes and node.js despite equal weight.
    cap=3,
    resume_text="rust and javascript daily",
    job_description="nodejs k8s js rust py required",
)
@example(
    # Cap 1 keeps only the single highest-(weight, term) skill.
    cap=1,
    resume_text="python",
    job_description="python rust sql",
)
@example(
    # Cap 0 (the defensive floor) empties the analyzed set entirely.
    cap=0,
    resume_text="python",
    job_description="python rust sql",
)
def test_analyze_sets_are_ordered_and_analyzed_is_capped(
    cap: int, resume_text: str, job_description: str
) -> None:
    """``analyze()`` orders all three sets and caps ``analyzed`` (Req 4.5).

    For arbitrary resume and job-description texts and any configured cap,
    the analyzed, matched, and missing sets each carry the ``(-weight,
    term)`` ordering; ``analyzed`` contains at most ``max_keywords`` terms;
    and ``analyzed`` equals the ``max_keywords``-prefix of the uncapped
    ordered extraction of the same job description — the highest-weighted
    candidates survive the cap, with the lexicographic tie-break deciding
    among equal-weight terms at the cut line.
    """
    analysis = _EXTRACTORS[cap].analyze(resume_text, job_description)

    for label, keywords in (
        ("analyzed", analysis.analyzed),
        ("matched", analysis.matched),
        ("missing", analysis.missing),
    ):
        _assert_weight_ordered(keywords, label)
        _assert_lexicon_weights(keywords, label)

    assert len(analysis.analyzed) <= cap, (
        "analyzed exceeds the configured cap",
        cap,
        analysis.analyzed,
    )
    assert analysis.analyzed == _UNCAPPED.extract(job_description)[:cap], (
        "analyzed is not the highest-weighted prefix of the ordered extraction",
        cap,
        analysis.analyzed,
    )
