"""Feature: phase-2-nlp-embeddings — Property 17.

Property 17: The full Phase 2 pipeline is deterministic.

    *For any* resume text and job-description text, two runs of the complete
    Phase 2 pipeline — embedding both texts through the ``Embedding_Service``
    and scoring through the ``Semantic_Match_Scorer`` — via two independently
    constructed pipeline compositions carrying the same ``Scorer_Version``
    produce identical results: identical ``score``, identical breakdown,
    identical matched/missing keyword lists, identical suggestions, and the
    identical ``scorer_version`` stamp.

**Validates: Requirements 6.6, 4.7**

Requirement 6.6 says identical inputs scored under an identical
``Scorer_Version`` produce identical stored outputs; Requirement 4.7 says the
Skill_Extractor is deterministic for identical input text and lexicon. This
module pins both at the pipeline level: determinism must hold not merely for
repeated calls on one object graph (where shared caches could mask state),
but across **independently constructed** compositions — two separate spaCy
pipelines, two separate ``Skill_Extractor`` instances with freshly built
``PhraseMatcher`` state, two separate ``Embedding_Service`` instances over
two separate stub encoders, and two separate ``Semantic_Match_Scorer``
instances. Nothing is shared between stack A and stack B except the lexicon
*document* (parsed into two independent ``Skill_Lexicon`` objects) and the
configuration values, so equality of outputs shows the result is a pure
function of (inputs, lexicon content, configuration, ``Scorer_Version``) —
no hidden per-instance state participates.

Per the design's PBT strategy the two stacks are built once at module scope
(constructing a spaCy ``PhraseMatcher`` per Hypothesis example would dominate
the runtime budget) with the deterministic hash-based stub ``Text_Encoder``
(design D6 — no model artifact in property tests). The stub's small
``max_tokens`` means longer generated documents also exercise the chunked
embedding path, so the determinism claim covers chunk-and-aggregate too.

Generated documents mix lexicon surface forms (canonicals and aliases),
generic job-posting words, and arbitrary unicode text, so the claim covers
the empty-input path, skill-free text, and skill-bearing text alike. Inputs
whose JD is non-empty after normalization but yields an empty analyzed set
raise ``EmptyAnalyzedSetError`` (Requirement 4.10 — the service-layer
fallback signal, unit-tested in task 7.6); both stacks must agree on *that*
outcome too, so the test asserts the error raises deterministically rather
than discarding those examples.

The pipeline is framework-free (Requirement 12.1): everything is constructed
directly, never touching settings, FastAPI, or the database.
"""

from __future__ import annotations

from string import ascii_lowercase, digits
from typing import Final

import pytest
import spacy
from hypothesis import example, given, settings
from hypothesis import strategies as st

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service
from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.scorer import (
    EmptyAnalyzedSetError,
    ScoreResult,
    Semantic_Match_Scorer,
)
from matchlayer_api.scoring.semantic import Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Two independently constructed pipeline stacks over one lexicon document
# ---------------------------------------------------------------------------

# The raw lexicon document. Parsed TWICE below so each stack owns an
# independent Skill_Lexicon object — sharing the parsed object could let
# object identity (rather than content) carry the equality.
_LEXICON_DOC: Final[dict[str, object]] = {
    "schema_version": 1,
    "lexicon_version": "prop17",
    "skills": [
        {
            "canonical": "python",
            "display": "Python",
            "category": "language",
            "weight": 1.0,
            "aliases": ["py"],
        },
        {
            "canonical": "kubernetes",
            "display": "Kubernetes",
            "category": "platform",
            "weight": 0.9,
            "aliases": ["k8s"],
        },
        # Deliberate weight tie so the ordering tie-break participates in the
        # determinism claim.
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
        {
            "canonical": "machine learning",
            "display": "Machine Learning",
            "category": "domain",
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
    ],
}

_SCORER_VERSION: Final[str] = "2.0.0+lex.prop17+emb.stub@rev+spacy.blank_en@0"
_W_SIMILARITY: Final[float] = 0.6
_W_KEYWORD: Final[float] = 0.4
_MAX_KEYWORDS: Final[int] = 50
_MAX_SUGGESTIONS: Final[int] = 10


def _build_stack() -> tuple[Embedding_Service, Semantic_Match_Scorer]:
    """One complete, independent Phase 2 pipeline composition.

    Everything below the raw configuration is freshly constructed: the spaCy
    pipeline, the lexicon object, the extractor (and its PhraseMatcher), the
    stub encoder, the embedding service, and the scorer.
    """
    lexicon = Skill_Lexicon(_LEXICON_DOC)
    extractor = Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=_MAX_KEYWORDS)
    scorer = Semantic_Match_Scorer(
        lexicon,
        extractor,
        Semantic_Scorer(),
        w_similarity=_W_SIMILARITY,
        w_keyword=_W_KEYWORD,
        max_suggestions=_MAX_SUGGESTIONS,
        scorer_version=_SCORER_VERSION,
    )
    # Small max_tokens: longer generated documents exercise the chunked
    # embedding path, so determinism covers chunk-and-aggregate.
    service = Embedding_Service(Stub_Text_Encoder(max_tokens=8))
    return service, scorer


_SERVICE_A, _SCORER_A = _build_stack()
_SERVICE_B, _SCORER_B = _build_stack()


def _run_pipeline(
    service: Embedding_Service, scorer: Semantic_Match_Scorer, resume_text: str, jd_text: str
) -> ScoreResult:
    """Embed both texts and score — the full Phase 2 scoring pipeline."""
    return scorer.score(resume_text, jd_text, service.embed(resume_text), service.embed(jd_text))


# ---------------------------------------------------------------------------
# Generated documents
# ---------------------------------------------------------------------------

_SURFACES: Final[list[str]] = sorted(
    {
        surface
        for skill in _LEXICON_DOC["skills"]  # type: ignore[union-attr]
        for surface in (skill["canonical"], *skill["aliases"])  # type: ignore[index]
    }
)

_GENERIC_WORDS: Final[list[str]] = [
    "developer",
    "engineer",
    "experience",
    "team",
    "required",
    "years",
    "strong",
    "build",
    "cloud",
    "platform",
]

_token = st.one_of(
    st.sampled_from(_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# min_size=0 admits the empty document: the empty-after-normalization zero
# path must be deterministic too. Up to 40 tokens crosses the stub encoder's
# max_tokens=8 chunk boundary many times over.
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Arbitrary unicode text so the claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(resume_text=_document, job_description=_document)
@example(resume_text="", job_description="")
@example(resume_text="python and k8s daily", job_description="py react angular ml sql required")
@example(
    # Skill-free but non-empty JD: both stacks must agree on the
    # EmptyAnalyzedSetError outcome (Req 4.10) deterministically.
    resume_text="python expert",
    job_description="wonderful opportunity for a passionate person",
)
def test_independent_pipeline_stacks_produce_identical_results(
    resume_text: str, job_description: str
) -> None:
    """Two independent Phase 2 pipeline stacks agree on every output field.

    Property 17 (Req 6.6, 4.7): for any resume/JD pair, running the full
    pipeline (embed both texts, score) through stack A and stack B —
    independently constructed compositions sharing only the lexicon document,
    configuration, and ``Scorer_Version`` — yields identical results:
    identical score, breakdown, matched/missing keyword lists (content and
    order), suggestions (content and order), and ``scorer_version`` stamp.
    When the inputs make the pipeline signal ``EmptyAnalyzedSetError``
    instead (non-empty JD, empty analyzed set — Req 4.10), both stacks must
    signal it alike.
    """
    try:
        first = _run_pipeline(_SERVICE_A, _SCORER_A, resume_text, job_description)
    except EmptyAnalyzedSetError:
        # Determinism of the error outcome: stack B must raise it too.
        with pytest.raises(EmptyAnalyzedSetError):
            _run_pipeline(_SERVICE_B, _SCORER_B, resume_text, job_description)
        return

    second = _run_pipeline(_SERVICE_B, _SCORER_B, resume_text, job_description)

    assert first.score == second.score
    assert first.breakdown == second.breakdown
    assert first.matched_keywords == second.matched_keywords
    assert first.missing_keywords == second.missing_keywords
    assert first.suggestions == second.suggestions
    assert first.scorer_version == second.scorer_version

    # Repeated runs on the SAME stack are identical too — the weaker but
    # still required reading of "generating twice produces identical output".
    again = _run_pipeline(_SERVICE_A, _SCORER_A, resume_text, job_description)
    assert again == first
