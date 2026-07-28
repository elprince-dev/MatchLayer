"""Feature: phase-2-nlp-embeddings — Property 5.

Property 5: Empty-after-normalization inputs score zero.

    *For any* pair of texts in which at least one member consists solely of
    whitespace characters (including the empty string), the Phase 2
    ``Semantic_Match_Scorer`` returns a final score of 0 with both breakdown
    components equal to 0.

**Validates: Requirements 3.5**

The Phase 2 scorer performs the empty-after-normalization check *first*
(design §4, the Phase 1 ``_normalize``: case-fold + whitespace-collapse).
When either text normalizes to the empty string:

* the similarity half is forced to ``0.0`` without ever consulting the
  supplied embeddings, and
* the coverage half is naturally ``0.0`` (an empty JD yields an empty
  analyzed set; an empty resume yields an empty matched set),

so the blended, clamped score is exactly ``0`` under any valid weight pair
— and the call never raises, *even when the embeddings are degenerate*.
To prove the empty check genuinely precedes the similarity path, the
generated embeddings deliberately include the inputs that would make
``Semantic_Scorer.similarity_component`` raise ``EmbeddingGeometryError``:
zero-magnitude vectors and dimension-mismatched pairs. If the scorer
consulted the embeddings before (or despite) the empty check, those
examples would fail with an exception instead of returning 0.

Three Hypothesis tests cover the configurations Requirement 3.5 enumerates:
empty resume with an arbitrary JD, arbitrary resume with an empty JD, and
both empty. The "without error" clause is checked structurally — each test
calls :meth:`Semantic_Match_Scorer.score` and asserts on its result, so any
exception raised by the empty path would fail the example.

Per the design's PBT strategy the expensive pieces — the tokenizer-only
spaCy pipeline, the ``Skill_Extractor`` with its prebuilt ``PhraseMatcher``,
and the shared ``Semantic_Scorer`` — are built once at module scope
(following the Property 4 module's conventions); only the lightweight
``Semantic_Match_Scorer`` composition is constructed per example so the
weight pair can vary. The scorer is framework-free (Requirement 12.1):
everything is constructed directly, never touching settings, FastAPI, or
the database. No embedding service is needed at all — the embeddings under
test are arbitrary raw vectors, which is precisely the point.
"""

from __future__ import annotations

from string import ascii_lowercase, digits
from typing import Final

import spacy
from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.scorer import ScoreResult, Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Module-scoped components (design PBT strategy: build the expensive pieces
# once; they are immutable and safe to share across examples)
# ---------------------------------------------------------------------------

_LEXICON: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop5",
        "skills": [
            {
                "canonical": "python",
                "display": "Python",
                "category": "language",
                "weight": 1.0,
                "aliases": ["py"],
            },
            {
                "canonical": "machine learning",
                "display": "Machine Learning",
                "category": "domain",
                "weight": 0.7,
                "aliases": ["ml"],
            },
            {
                "canonical": "docker",
                "display": "Docker",
                "category": "tool",
                "weight": 0.6,
                "aliases": [],
            },
        ],
    }
)

# Tokenizer-only pipeline: no POS model, so the POS gate passes candidates
# through and the lexicon stays the final authority. The empty-input contract
# under test is pipeline-independent.
_EXTRACTOR: Final[Skill_Extractor] = Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50)

_SEMANTIC: Final[Semantic_Scorer] = Semantic_Scorer()

_SCORER_VERSION: Final[str] = "2.0.0+lex.prop5+emb.stub@rev+spacy.blank_en@0"

_MAX_SUGGESTIONS: Final[int] = 10


def _make_scorer(w_similarity: float, w_keyword: float) -> Semantic_Match_Scorer:
    """A Semantic_Match_Scorer over the shared components with the drawn weights.

    Per-example construction is cheap (only the Phase 1 Suggestion_Generator
    is composed); the extractor, semantic scorer, and lexicon are shared.
    """
    return Semantic_Match_Scorer(
        _LEXICON,
        _EXTRACTOR,
        _SEMANTIC,
        w_similarity=w_similarity,
        w_keyword=w_keyword,
        max_suggestions=_MAX_SUGGESTIONS,
        scorer_version=_SCORER_VERSION,
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Whitespace characters that ``str.split()`` collapses away — the ASCII set
# plus a sample of Unicode whitespace (NBSP, en/em spaces, line/paragraph
# separators, ideographic space). A string drawn only from these (including
# the empty string at ``min_size=0``) normalizes to the empty string —
# "empty after normalization" in the precise sense Requirement 3.5 means
# (spaces, tabs, newlines, unicode whitespace, mixed).
_WHITESPACE_CHARS: Final[str] = " \t\n\r\f\v\u00a0\u2002\u2003\u2009\u2028\u2029\u3000"

_blank_text = st.text(alphabet=_WHITESPACE_CHARS, min_size=0, max_size=24)


def _normalizes_to_empty(text: str) -> bool:
    """True iff ``text`` is empty after the scorer's normalization.

    Mirrors :func:`matchlayer_api.scoring.scorer._normalize` (case-fold +
    whitespace-collapse) so the test's precondition is stated against the
    exact rule the scorer applies, not a private import.
    """
    return " ".join(text.casefold().split()) == ""


# The arbitrary (non-empty) side mixes lexicon surface forms, generic
# job-posting filler, and free random tokens, so the JD's analyzed set is
# frequently populated and the empty-resume case genuinely exercises the
# "0 / |analyzed|" coverage branch rather than only the empty-analyzed one.
_SURFACES: Final[tuple[str, ...]] = tuple(
    sorted({surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)})
)

_GENERIC_WORDS: Final[list[str]] = [
    "developer",
    "engineer",
    "experience",
    "team",
    "required",
    "preferred",
    "years",
    "strong",
    "build",
    "cloud",
    "platform",
    "services",
]

_token = st.one_of(
    st.sampled_from(_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# An arbitrary value that happens to normalize to "" simply degenerates into
# a both-empty case (still score 0), so no example is ever invalid.
_arbitrary_document = st.one_of(
    st.lists(_token, min_size=1, max_size=20).map(" ".join),
    st.text(min_size=1, max_size=120),
)

# Arbitrary embeddings, deliberately including degenerate geometry:
#
# * ``_finite_vector`` — any finite float vector of dimension 1..12,
#   including vectors whose components are all 0.0 (zero magnitude);
# * ``_zero_vector`` — an explicit all-zeros vector, guaranteeing
#   zero-magnitude coverage on every run.
#
# Zero-magnitude vectors and dimension-mismatched pairs (the dimensions of
# the two sides are drawn independently) are exactly the inputs that make
# ``Semantic_Scorer.similarity_component`` raise ``EmbeddingGeometryError``
# — so any example that returns instead of raising proves the empty check
# precedes the similarity path (Requirement 3.5).
_finite_component = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)

_finite_vector = st.lists(_finite_component, min_size=1, max_size=12)

_zero_vector = st.integers(min_value=1, max_value=12).map(lambda dim: [0.0] * dim)

_embedding = st.one_of(_finite_vector, _zero_vector)

# Weight pairs valid per the settings contract: each in [0, 1], summing to 1.
_weight_similarity = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------


def _assert_zero_without_error(result: ScoreResult) -> None:
    """Assert the Property 5 contract on a produced :class:`ScoreResult`.

    A final score of 0 with both breakdown components equal to 0
    (Requirement 3.5). The mere fact that ``result`` exists means
    :meth:`Semantic_Match_Scorer.score` returned rather than raised — the
    "no error even with degenerate embeddings" half of the property.
    """
    assert result.score == 0
    assert result.breakdown.final_score == 0
    assert result.breakdown.similarity_component == 0.0
    assert result.breakdown.keyword_coverage_component == 0.0


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    w_similarity=_weight_similarity,
    resume_text=_blank_text,
    job_description=_arbitrary_document,
    resume_embedding=_embedding,
    jd_embedding=_embedding,
)
@example(
    w_similarity=0.6,
    resume_text="",
    job_description="python developer",
    resume_embedding=[0.0, 0.0, 0.0],
    jd_embedding=[1.0, 2.0],
)
@example(
    w_similarity=1.0,
    resume_text=" \t\n",
    job_description="ml docker engineer",
    resume_embedding=[0.0],
    jd_embedding=[0.0],
)
def test_empty_resume_with_arbitrary_jd_scores_zero(
    w_similarity: float,
    resume_text: str,
    job_description: str,
    resume_embedding: list[float],
    jd_embedding: list[float],
) -> None:
    """An empty/whitespace-only resume scores 0 against any JD (Req 3.5).

    The analyzed set comes from the (arbitrary) JD and may be non-empty, but
    no term can be present in an empty resume, so coverage is 0; the
    similarity half is forced to 0 without consulting the embeddings — even
    zero-magnitude or dimension-mismatched vectors never raise.
    """
    # Precondition: the resume is empty after the scorer's normalization.
    assert _normalizes_to_empty(resume_text)

    scorer = _make_scorer(w_similarity, 1.0 - w_similarity)
    result = scorer.score(resume_text, job_description, resume_embedding, jd_embedding)

    _assert_zero_without_error(result)


@settings(max_examples=200, deadline=None)
@given(
    w_similarity=_weight_similarity,
    resume_text=_arbitrary_document,
    job_description=_blank_text,
    resume_embedding=_embedding,
    jd_embedding=_embedding,
)
@example(
    w_similarity=0.6,
    resume_text="python developer with docker",
    job_description="",
    resume_embedding=[1.0, 2.0, 3.0],
    jd_embedding=[0.0, 0.0],
)
@example(
    w_similarity=0.0,
    resume_text="seasoned engineer",
    job_description="   ",
    resume_embedding=[0.0, 0.0],
    jd_embedding=[0.0, 0.0, 0.0],
)
def test_arbitrary_resume_with_empty_jd_scores_zero(
    w_similarity: float,
    resume_text: str,
    job_description: str,
    resume_embedding: list[float],
    jd_embedding: list[float],
) -> None:
    """An empty/whitespace-only JD scores 0 against any resume (Req 3.5).

    With an empty JD the analyzed set is empty, so coverage is defined as 0
    — and ``EmptyAnalyzedSetError`` is *not* raised, because that signal is
    reserved for non-empty JDs (Requirement 4.10). The similarity half is
    forced to 0 without consulting the embeddings.
    """
    # Precondition: the JD is empty after the scorer's normalization.
    assert _normalizes_to_empty(job_description)

    scorer = _make_scorer(w_similarity, 1.0 - w_similarity)
    result = scorer.score(resume_text, job_description, resume_embedding, jd_embedding)

    _assert_zero_without_error(result)


@settings(max_examples=200, deadline=None)
@given(
    w_similarity=_weight_similarity,
    resume_text=_blank_text,
    job_description=_blank_text,
    resume_embedding=_embedding,
    jd_embedding=_embedding,
)
@example(
    w_similarity=0.5,
    resume_text="",
    job_description="",
    resume_embedding=[0.0],
    jd_embedding=[0.0],
)
@example(
    w_similarity=0.5,
    resume_text="\n\t ",
    job_description="  \r\f\v ",
    resume_embedding=[0.0, 1.0],
    jd_embedding=[2.0, 3.0, 4.0],
)
@example(
    w_similarity=0.3,
    resume_text="\u00a0\u3000",
    job_description="\u2028 \u2003",
    resume_embedding=[1.0],
    jd_embedding=[0.0, 0.0],
)
def test_both_empty_scores_zero(
    w_similarity: float,
    resume_text: str,
    job_description: str,
    resume_embedding: list[float],
    jd_embedding: list[float],
) -> None:
    """Two empty/whitespace-only texts score 0 (Req 3.5).

    Both halves are 0 (the similarity path is skipped and the analyzed set
    is empty), so the blended score is 0 under any valid weight pair and the
    call does not raise regardless of embedding geometry.
    """
    # Precondition: both sides are empty after the scorer's normalization.
    assert _normalizes_to_empty(resume_text)
    assert _normalizes_to_empty(job_description)

    scorer = _make_scorer(w_similarity, 1.0 - w_similarity)
    result = scorer.score(resume_text, job_description, resume_embedding, jd_embedding)

    _assert_zero_without_error(result)
