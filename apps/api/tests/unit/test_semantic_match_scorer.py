"""Unit tests for the Phase 2 Semantic_Match_Scorer (task 7.1).

Concrete examples over a small synthetic lexicon, ``spacy.blank("en")``, and
hand-picked embedding vectors: the documented weighted combination and
breakdown shape (Req 3.2, 3.3), the empty-after-normalization contract
(Req 3.5), the zero-similarity non-suppression rule (Req 3.6), the coverage
definition (Req 4.6), the ``EmptyAnalyzedSetError`` signal (Req 4.10),
``EmbeddingGeometryError`` propagation (Req 3.10), suggestion continuity
(Req 8.1), and the additive ``similarity_method`` field on both scorers
(Req 9.5). The exhaustive universal properties are the property tests of
tasks 7.2 through 7.5; edge-case unit tests are task 7.6.
"""

from __future__ import annotations

import pytest
import spacy

from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.scorer import (
    EmptyAnalyzedSetError,
    Match_Scorer,
    Semantic_Match_Scorer,
)
from matchlayer_api.scoring.semantic import EmbeddingGeometryError, Semantic_Scorer
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_W_SIMILARITY = 0.6
_W_KEYWORD = 0.4
_SCORER_VERSION = "v2:lex=test:model=stub@rev:spacy=blank_en@0"


def _make_lexicon() -> Skill_Lexicon:
    return Skill_Lexicon(
        {
            "schema_version": 1,
            "lexicon_version": "test",
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
            ],
        }
    )


@pytest.fixture(scope="module")
def scorer() -> Semantic_Match_Scorer:
    lexicon = _make_lexicon()
    return Semantic_Match_Scorer(
        lexicon,
        Skill_Extractor(spacy.blank("en"), lexicon, max_keywords=50),
        Semantic_Scorer(),
        w_similarity=_W_SIMILARITY,
        w_keyword=_W_KEYWORD,
        max_suggestions=10,
        scorer_version=_SCORER_VERSION,
    )


# ---------------------------------------------------------------------------
# Composition: formula, breakdown, partition, suggestions (Req 3.2, 3.3, 8.1)
# ---------------------------------------------------------------------------


class TestComposition:
    def test_score_follows_the_documented_formula(self, scorer: Semantic_Match_Scorer) -> None:
        # Identical unit vectors → cosine 1 → similarity component 1.0.
        # JD analyzes {python, java}; resume covers python → coverage 0.5.
        # final = round(100 * (0.6 * 1.0 + 0.4 * 0.5)) = 80.
        result = scorer.score(
            "I write python daily",
            "need python and java",
            [1.0, 0.0],
            [1.0, 0.0],
        )
        assert result.score == 80
        assert result.breakdown.similarity_component == 1.0
        assert result.breakdown.keyword_coverage_component == 0.5
        assert result.breakdown.weight_similarity == _W_SIMILARITY
        assert result.breakdown.weight_keyword == _W_KEYWORD
        assert result.breakdown.final_score == result.score
        assert result.breakdown.similarity_method == "semantic-embedding"
        assert result.scorer_version == _SCORER_VERSION

    def test_matched_missing_partition_and_suggestions(self, scorer: Semantic_Match_Scorer) -> None:
        result = scorer.score(
            "shipped py services",
            "need python and java",
            [1.0, 0.0],
            [1.0, 0.0],
        )
        assert [k.term for k in result.matched_keywords] == ["python"]
        assert [k.term for k in result.missing_keywords] == ["java"]
        # Phase 1 Suggestion_Generator over `missing` (Req 8.1): one
        # suggestion referencing exactly the missing skill.
        assert [s.keyword for s in result.suggestions] == ["java"]

    def test_zero_similarity_does_not_suppress_coverage(
        self, scorer: Semantic_Match_Scorer
    ) -> None:
        # Opposite unit vectors → cosine -1 → similarity component 0.0.
        # Resume covers everything analyzed → coverage 1.0.
        # final = round(100 * (0.6 * 0.0 + 0.4 * 1.0)) = 40 (Req 3.6).
        result = scorer.score(
            "python and java expert",
            "need python and java",
            [1.0, 0.0],
            [-1.0, 0.0],
        )
        assert result.score == 40
        assert result.breakdown.similarity_component == 0.0
        assert result.breakdown.keyword_coverage_component == 1.0
        # Empty missing set → exactly one affirmative suggestion (Req 8.3
        # continuity via the reused Phase 1 generator).
        assert len(result.suggestions) == 1
        assert result.suggestions[0].keyword == ""


# ---------------------------------------------------------------------------
# Empty inputs (Req 3.5) and error signals (Req 3.10, 4.10)
# ---------------------------------------------------------------------------


class TestEmptyAndErrors:
    @pytest.mark.parametrize(
        ("resume_text", "job_description"),
        [
            ("", "need python"),
            ("python expert", "   \n\t"),
            ("", ""),
        ],
    )
    def test_empty_after_normalization_scores_zero_without_error(
        self, scorer: Semantic_Match_Scorer, resume_text: str, job_description: str
    ) -> None:
        # Zero-magnitude embeddings would raise EmbeddingGeometryError if the
        # similarity path ran — proving the empty check comes first and the
        # call never raises (Req 3.5).
        result = scorer.score(resume_text, job_description, [0.0, 0.0], [0.0, 0.0])
        assert result.score == 0
        assert result.breakdown.similarity_component == 0.0
        assert result.breakdown.keyword_coverage_component == 0.0

    def test_empty_analyzed_set_from_non_empty_jd_raises(
        self, scorer: Semantic_Match_Scorer
    ) -> None:
        # Both texts non-empty, but the JD contains no lexicon skill →
        # EmptyAnalyzedSetError so the service applies the Req 4.10 fallback.
        with pytest.raises(EmptyAnalyzedSetError):
            scorer.score(
                "python expert",
                "great team and selection process",
                [1.0, 0.0],
                [1.0, 0.0],
            )

    def test_embedding_geometry_error_propagates(self, scorer: Semantic_Match_Scorer) -> None:
        # Mismatched dimensions → the Semantic_Scorer's error propagates to
        # the caller unchanged (Req 3.10).
        with pytest.raises(EmbeddingGeometryError):
            scorer.score("python expert", "need python", [1.0, 0.0], [1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# Phase 1 continuity: additive similarity_method only (Req 9.5)
# ---------------------------------------------------------------------------


class TestPhase1Continuity:
    def test_phase1_scorer_populates_tfidf_method(self) -> None:
        phase1 = Match_Scorer(
            _make_lexicon(),
            w_similarity=_W_SIMILARITY,
            w_keyword=_W_KEYWORD,
            max_keywords=50,
            max_suggestions=10,
        )
        result = phase1.score("python developer", "need python and java")
        assert result.breakdown.similarity_method == "tfidf"
