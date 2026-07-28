"""Feature: phase-2-nlp-embeddings — Property 4.

Property 4: The final score is reproducible from the breakdown alone, with
the Phase 1 shape preserved.

    *For any* similarity component and coverage component in [0, 1] and
    valid weight pair, the final score equals
    ``max(0, min(100, round(100 * (w_sim * sim + w_kw * cov))))`` and lies
    in [0, 100]; the breakdown contains every Phase 1 field
    (``similarity_component``, ``keyword_coverage_component``,
    ``weight_similarity``, ``weight_keyword``, ``final_score``) under its
    Phase 1 name and type plus a ``similarity_method`` identifier;
    recomputing the weighted formula from the breakdown fields alone
    reproduces ``final_score``; the recorded coverage component equals
    ``|matched| / |analyzed|`` recomputed from the returned keyword sets
    (0 when analyzed is empty); and a zero similarity component with a
    non-zero coverage component still yields the weighted combination
    rather than 0.

**Validates: Requirements 3.2, 3.3, 3.6, 4.6, 9.5**

Two Hypothesis tests encode the property over generated resume/JD texts and
weight pairs:

* **Reproducibility and shape.** Embeddings for the generated texts come
  from the ``Embedding_Service`` over the deterministic stub
  ``Text_Encoder`` (no model artifact, per the design's PBT strategy). The
  breakdown alone re-derives ``final_score`` via the documented formula
  (Req 3.2), ``final_score`` equals the result's score, the constructed
  weights are echoed, both components are valid fractions, coverage equals
  the matched fraction recomputed from the returned keyword lists
  (Req 4.6), and every Phase 1 breakdown field keeps its Phase 1 name and
  type with ``similarity_method == "semantic-embedding"`` as the only
  addition (Req 3.3, 9.5).

* **Zero similarity never suppresses coverage.** The JD embedding is the
  exact negation of the resume embedding (cosine -1 → similarity component
  clamps to 0) while the resume covers every analyzed skill (coverage 1).
  The score is still the weighted combination — non-zero whenever the
  keyword weight rounds to at least one point — never forced to 0
  (Req 3.6).

Generated JD texts always embed at least one lexicon surface form at token
boundaries, so the analyzed set is non-empty and
``EmptyAnalyzedSetError`` (Req 4.10, unit-tested in task 7.6) never fires.
The filler vocabulary is verified disjoint from every token fragment of
every lexicon surface form, so filler can neither add a skill mention nor
extend an embedded surface form into a longer lexicon match.

Per the design's PBT strategy the expensive pieces — the spaCy pipeline
(``spacy.blank("en")``, tokenizer-only), the ``Skill_Extractor`` with its
prebuilt ``PhraseMatcher``, and the ``Embedding_Service`` over the stub
encoder — are built once at module scope; only the lightweight
``Semantic_Match_Scorer`` composition is constructed per example so the
weight pair can vary. The scorer is framework-free (Requirement 12.1):
everything is constructed directly, never touching settings, FastAPI, or
the database.
"""

from __future__ import annotations

from string import ascii_lowercase, digits
from typing import Final

import spacy
from hypothesis import given, settings
from hypothesis import strategies as st

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service
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
        "lexicon_version": "prop4",
        "skills": [
            {
                "canonical": "python",
                "display": "Python",
                "category": "language",
                "weight": 1.0,
                "aliases": ["py"],
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
                "canonical": "docker",
                "display": "Docker",
                "category": "tool",
                "weight": 0.6,
                "aliases": [],
            },
            {
                "canonical": "sql",
                "display": "SQL",
                "category": "language",
                "weight": 0.5,
                "aliases": [],
            },
        ],
    }
)

# Tokenizer-only pipeline: no POS model, so the POS gate passes candidates
# through and the lexicon stays the final authority — the behaviors under
# test here (formula, breakdown shape, coverage) are pipeline-independent.
_EXTRACTOR: Final[Skill_Extractor] = Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50)

_SEMANTIC: Final[Semantic_Scorer] = Semantic_Scorer()

# Small max_tokens so generated documents also exercise the chunked
# embedding path; the property is independent of which path produced the
# vectors.
_SERVICE: Final[Embedding_Service] = Embedding_Service(Stub_Text_Encoder(max_tokens=8))

_SCORER_VERSION: Final[str] = "2.0.0+lex.prop4+emb.stub@rev+spacy.blank_en@0"

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
# Vocabulary — filler provably disjoint from lexicon surface fragments
# ---------------------------------------------------------------------------

_SURFACES: Final[tuple[str, ...]] = tuple(
    sorted({surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)})
)


def _fragments() -> frozenset[str]:
    """Every token fragment of every surface form (plus the full forms)."""
    pieces: set[str] = set()
    for surface in _SURFACES:
        pieces.add(surface)
        pieces.update(surface.split())
    return frozenset(pieces)


_FORBIDDEN_FILLER: Final[frozenset[str]] = _fragments()

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
assert not set(_GENERIC_WORDS) & _FORBIDDEN_FILLER

_filler_token = st.one_of(
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10).filter(
        lambda token: token not in _FORBIDDEN_FILLER
    ),
)

_skill_surface = st.sampled_from(_SURFACES)

# Weight pairs valid per the settings contract: each in [0, 1], summing to 1.
_weight_similarity = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@st.composite
def _jd_document(draw: st.DrawFn) -> str:
    """A JD with at least one lexicon skill mention at token boundaries.

    Guarantees a non-empty analyzed set so ``EmptyAnalyzedSetError`` (the
    Req 4.10 signal, covered by unit tests) never preempts the property.
    """
    skills = draw(st.lists(_skill_surface, min_size=1, max_size=4))
    filler = draw(st.lists(_filler_token, min_size=0, max_size=12))
    tokens = draw(st.permutations([*skills, *filler]))
    return " ".join(tokens)


@st.composite
def _resume_document(draw: st.DrawFn) -> str:
    """A non-empty resume with zero or more skill mentions."""
    skills = draw(st.lists(_skill_surface, min_size=0, max_size=4))
    filler = draw(st.lists(_filler_token, min_size=1, max_size=12))
    tokens = draw(st.permutations([*skills, *filler]))
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Shared assertions
# ---------------------------------------------------------------------------

# Phase 1 breakdown field names and their exact Phase 1 types (Req 9.5).
_PHASE1_FIELDS: Final[dict[str, type]] = {
    "similarity_component": float,
    "keyword_coverage_component": float,
    "weight_similarity": float,
    "weight_keyword": float,
    "final_score": int,
}

_COVERAGE_TOL: Final[float] = 1e-9


def _assert_property_four(result: ScoreResult, w_similarity: float, w_keyword: float) -> None:
    """All Property 4 clauses that hold for every scored example."""
    breakdown = result.breakdown

    # Phase 1 shape preserved: every Phase 1 field under its Phase 1 name and
    # exact type, plus the additive similarity_method identifier (Req 3.3, 9.5).
    for name, expected_type in _PHASE1_FIELDS.items():
        assert hasattr(breakdown, name), f"Phase 1 breakdown field {name!r} missing"
        value = getattr(breakdown, name)
        assert type(value) is expected_type, (name, type(value))
    assert breakdown.similarity_method == "semantic-embedding"

    # Components are valid fractions; the constructed weights are echoed.
    assert 0.0 <= breakdown.similarity_component <= 1.0
    assert 0.0 <= breakdown.keyword_coverage_component <= 1.0
    assert breakdown.weight_similarity == w_similarity
    assert breakdown.weight_keyword == w_keyword

    # Coverage equals |matched| / |analyzed| recomputed from the returned
    # keyword sets, 0 when analyzed is empty (Req 4.6). matched/missing
    # partition the analyzed set by construction (Property 10).
    matched_count = len(result.matched_keywords)
    analyzed_count = matched_count + len(result.missing_keywords)
    expected_coverage = (matched_count / analyzed_count) if analyzed_count else 0.0
    assert abs(breakdown.keyword_coverage_component - expected_coverage) <= _COVERAGE_TOL

    # The breakdown reports the score the caller acts on (Req 3.3) and
    # re-derives it from its own fields alone via the documented formula
    # (Req 3.2): final = max(0, min(100, round(100 * (w_sim*sim + w_kw*cov)))).
    assert breakdown.final_score == result.score
    recomputed = max(
        0,
        min(
            100,
            round(
                100
                * (
                    breakdown.weight_similarity * breakdown.similarity_component
                    + breakdown.weight_keyword * breakdown.keyword_coverage_component
                )
            ),
        ),
    )
    assert recomputed == breakdown.final_score
    assert 0 <= result.score <= 100


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(
    w_similarity=_weight_similarity,
    resume_text=_resume_document(),
    job_description=_jd_document(),
)
def test_final_score_is_reproducible_from_the_breakdown_alone(
    w_similarity: float, resume_text: str, job_description: str
) -> None:
    """The breakdown alone re-derives the score, in the Phase 1 shape.

    Property 4 (Req 3.2, 3.3, 4.6, 9.5): for any valid weight pair and any
    generated resume/JD pair (embedded through the Embedding_Service over
    the stub encoder), recomputing the weighted/rounded/clamped formula from
    the breakdown fields alone reproduces ``final_score``; the coverage
    component equals the matched fraction recomputed from the returned
    keyword sets; and the breakdown carries every Phase 1 field under its
    Phase 1 name and type plus ``similarity_method == "semantic-embedding"``.
    """
    w_keyword = 1.0 - w_similarity
    scorer = _make_scorer(w_similarity, w_keyword)

    resume_embedding = _SERVICE.embed(resume_text)
    jd_embedding = _SERVICE.embed(job_description)
    result = scorer.score(resume_text, job_description, resume_embedding, jd_embedding)

    _assert_property_four(result, w_similarity, w_keyword)


@settings(max_examples=150, deadline=None)
@given(w_similarity=_weight_similarity, job_description=_jd_document())
def test_zero_similarity_does_not_suppress_non_zero_coverage(
    w_similarity: float, job_description: str
) -> None:
    """A zero similarity component still yields the weighted combination.

    Property 4's Req 3.6 clause: with the JD embedding the exact negation of
    the resume embedding (cosine -1 → similarity component clamps to 0) and
    the resume covering every analyzed skill (coverage 1), the score is the
    weighted combination — at least 1 whenever ``100 * w_keyword`` rounds to
    a point — never forced to 0 by the zero similarity half.
    """
    w_keyword = 1.0 - w_similarity
    scorer = _make_scorer(w_similarity, w_keyword)

    # The resume repeats the JD verbatim plus filler, so extract(resume) ⊇
    # analyzed and coverage is exactly 1.0.
    resume_text = f"{job_description} seasoned engineer"
    resume_embedding = _SERVICE.embed(resume_text)
    jd_embedding = [-component for component in resume_embedding]

    result = scorer.score(resume_text, job_description, resume_embedding, jd_embedding)

    _assert_property_four(result, w_similarity, w_keyword)

    # Opposite unit vectors: cosine -1 up to float rounding, so the mapped
    # component clamps to (essentially) zero...
    assert result.breakdown.similarity_component <= 1e-9
    # ...while the coverage half is fully populated...
    assert result.breakdown.keyword_coverage_component == 1.0
    # ...and the weighted combination survives: whenever the keyword weight
    # alone is worth at least one rounded point, the score is non-zero
    # (Req 3.6 — zero similarity never zeroes the result).
    if round(100 * w_keyword) >= 1:
        assert result.score >= 1
