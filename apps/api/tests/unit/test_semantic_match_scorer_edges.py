"""Edge-behavior unit tests for the Phase 2 Semantic_Match_Scorer (task 7.6).

Deterministic examples over a small synthetic lexicon, ``spacy.blank("en")``,
and hand-picked embedding vectors, complementing the task 7.1 composition
tests and the tasks 7.2-7.5 property tests:

* ``EmptyAnalyzedSetError`` is raised for a non-empty JD whose analyzed skill
  set is empty, and it is catchable as ``ValueError`` — the documented
  contract the Scoring_Service's Requirement 4.10 fallback relies on
  (Requirement 4.10).
* An empty ``missing_keywords`` set produces exactly one affirmative
  suggestion, with the affirmative shape (empty ``keyword``, non-empty
  user-facing ``text``) rather than an unexplained empty list
  (Requirement 8.3).
* A zero similarity component with a non-zero coverage component still yields
  the full weighted combination ``round(100 * w_kw * coverage)`` — verified
  with a *partial* coverage value so the test distinguishes the documented
  formula from a mere non-zero result (Requirement 3.6).
"""

from __future__ import annotations

import pytest
import spacy

from matchlayer_api.scoring.lexicon import Skill_Lexicon
from matchlayer_api.scoring.scorer import (
    EmptyAnalyzedSetError,
    Semantic_Match_Scorer,
)
from matchlayer_api.scoring.semantic import Semantic_Scorer
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
# Empty analyzed set from a non-empty JD (Requirement 4.10)
# ---------------------------------------------------------------------------


class TestEmptyAnalyzedSet:
    def test_non_empty_jd_with_no_lexicon_skill_raises(self, scorer: Semantic_Match_Scorer) -> None:
        """A non-empty JD mentioning no lexicon skill raises the fallback signal.

        Validates: Requirements 4.10
        """
        with pytest.raises(EmptyAnalyzedSetError):
            scorer.score(
                "python and java expert",
                "join our fast growing team with a rigorous selection process",
                [1.0, 0.0],
                [1.0, 0.0],
            )

    def test_error_is_catchable_as_value_error(self, scorer: Semantic_Match_Scorer) -> None:
        """The signal is a ValueError subclass, the documented caller contract.

        Validates: Requirements 4.10
        """
        with pytest.raises(ValueError, match="empty analyzed skill set"):
            scorer.score(
                "python and java expert",
                "join our fast growing team with a rigorous selection process",
                [1.0, 0.0],
                [1.0, 0.0],
            )


# ---------------------------------------------------------------------------
# Empty missing set → exactly one affirmative suggestion (Requirement 8.3)
# ---------------------------------------------------------------------------


class TestAffirmativeSuggestion:
    def test_full_coverage_yields_exactly_one_affirmative_suggestion(
        self, scorer: Semantic_Match_Scorer
    ) -> None:
        """A resume covering every analyzed skill gets one affirmative suggestion.

        Validates: Requirements 8.3
        """
        result = scorer.score(
            "python and java expert",
            "need python and java",
            [1.0, 0.0],
            [1.0, 0.0],
        )
        assert result.missing_keywords == []
        assert len(result.suggestions) == 1
        affirmative = result.suggestions[0]
        # The affirmative shape: no missing term addressed, non-empty
        # user-facing guidance — never an unexplained empty list.
        assert affirmative.keyword == ""
        assert affirmative.text.strip()


# ---------------------------------------------------------------------------
# Zero similarity with non-zero coverage (Requirement 3.6)
# ---------------------------------------------------------------------------


class TestZeroSimilarityWeightedCombination:
    def test_partial_coverage_yields_the_weighted_combination(
        self, scorer: Semantic_Match_Scorer
    ) -> None:
        """Zero similarity with partial coverage produces round(100 * w_kw * cov).

        Opposite unit vectors → cosine -1 → similarity component (cos+1)/2 = 0.
        JD analyzes {python, java}; the resume covers python only → coverage
        0.5. final = round(100 * (0.6 * 0.0 + 0.4 * 0.5)) = 20 — the exact
        weighted combination, not merely a non-zero score.

        Validates: Requirements 3.6
        """
        result = scorer.score(
            "I ship python services",
            "need python and java",
            [1.0, 0.0],
            [-1.0, 0.0],
        )
        assert result.breakdown.similarity_component == 0.0
        assert result.breakdown.keyword_coverage_component == 0.5
        assert result.score == 20
        assert result.breakdown.final_score == 20
