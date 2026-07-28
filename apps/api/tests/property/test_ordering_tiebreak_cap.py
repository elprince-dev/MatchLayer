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
highest-weighted terms. This module asserts all of that across a generated
input space:

* **Total order everywhere.** ``extract(text)`` and all three ``analyze()``
  output lists are sorted by the key ``(-weight, canonical)``. Because the
  tie-break is deterministic, this is a *total* order — asserting the list
  equals its own sort under that key rules out any pair out of order,
  including within equal-weight groups.
* **Cap size.** ``len(analyzed) <= max_keywords`` for every configured cap,
  including a cap of 0 (analyzed is empty) and a cap larger than the
  lexicon (nothing is dropped).
* **Cap retention.** The analyzed set is exactly the first ``max_keywords``
  elements of the *uncapped* ordered extraction of the job description —
  the prefix of a weight-descending order is precisely "the highest-weighted
  terms" (and, via the total order, this equality is stronger than any
  set-level "top weights" claim: it also pins the tie-break at the cap
  boundary).

The synthetic lexicon deliberately stacks several entries on *equal*
weights (three groups of ties, exercised through canonicals and aliases
alike) so the lexicographic tie-break is hit constantly rather than by
luck, and generated documents mix lexicon surface forms, generic
job-posting words, free random tokens, and genuinely arbitrary unicode
text so the ordering claim is not limited to tidy skill-bearing input.
Module-scoped extractor variants with caps 0, 1, 3, and 50 exercise the
empty cap, a cap forcing a tie-break cut inside an equal-weight group, a
mid-size cap, and the effectively-uncapped shape.

Per the design's PBT strategy, extractors are built once at module scope
over a small synthetic lexicon (constructing a spaCy ``PhraseMatcher`` per
Hypothesis example would dominate the runtime budget) and the pipeline is
``spacy.blank("en")`` — tokenizer-only, no model artifact. A blank pipeline
assigns no POS, so the POS gate passes candidates through and the ordering
and cap logic — the behavior under test — is exercised in isolation.

The Skill_Extractor is framework-free (Requirement 12.1): this test
constructs it directly from a synthetic lexicon document and an injected
``max_keywords`` cap, never touching settings, FastAPI, or the database.
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

# Deliberate equal-weight groups so the lexicographic tie-break is exercised
# constantly: 1.0 {python, rust}, 0.9 {javascript, kotlin, node.js}, and
# 0.8 {c++, ci/cd, docker}. The set also covers single-token aliases ("py",
# "js"), multi-token surface forms ("node js", "machine learning"), and
# punctuation-bearing canonicals ("node.js", "ci/cd", "c++") so ordering is
# asserted across the full matching surface.
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
                "canonical": "kotlin",
                "display": "Kotlin",
                "category": "language",
                "weight": 0.9,
                "aliases": ["kt"],
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
                "canonical": "docker",
                "display": "Docker",
                "category": "tool",
                "weight": 0.8,
                "aliases": [],
            },
            {
                "canonical": "machine learning",
                "display": "Machine Learning",
                "category": "domain",
                "weight": 0.7,
                "aliases": ["ml"],
            },
            {
                "canonical": "go",
                "display": "Go",
                "category": "language",
                "weight": 0.6,
                "aliases": ["golang"],
            },
        ],
    }
)

# Built once at module scope; instances hold only immutable state (pipeline,
# lexicon, prebuilt matcher) and are safe to share across examples. The caps
# exercise: 0 (analyzed always empty), 1 (a tie-break cut inside the 1.0
# group whenever both python and rust are extracted), 3 (a cut inside the
# 0.9 group), and 50 (exceeds the lexicon size — effectively uncapped).
_CAPS: Final[tuple[int, ...]] = (0, 1, 3, 50)
_EXTRACTORS: Final[tuple[Skill_Extractor, ...]] = tuple(
    Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=cap) for cap in _CAPS
)

_extractor_index = st.integers(min_value=0, max_value=len(_EXTRACTORS) - 1)

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
# Shared assertion
# ---------------------------------------------------------------------------


def _assert_weight_ordered(keywords: list[Keyword]) -> None:
    """``keywords`` is sorted by descending weight, ties ascending term (Req 4.5).

    ``(-weight, term)`` is a *total* order (the lexicographic tie-break is
    deterministic), so equality with the sorted key sequence rules out any
    adjacent — and hence any — pair out of order, including within
    equal-weight groups.
    """
    keys = [(-keyword.weight, keyword.term) for keyword in keywords]
    assert keys == sorted(keys), ("skill set is not weight-ordered with lex tie-break", keywords)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(text=_document)
@example(text="")
@example(
    # All three equal-weight groups hit at once, in scrambled surface order
    # and through aliases: the output must come back as
    # python, rust | javascript, kotlin, node.js | c++, ci/cd, docker | ...
    text="docker golang rust cicd nodejs kt cpp js py machine learning",
)
def test_extract_is_weight_ordered_with_lexicographic_tiebreak(text: str) -> None:
    """extract(text) is ordered by (-weight, canonical) (Req 4.5).

    For any input text, the extracted skill list is sorted by descending
    lexicon weight with equal-weight ties broken by ascending lexicographic
    canonical term.
    """
    # The uncapped-shape extractor; extract() itself never caps, so any
    # variant would do — the cap only applies inside analyze().
    _assert_weight_ordered(_EXTRACTORS[-1].extract(text))


@settings(max_examples=200, deadline=None)
@given(extractor_index=_extractor_index, resume_text=_document, job_description=_document)
@example(
    # cap=1 forces a tie-break cut inside the weight-1.0 group: both python
    # and rust are extracted, and analyzed must keep exactly ["python"].
    extractor_index=1,
    resume_text="rust developer",
    job_description="rust and python required",
)
@example(
    # cap=3 cuts inside the 0.9 tie group {javascript, kotlin, node.js}:
    # analyzed must be [python, rust, javascript] — the lexicographically
    # first of the tied trio survives the cap.
    extractor_index=2,
    resume_text="py kotlin docker",
    job_description="nodejs kotlin javascript rust python docker",
)
@example(
    # cap=0: analyzed is always empty regardless of the JD's skills.
    extractor_index=0,
    resume_text="python",
    job_description="python rust javascript",
)
@example(
    # cap=50 exceeds the lexicon: nothing is dropped, full ordered set.
    extractor_index=3,
    resume_text="cicd machine learning golang",
    job_description="docker golang rust cicd nodejs kt cpp js py machine learning",
)
def test_analyze_sets_are_ordered_and_analyzed_is_capped(
    extractor_index: int, resume_text: str, job_description: str
) -> None:
    """analyzed/matched/missing are ordered; analyzed is capped (Req 4.5).

    For any resume and job-description texts and any configured cap, all
    three ``analyze()`` output lists follow the descending-weight,
    ascending-lexicographic order; ``analyzed`` holds at most
    ``max_keywords`` terms; and ``analyzed`` equals the first
    ``max_keywords`` elements of the uncapped ordered extraction of the job
    description — the highest-weighted candidates, with the tie-break
    pinning exactly which equal-weight terms survive the cap boundary.
    """
    extractor = _EXTRACTORS[extractor_index]
    cap = _CAPS[extractor_index]

    analysis = extractor.analyze(resume_text, job_description)

    _assert_weight_ordered(analysis.analyzed)
    _assert_weight_ordered(analysis.matched)
    _assert_weight_ordered(analysis.missing)

    assert len(analysis.analyzed) <= cap, (
        "analyzed exceeds max_keywords",
        len(analysis.analyzed),
        cap,
    )

    # Cap retention: the analyzed set is exactly the cap-length prefix of
    # the uncapped ordered extraction. Because extract() orders by
    # descending weight (total order via the tie-break), the prefix *is*
    # "the highest-weighted terms" of Requirement 4.5 — and the list
    # equality also fixes the tie-break at the cut point.
    assert analysis.analyzed == extractor.extract(job_description)[:cap], (
        "analyzed is not the highest-weighted prefix of the uncapped extraction",
        analysis.analyzed,
    )
