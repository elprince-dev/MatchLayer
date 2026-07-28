"""Feature: phase-2-nlp-embeddings — Property 15.

Property 15: Suggestions reference exactly one missing skill each, capped,
ordered, deterministic.

    *For any* ordered set of missing skills, every generated suggestion
    references exactly one skill from that set, at most ``max_suggestions``
    suggestions are produced, they are ordered by descending lexicon weight
    of the addressed skill, and generating twice from identical input
    produces identical output.

**Validates: Requirements 8.1, 8.2, 8.4**

The missing sets under test are produced by the real Phase 2 pipeline: each
example scores a generated resume/JD pair through the
``Semantic_Match_Scorer`` (embeddings from the ``Embedding_Service`` over
the deterministic stub ``Text_Encoder``, per the design's PBT strategy), so
``missing_keywords`` varies with which lexicon skills the JD mentions and
the resume lacks. Two Hypothesis tests encode the property:

* **Reference, cap, and order.** Every suggestion's ``keyword`` is exactly
  one term from that result's ``missing_keywords`` set, no term is
  addressed twice, at most the drawn ``max_suggestions`` suggestions are
  produced (Req 8.2's cap is a constructor knob, so the strategy draws it),
  and the addressed-skill sequence follows descending lexicon weight with
  the established tie-break — the generator's stable descending-weight sort
  preserves the extractor's ascending-lexicographic order for equal
  weights, so the suggestion keywords equal the ``max_suggestions``-prefix
  of the ``missing_keywords`` terms exactly (Req 8.1, 8.2).

* **Determinism.** Scoring the identical inputs through two independently
  constructed scorer compositions (same lexicon, caps, weights, and
  Scorer_Version — hence two fresh ``Suggestion_Generator`` instances)
  produces the identical ordered suggestion list (Req 8.4).

The empty-``missing`` affirmative case (Req 8.3: exactly one affirmative
suggestion) is deliberately out of the first test's scope — Property 15
covers 8.1/8.2/8.4 only, and 8.3 is unit-tested in task 7.6 — so examples
whose resume covers every analyzed skill are discarded via ``assume``. The
determinism test keeps them: Req 8.4 applies to the affirmative output too.

The synthetic lexicon includes deliberate weight ties ("react"/"angular" at
0.7, "docker"/"machine learning" at 0.6) so the lexicographic tie-break is
genuinely exercised, not vacuously true. Generated JD texts always embed at
least one lexicon surface form at token boundaries, so the analyzed set is
non-empty and ``EmptyAnalyzedSetError`` (Req 4.10, unit-tested in 7.6)
never fires; the filler vocabulary is verified disjoint from every token
fragment of every lexicon surface form, so filler can neither add a skill
mention nor extend an embedded surface form into a longer lexicon match.

Per the design's PBT strategy the expensive pieces — the tokenizer-only
spaCy pipeline (``spacy.blank("en")``), the ``Skill_Extractor`` with its
prebuilt ``PhraseMatcher``, and the ``Embedding_Service`` over the stub
encoder — are built once at module scope; only the lightweight
``Semantic_Match_Scorer`` composition is constructed per example so the
suggestion cap can vary. The scorer is framework-free (Requirement 12.1):
everything is constructed directly, never touching settings, FastAPI, or
the database.
"""

from __future__ import annotations

from itertools import pairwise
from string import ascii_lowercase, digits
from typing import Final

import spacy
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.scorer import Semantic_Match_Scorer
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Module-scoped components (design PBT strategy: build the expensive pieces
# once; they are immutable and safe to share across examples)
# ---------------------------------------------------------------------------

_LEXICON: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop15",
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
                "weight": 0.9,
                "aliases": [],
            },
            # Deliberate weight tie: the ordering tie-break (ascending
            # lexicographic canonical term for equal weights) is exercised
            # whenever both land in the same missing set.
            {
                "canonical": "angular",
                "display": "Angular",
                "category": "framework",
                "weight": 0.7,
                "aliases": [],
            },
            {
                "canonical": "react",
                "display": "React",
                "category": "framework",
                "weight": 0.7,
                "aliases": [],
            },
            # Second deliberate tie, mixing a single-token and a multi-token
            # surface form at the same weight.
            {
                "canonical": "docker",
                "display": "Docker",
                "category": "tool",
                "weight": 0.6,
                "aliases": [],
            },
            {
                "canonical": "machine learning",
                "display": "Machine Learning",
                "category": "data",
                "weight": 0.6,
                "aliases": ["ml"],
            },
            {
                "canonical": "sql",
                "display": "SQL",
                "category": "language",
                "weight": 0.5,
                "aliases": [],
            },
            {
                "canonical": "terraform",
                "display": "Terraform",
                "category": "devops",
                "weight": 0.4,
                "aliases": [],
            },
        ],
    }
)

# Tokenizer-only pipeline: no POS model, so the POS gate passes candidates
# through and the lexicon stays the final authority — the suggestion
# behaviors under test are pipeline-independent.
_EXTRACTOR: Final[Skill_Extractor] = Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50)

_SEMANTIC: Final[Semantic_Scorer] = Semantic_Scorer()

# Small max_tokens so generated documents also exercise the chunked embedding
# path; the property is independent of which path produced the vectors.
_SERVICE: Final[Embedding_Service] = Embedding_Service(Stub_Text_Encoder(max_tokens=8))

_SCORER_VERSION: Final[str] = "2.0.0+lex.prop15+emb.stub@rev+spacy.blank_en@0"

# Fixed valid weight pair (suggestions do not depend on the score weights).
_W_SIMILARITY: Final[float] = 0.6
_W_KEYWORD: Final[float] = 0.4


def _make_scorer(max_suggestions: int) -> Semantic_Match_Scorer:
    """A Semantic_Match_Scorer over the shared components with the drawn cap.

    Per-example construction is cheap (only the Phase 1 Suggestion_Generator
    is composed); the extractor, semantic scorer, and lexicon are shared.
    Constructing per example is also what lets the determinism test compare
    two *independent* generator instances over identical input (Req 8.4).
    """
    return Semantic_Match_Scorer(
        _LEXICON,
        _EXTRACTOR,
        _SEMANTIC,
        w_similarity=_W_SIMILARITY,
        w_keyword=_W_KEYWORD,
        max_suggestions=max_suggestions,
        scorer_version=_SCORER_VERSION,
    )


# ---------------------------------------------------------------------------
# Vocabulary — filler provably disjoint from lexicon surface fragments
# ---------------------------------------------------------------------------

_SURFACES: Final[tuple[str, ...]] = tuple(
    sorted({surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)})
)

# Lexicon weight per canonical term, for the descending-weight assertion.
_WEIGHT_BY_TERM: Final[dict[str, float]] = {
    entry.canonical: entry.weight for entry in _LEXICON.entries
}


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

# The suggestion cap is a constructor knob (the configured
# MATCHLAYER_MATCH_MAX_SUGGESTIONS, default 10); drawing it small relative to
# the 8-skill lexicon makes the cap genuinely bind in many examples, and 0 is
# the strongest boundedness edge (an empty suggestion list).
_max_suggestions = st.integers(min_value=0, max_value=6)


@st.composite
def _jd_document(draw: st.DrawFn) -> str:
    """A JD with at least one lexicon skill mention at token boundaries.

    Guarantees a non-empty analyzed set so ``EmptyAnalyzedSetError`` (the
    Req 4.10 signal, covered by unit tests in task 7.6) never preempts the
    property. Up to six skill mentions keep the missing sets varied.
    """
    skills = draw(st.lists(_skill_surface, min_size=1, max_size=6))
    filler = draw(st.lists(_filler_token, min_size=0, max_size=12))
    tokens = draw(st.permutations([*skills, *filler]))
    return " ".join(tokens)


@st.composite
def _resume_document(draw: st.DrawFn) -> str:
    """A non-empty resume with zero or more skill mentions.

    Partial overlap with the JD's skills is what makes the missing sets vary
    from empty (full coverage) to the full analyzed set (no coverage).
    """
    skills = draw(st.lists(_skill_surface, min_size=0, max_size=4))
    filler = draw(st.lists(_filler_token, min_size=1, max_size=12))
    tokens = draw(st.permutations([*skills, *filler]))
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(
    max_suggestions=_max_suggestions,
    resume_text=_resume_document(),
    job_description=_jd_document(),
)
def test_suggestions_reference_one_missing_skill_each_capped_and_ordered(
    max_suggestions: int, resume_text: str, job_description: str
) -> None:
    """Each suggestion addresses one missing skill; capped; weight-ordered.

    Property 15 (Req 8.1, 8.2): for any generated resume/JD pair scored
    through the Phase 2 pipeline, every suggestion's ``keyword`` is a
    non-empty member of that result's ``missing_keywords`` terms, no missing
    skill is addressed twice, at most ``max_suggestions`` suggestions are
    produced, and the addressed skills follow descending lexicon weight with
    the established ascending-lexicographic tie-break — i.e. the suggestion
    keywords are exactly the ``max_suggestions``-prefix of the ordered
    ``missing_keywords`` terms.
    """
    scorer = _make_scorer(max_suggestions)
    resume_embedding = _SERVICE.embed(resume_text)
    jd_embedding = _SERVICE.embed(job_description)

    result = scorer.score(resume_text, job_description, resume_embedding, jd_embedding)

    # The empty-missing affirmative case (Req 8.3) is outside Property 15's
    # scope; it is unit-tested in task 7.6.
    assume(result.missing_keywords)

    missing_terms = [keyword.term for keyword in result.missing_keywords]
    addressed = [suggestion.keyword for suggestion in result.suggestions]

    # Cap (Req 8.2): at most max_suggestions — and exactly one suggestion per
    # missing skill up to the cap, so the count is fully determined.
    assert len(result.suggestions) <= max_suggestions
    assert len(result.suggestions) == min(len(missing_terms), max_suggestions)

    # Reference (Req 8.1): each suggestion addresses exactly one skill from
    # this request's missing set — a single non-empty keyword that is a
    # member of missing_terms — and no skill is addressed twice. The
    # empty-string sentinel of the affirmative suggestion never appears.
    assert all(keyword for keyword in addressed)
    assert set(addressed) <= set(missing_terms)
    assert len(set(addressed)) == len(addressed)

    # Order (Req 8.2): descending lexicon weight of the addressed skill…
    weights = [_WEIGHT_BY_TERM[keyword] for keyword in addressed]
    assert all(earlier >= later for earlier, later in pairwise(weights))

    # …with the established tie-break: the generator's stable descending-
    # weight sort preserves the extractor's ascending-lexicographic order for
    # equal weights, so the addressed sequence equals the cap-prefix of the
    # ordered missing terms exactly.
    assert addressed == missing_terms[:max_suggestions]


@settings(max_examples=150, deadline=None)
@given(
    max_suggestions=_max_suggestions,
    resume_text=_resume_document(),
    job_description=_jd_document(),
)
def test_identical_inputs_yield_identical_suggestions(
    max_suggestions: int, resume_text: str, job_description: str
) -> None:
    """Generating twice from identical input produces identical output.

    Property 15's determinism clause (Req 8.4): scoring the identical
    resume/JD pair and embeddings under an identical Scorer_Version through
    two independently constructed scorer compositions (hence two independent
    Suggestion_Generator instances) yields the identical ordered suggestion
    list — including on the empty-missing affirmative path.
    """
    resume_embedding = _SERVICE.embed(resume_text)
    jd_embedding = _SERVICE.embed(job_description)

    first = _make_scorer(max_suggestions).score(
        resume_text, job_description, resume_embedding, jd_embedding
    )
    second = _make_scorer(max_suggestions).score(
        resume_text, job_description, resume_embedding, jd_embedding
    )

    assert first.suggestions == second.suggestions
