"""Feature: phase-2-nlp-embeddings — Property 10.

Property 10: matched/missing partition the analyzed set.

    *For any* resume text and job-description text, ``matched_keywords``
    and ``missing_keywords`` are disjoint and their union equals the
    analyzed skill set; and *for any* resume text that is empty after
    normalization, ``matched_keywords`` is empty and ``missing_keywords``
    equals the analyzed set.

**Validates: Requirements 4.4**

Requirement 4.4 says the Skill_Extractor partitions the analyzed skill set
into ``matched_keywords`` (skills present in the resume, as determined by
running the Skill_Extractor against the resume text) and
``missing_keywords`` (skills absent from the resume); the two sets are
disjoint and their union equals the analyzed skill set; and an empty resume
yields an empty ``matched`` with ``missing`` equal to the analyzed set.
This module asserts all of that across a generated input space:

* **Exact partition.** For arbitrary resume and job-description documents,
  every analyzed keyword appears in exactly one of ``matched`` / ``missing``,
  and both output lists preserve the analyzed order and multiplicity: the
  subsequence of ``analyzed`` whose terms are matched *is* ``matched``, and
  the complementary subsequence *is* ``missing``. This is stronger than the
  set-level claim — it also rules out reordering, duplication, and
  fabricated keywords in either output.
* **Membership is resume extraction.** Every ``matched`` term is present in
  ``extract(resume_text)`` and every ``missing`` term is absent from it, so
  the partition criterion is exactly "the same extractor run against the
  resume text" (Requirement 4.4's determination clause).
* **Empty resume.** For any resume text that normalizes to nothing
  (whitespace-only, including the empty string), ``matched`` is empty and
  ``missing`` equals ``analyzed`` element-for-element.

Generated documents deliberately mix lexicon surface forms (canonical terms
*and* aliases, including multi-token forms), generic job-posting words, and
free random tokens — plus genuinely arbitrary unicode text — so the
partition claim is not limited to tidy skill-bearing input. A small-cap
extractor variant proves the partition holds over the *capped* analyzed set
too (``analyze`` partitions whatever ``analyzed`` it produced).

Per the design's PBT strategy, extractors are built once at module scope
over a small synthetic lexicon (constructing a spaCy ``PhraseMatcher`` per
Hypothesis example would dominate the runtime budget) and the pipeline is
``spacy.blank("en")`` — tokenizer-only, no model artifact. A blank pipeline
assigns no POS, so the POS gate passes candidates through and the
partition logic — the behavior under test — is exercised in isolation.

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

from matchlayer_api.scoring.keyword_analyzer import KeywordAnalysis
from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Synthetic lexicon and module-scoped extractors
# ---------------------------------------------------------------------------

# The set covers single-token aliases ("py", "js"), multi-token surface
# forms ("node js", "machine learning"), punctuation-bearing canonicals
# ("node.js", "ci/cd", "c++"), and equal-weight entries ("javascript" /
# "node.js") so the partition is exercised across the full matching surface.
_LEXICON: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop10",
        "skills": [
            {
                "canonical": "python",
                "display": "Python",
                "category": "language",
                "weight": 1.0,
                "aliases": ["py", "py3"],
            },
            {
                "canonical": "javascript",
                "display": "JavaScript",
                "category": "language",
                "weight": 0.9,
                "aliases": ["js"],
            },
            {
                "canonical": "node.js",
                "display": "Node.js",
                "category": "runtime",
                "weight": 0.9,
                "aliases": ["node js", "nodejs"],
            },
            {
                "canonical": "ci/cd",
                "display": "CI/CD",
                "category": "practice",
                "weight": 0.8,
                "aliases": ["cicd"],
            },
            {
                "canonical": "c++",
                "display": "C++",
                "category": "language",
                "weight": 0.8,
                "aliases": ["cpp"],
            },
            {
                "canonical": "machine learning",
                "display": "Machine Learning",
                "category": "domain",
                "weight": 0.7,
                "aliases": ["ml"],
            },
            {
                "canonical": "sql",
                "display": "SQL",
                "category": "language",
                "weight": 0.7,
                "aliases": [],
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
# lexicon, prebuilt matcher) and are safe to share across examples. The
# large-cap variant (50, exceeding the lexicon size) exercises the uncapped
# shape; the small-cap variant (2) proves the partition holds over the
# *capped* analyzed set too.
_EXTRACTORS: Final[tuple[Skill_Extractor, ...]] = (
    Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50),
    Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=2),
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
# assertions are on the *output* partition, not the input).
_token = st.one_of(
    st.sampled_from(_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# A document of tokens joined by single spaces; min_size=0 admits the empty
# document (empty analyzed set, the partition holds vacuously).
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Genuinely arbitrary text (unicode, punctuation, odd whitespace) so the
# claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)

# A resume that is empty after normalization: nothing but (unicode)
# whitespace, including the empty string. Such a text tokenizes to no
# words, so extraction finds nothing.
_whitespace_resume = st.text(alphabet=" \t\r\n\u00a0\u2003", min_size=0, max_size=20)

# ---------------------------------------------------------------------------
# Shared assertion
# ---------------------------------------------------------------------------


def _assert_exact_partition(analysis: KeywordAnalysis) -> None:
    """``matched`` and ``missing`` exactly partition ``analyzed`` (Req 4.4).

    Disjoint by term; and each output list is precisely the subsequence of
    ``analyzed`` its membership predicate selects — same elements, same
    order, same multiplicity, nothing fabricated.
    """
    matched_terms = {keyword.term for keyword in analysis.matched}
    missing_terms = {keyword.term for keyword in analysis.missing}

    assert not matched_terms & missing_terms, (
        "matched and missing share terms",
        matched_terms & missing_terms,
    )

    # Order- and multiplicity-preserving partition: reconstructing each side
    # by filtering `analyzed` reproduces the outputs exactly, which also
    # forces union == analyzed (every analyzed keyword lands in exactly one
    # side, and neither side contains anything outside analyzed).
    assert [k for k in analysis.analyzed if k.term in matched_terms] == analysis.matched
    assert [k for k in analysis.analyzed if k.term not in matched_terms] == analysis.missing


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(extractor_index=_extractor_index, resume_text=_document, job_description=_document)
@example(
    extractor_index=0,
    resume_text="",
    job_description="",
)
@example(
    # Partial overlap through aliases: resume covers python (via "py") and
    # node.js (via "nodejs") but not sql or machine learning.
    extractor_index=0,
    resume_text="py developer, strong nodejs experience",
    job_description="python node.js sql and machine learning required",
)
@example(
    # Small cap: analyzed keeps only the 2 highest-weighted JD skills; the
    # partition must cover exactly that capped set.
    extractor_index=1,
    resume_text="sql and go daily",
    job_description="python javascript sql go",
)
def test_matched_and_missing_partition_analyzed(
    extractor_index: int, resume_text: str, job_description: str
) -> None:
    """matched/missing exactly partition analyzed, per resume extraction (Req 4.4).

    For arbitrary resume and job-description texts, ``analyze()`` splits
    ``analyzed`` into disjoint ``matched`` / ``missing`` whose interleaved
    union is ``analyzed`` itself (order and multiplicity preserved), and
    membership agrees with running the same extractor against the resume
    text: every matched term is extracted from the resume, every missing
    term is not.
    """
    extractor = _EXTRACTORS[extractor_index]

    analysis = extractor.analyze(resume_text, job_description)
    _assert_exact_partition(analysis)

    resume_terms = {keyword.term for keyword in extractor.extract(resume_text)}
    for keyword in analysis.matched:
        assert keyword.term in resume_terms, (
            "matched term is not extracted from the resume text",
            keyword.term,
            resume_text,
        )
    for keyword in analysis.missing:
        assert keyword.term not in resume_terms, (
            "missing term is extracted from the resume text",
            keyword.term,
            resume_text,
        )


@settings(max_examples=200, deadline=None)
@given(
    extractor_index=_extractor_index,
    resume_text=_whitespace_resume,
    job_description=_document,
)
@example(
    extractor_index=0,
    resume_text="",
    job_description="python node.js sql and machine learning required",
)
@example(
    extractor_index=0,
    resume_text=" \t\n",
    job_description="ci/cd cpp golang engineer",
)
def test_empty_resume_yields_empty_matched_and_missing_equals_analyzed(
    extractor_index: int, resume_text: str, job_description: str
) -> None:
    """An empty-after-normalization resume matches nothing (Req 4.4).

    For any whitespace-only resume text (including the empty string) and
    any job description, ``matched`` is empty and ``missing`` equals
    ``analyzed`` element-for-element — the empty-resume clause of
    Requirement 4.4 — and the exact-partition invariant still holds.
    """
    extractor = _EXTRACTORS[extractor_index]

    analysis = extractor.analyze(resume_text, job_description)

    assert analysis.matched == []
    assert analysis.missing == analysis.analyzed
    _assert_exact_partition(analysis)
