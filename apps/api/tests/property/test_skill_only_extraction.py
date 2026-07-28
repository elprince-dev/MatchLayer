"""Feature: phase-2-nlp-embeddings — Property 7.

Property 7: Extraction output is skill-only.

    *For any* input text (arbitrary mixtures of lexicon surface forms,
    generic job-posting words, and random tokens), every term in the
    extracted analyzed, matched, and missing sets is a canonical
    Skill_Lexicon term — non-lexicon terms, including generic words like
    "check" and "selection", never appear.

**Validates: Requirements 4.1, 4.2, 4.9**

Phase 2 fixes a known Phase 1 quality defect: the Phase 1 Keyword_Analyzer
sourced analyzed keywords from single-document TF-IDF filtered by a
hand-curated stopword blocklist, and generic non-skill words ("check",
"selection") leaked into the missing-keywords list. The Skill_Extractor
retires the blocklist approach: the Skill_Lexicon is the final authority on
skill-hood, so the output is skill-only *by construction* (Requirements 4.1,
4.2) and generic job-posting terms are excluded no matter how prominently —
or how frequently — they appear in the text (Requirement 4.9, closing the
TF-IDF frequency loophole).

This module asserts that claim across a generated input space:

* **Skill-only output, everywhere.** For arbitrary resume and
  job-description documents, every ``Keyword.term`` in ``extract(text)``
  and in ``analyze()``'s ``analyzed`` / ``matched`` / ``missing`` sets is a
  member of the extractor's canonical lexicon-term set. Because the generic
  vocabulary below is disjoint from every synthetic lexicon, this subset
  claim simultaneously proves generic words never appear.
* **Frequency immunity.** A document built *exclusively* from generic
  job-posting words — each repeated an arbitrary number of times, the exact
  shape that inflated Phase 1 TF-IDF scores — extracts nothing, and every
  ``analyze()`` set stays empty (Requirements 4.2, 4.9).

Generated documents deliberately mix (a) lexicon surface forms (canonical
terms *and* aliases, including multi-token forms like "machine learning" and
"node js"), (b) generic job-posting words including the requirement-named
"check" and "selection", and (c) free random tokens, joined into documents —
plus genuinely arbitrary unicode text so the claim is not limited to tidy
ASCII input. Surface forms from *another* extractor's lexicon act as
realistic non-lexicon skill-shaped noise.

Per the design's PBT strategy, extractors are built once at module scope
over small synthetic lexicons (constructing a spaCy ``PhraseMatcher`` per
Hypothesis example would dominate the runtime budget) and the pipeline is
``spacy.blank("en")`` — tokenizer-only, no model artifact. A blank pipeline
assigns no POS, so the POS gate passes candidates through and the *lexicon
alone* must enforce skill-hood: precisely the authority this property pins
down. Extractor instances hold only immutable state (pipeline, lexicon,
prebuilt matcher) and are safe to share across examples.

The Skill_Extractor is framework-free (Requirement 12.1): this test
constructs it directly from synthetic lexicon documents and an injected
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
# Synthetic lexicons and module-scoped extractors
# ---------------------------------------------------------------------------

# Two disjoint synthetic lexicons (mirroring the unit-test construction
# pattern) so each extractor sees the other lexicon's surface forms as
# skill-shaped *non*-lexicon noise. Both include multi-token surface forms,
# punctuation-bearing canonicals, and equal-weight entries.
_LEXICON_A: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop7-a",
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
                "canonical": "java",
                "display": "Java",
                "category": "language",
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

_LEXICON_B: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop7-b",
        "skills": [
            {
                "canonical": "rust",
                "display": "Rust",
                "category": "language",
                "weight": 1.0,
                "aliases": [],
            },
            {
                "canonical": "kubernetes",
                "display": "Kubernetes",
                "category": "platform",
                "weight": 0.9,
                "aliases": ["k8s"],
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
                "canonical": "sql",
                "display": "SQL",
                "category": "language",
                "weight": 0.7,
                "aliases": ["structured query language"],
            },
        ],
    }
)


def _canonical_terms(lexicon: Skill_Lexicon) -> frozenset[str]:
    """The canonical-term universe an extractor over ``lexicon`` may emit."""
    return frozenset(entry.canonical for entry in lexicon.entries)


def _surfaces(lexicon: Skill_Lexicon) -> list[str]:
    """Every surface form (canonical + aliases) of ``lexicon``, sorted."""
    return sorted(
        {surface for entry in lexicon.entries for surface in (entry.canonical, *entry.aliases)}
    )


# Extractors are built once at module scope (a PhraseMatcher per Hypothesis
# example would dominate the runtime budget). The small-cap variant proves
# the skill-only claim is independent of the ``max_keywords`` cap.
_EXTRACTORS: Final[tuple[tuple[Skill_Extractor, frozenset[str]], ...]] = (
    (Skill_Extractor(spacy.blank("en"), _LEXICON_A, max_keywords=50), _canonical_terms(_LEXICON_A)),
    (Skill_Extractor(spacy.blank("en"), _LEXICON_B, max_keywords=50), _canonical_terms(_LEXICON_B)),
    (Skill_Extractor(spacy.blank("en"), _LEXICON_A, max_keywords=2), _canonical_terms(_LEXICON_A)),
)

# ---------------------------------------------------------------------------
# Vocabulary for generated text
# ---------------------------------------------------------------------------

# Generic job-posting words that are NOT skills in either lexicon — including
# "check" and "selection", the two terms Requirement 4.9 names as the Phase 1
# leak. Disjoint from every lexicon surface form above, so the subset
# assertion doubles as the generic-exclusion assertion.
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

_ALL_SURFACES: Final[list[str]] = sorted({*_surfaces(_LEXICON_A), *_surfaces(_LEXICON_B)})

# A single generated token: a lexicon surface form (either lexicon — the
# other lexicon's forms are non-lexicon noise for the extractor under test),
# a generic job-posting word, or a free random alphanumeric token (which may
# coincide with a surface form like "go" or "js"; that only makes the mix
# richer — the assertion is on the *output*, not the input).
_token = st.one_of(
    st.sampled_from(_ALL_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# A document of tokens joined by single spaces; min_size=0 admits the empty
# document (empty extraction, property holds vacuously).
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Genuinely arbitrary text (unicode, punctuation, odd whitespace) so the
# claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)

# A document made EXCLUSIVELY of generic job-posting words, each repeated
# 1..10 times — the frequency shape that inflated Phase 1 TF-IDF weights.
_generic_only_document = st.lists(
    st.tuples(st.sampled_from(_GENERIC_WORDS), st.integers(min_value=1, max_value=10)),
    min_size=1,
    max_size=10,
).map(lambda pairs: " ".join(word for word, count in pairs for _ in range(count)))

_extractor_index = st.integers(min_value=0, max_value=len(_EXTRACTORS) - 1)

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def _assert_skill_only(keywords: list[Keyword], canonicals: frozenset[str], label: str) -> None:
    for keyword in keywords:
        assert isinstance(keyword, Keyword)
        assert keyword.term in canonicals, (
            f"non-lexicon term leaked into the {label} set",
            keyword.term,
        )


@settings(max_examples=200, deadline=None)
@given(extractor_index=_extractor_index, resume_text=_document, job_description=_document)
@example(
    extractor_index=0,
    resume_text="",
    job_description="",
)
@example(
    # The requirement-named generic terms mixed with real skills: the skills
    # may appear, "check" / "selection" / "experience" / "team" must not.
    extractor_index=0,
    resume_text="python developer with strong team experience",
    job_description="check our selection process; python and java experience required",
)
@example(
    # Skill-shaped noise: lexicon B surface forms are non-lexicon terms for
    # the lexicon A extractor and must be excluded.
    extractor_index=0,
    resume_text="rust kubernetes k8s cpp sql",
    job_description="rust and kubernetes engineer, ci/cd required",
)
def test_extraction_output_is_skill_only(
    extractor_index: int, resume_text: str, job_description: str
) -> None:
    """Every emitted term is a canonical lexicon term (Req 4.1, 4.2, 4.9).

    For arbitrary mixtures of lexicon surface forms, generic job-posting
    words, and random tokens — in both text roles — every term in
    ``extract(text)`` and in ``analyze()``'s analyzed / matched / missing
    sets is a member of the extractor's canonical-term universe. Because
    the generic vocabulary is disjoint from the lexicons, membership in the
    canonical set is exactly the "generic words never appear" claim.
    """
    extractor, canonicals = _EXTRACTORS[extractor_index]

    _assert_skill_only(extractor.extract(job_description), canonicals, "extract(jd)")
    _assert_skill_only(extractor.extract(resume_text), canonicals, "extract(resume)")

    analysis = extractor.analyze(resume_text, job_description)
    _assert_skill_only(analysis.analyzed, canonicals, "analyzed")
    _assert_skill_only(analysis.matched, canonicals, "matched")
    _assert_skill_only(analysis.missing, canonicals, "missing")


@settings(max_examples=200, deadline=None)
@given(
    extractor_index=_extractor_index,
    resume_text=_document,
    generic_document=_generic_only_document,
)
@example(
    # The literal Phase 1 leak shape, repeated for TF-IDF-style prominence.
    extractor_index=0,
    resume_text="python and java daily",
    generic_document="check check check selection selection selection experience team",
)
def test_generic_words_never_extracted_regardless_of_frequency(
    extractor_index: int, resume_text: str, generic_document: str
) -> None:
    """A generic-words-only document yields empty skill sets (Req 4.2, 4.9).

    No matter how many times each generic job-posting word repeats — the
    exact frequency shape that leaked "check" and "selection" through the
    Phase 1 TF-IDF derivation — extraction finds nothing, and with the
    generic document as the job description every ``analyze()`` set is
    empty (nothing analyzed means nothing matched and nothing missing).
    """
    extractor, _canonicals = _EXTRACTORS[extractor_index]

    assert extractor.extract(generic_document) == []

    analysis = extractor.analyze(resume_text, generic_document)
    assert analysis.analyzed == []
    assert analysis.matched == []
    assert analysis.missing == []
