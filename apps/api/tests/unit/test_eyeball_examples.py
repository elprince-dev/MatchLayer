"""Eyeball-example unit tests for the Match_Scorer (task 4.16).

Concrete, hand-picked resume/job-description pairs that pin the
:class:`~matchlayer_api.scoring.scorer.Match_Scorer`'s *observable* behavior on
realistic input. Where the property tests (tasks 4.11-4.15) assert the universal
invariants across a generated input space, these examples sanity-check the score
on three deliberately chosen scenarios drawn from the committed eyeball dataset
(``ml/evals/datasets/eyeball/``, reused per the design's Testing Strategy):

* **Strong match** — a backend-engineer resume that genuinely covers every key
  skill in the JD. The naive Phase 1 scorer should rate it highly and report the
  JD's key skills as *matched* (Requirements 5.1, 5.2, 5.3).
* **Clear mismatch** — an unrelated (pastry-chef) resume against the same JD.
  The score should be low and the JD's key skills should be reported as
  *missing*.
* **Keyword-stuffed adversarial** — a resume that merely lists the JD's keywords
  with no real prose. Phase 1 is *deliberately* a naive TF-IDF-plus-keyword
  scorer (``product.md`` "infrastructure before intelligence"; Requirement 5.8
  forbids any LLM/embedding) with **no anti-stuffing defense**. So this test
  documents and asserts the scorer's *actual* Phase 1 behavior — stuffing the
  lexicon keywords genuinely raises the keyword-coverage component and the
  score — rather than asserting a defense the scorer does not yet have. Semantic
  resistance to keyword-stuffing is a Phase 2+ concern (embeddings), explicitly
  out of scope here.

Assertions are phrased as ranges, inequalities, and membership rather than
exact-equality on the integer score, so they stay robust to small,
behavior-preserving changes in the algorithm while still catching a real
regression. The scorer is deterministic for a fixed algorithm + lexicon version
(Requirement 5.4), so the relative orderings asserted here are stable.

References: Requirements 5.1, 5.2, 5.3; design §"Testing Strategy" → Unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer, ScoreResult

# ---------------------------------------------------------------------------
# Scorer under test
# ---------------------------------------------------------------------------

# Production defaults: weights 0.6/0.4 (sum to 1.0 per Requirement 5.3) and the
# default keyword/suggestion caps. The committed runtime lexicon is the real
# vocabulary the scorer matches against.
_LEXICON: Skill_Lexicon = load_lexicon()
_W_SIMILARITY = 0.6
_W_KEYWORD = 0.4
_MAX_KEYWORDS = 50
_MAX_SUGGESTIONS = 10


@pytest.fixture(scope="module")
def scorer() -> Match_Scorer:
    """A Match_Scorer wired with the production-default weights and caps."""
    return Match_Scorer(
        _LEXICON,
        w_similarity=_W_SIMILARITY,
        w_keyword=_W_KEYWORD,
        max_keywords=_MAX_KEYWORDS,
        max_suggestions=_MAX_SUGGESTIONS,
    )


# ---------------------------------------------------------------------------
# Eyeball dataset loading (ml/evals/datasets/eyeball/)
# ---------------------------------------------------------------------------


def _eyeball_dir() -> Path:
    """Locate the committed ``ml/evals/datasets/eyeball/`` directory.

    Walks up from this test file until it finds the repo-level ``ml`` tree, so
    the test is insensitive to the exact monorepo depth.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "ml" / "evals" / "datasets" / "eyeball"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("could not locate ml/evals/datasets/eyeball/")


def _load_example(name: str) -> dict[str, Any]:
    """Load and minimally validate one committed eyeball example pair."""
    path = _eyeball_dir() / name
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} must be a JSON object"
    for key in ("resume", "job_description", "jd_key_skills"):
        assert key in data, f"{name} is missing required key {key!r}"
    return data


def _terms(keywords: list[Any]) -> set[str]:
    """The set of normalized terms in a matched/missing keyword list."""
    return {kw.term for kw in keywords}


def _recompute_score(result: ScoreResult) -> int:
    """Re-derive the integer score from the breakdown (Requirement 5.3 formula)."""
    b = result.breakdown
    weighted = b.weight_similarity * b.similarity_component + b.weight_keyword * (
        b.keyword_coverage_component
    )
    return max(0, min(100, round(100 * weighted)))


# ---------------------------------------------------------------------------
# Strong match — high score, JD key skills are matched (5.1, 5.2, 5.3)
# ---------------------------------------------------------------------------


def test_strong_match_scores_high_and_matches_key_skills(scorer: Match_Scorer) -> None:
    """A resume that genuinely fits the JD scores highly and covers its key skills."""
    example = _load_example("strong_match.json")
    result = scorer.score(example["resume"], example["job_description"])

    # The score is a bounded integer (Requirement 5.1, 5.3).
    assert isinstance(result.score, int) and not isinstance(result.score, bool)
    assert 0 <= result.score <= 100

    # A genuine, well-targeted resume clears a comfortable lower bound. The
    # observed score for this pair is well above this floor; the margin keeps
    # the assertion robust to small algorithm tweaks while still catching a
    # regression that would collapse a strong match to a mediocre score.
    assert result.score >= 45, f"strong match scored unexpectedly low: {result.score}"

    # Every key JD skill is reported as matched (Requirement 5.2 coverage half).
    matched_terms = _terms(result.matched_keywords)
    for skill in example["jd_key_skills"]:
        assert skill in matched_terms, f"expected key skill {skill!r} in matched_keywords"

    # Both scoring components carry real signal for a true match (Requirement
    # 5.1 similarity, 5.2 keyword coverage).
    assert result.breakdown.similarity_component > 0.0
    assert result.breakdown.keyword_coverage_component > 0.5


# ---------------------------------------------------------------------------
# Clear mismatch — low score, JD key skills are missing (5.1, 5.2, 5.3)
# ---------------------------------------------------------------------------


def test_clear_mismatch_scores_low_and_misses_key_skills(scorer: Match_Scorer) -> None:
    """An unrelated resume scores low and reports the JD's key skills as missing."""
    example = _load_example("clear_mismatch.json")
    result = scorer.score(example["resume"], example["job_description"])

    assert isinstance(result.score, int) and not isinstance(result.score, bool)
    assert 0 <= result.score <= 100

    # An unrelated resume stays under a sensible upper bound. The observed score
    # sits well below this ceiling; the margin avoids brittleness.
    assert result.score <= 30, f"clear mismatch scored unexpectedly high: {result.score}"

    # None of the JD's key technical skills are present, so each is reported as
    # missing (Requirement 5.2 coverage half).
    missing_terms = _terms(result.missing_keywords)
    for skill in example["jd_key_skills"]:
        assert skill in missing_terms, f"expected key skill {skill!r} in missing_keywords"


# ---------------------------------------------------------------------------
# Keyword-stuffed adversarial — documents the naive Phase 1 behavior (5.2, 5.3)
# ---------------------------------------------------------------------------


def test_keyword_stuffing_raises_coverage_as_designed(scorer: Match_Scorer) -> None:
    """A keyword-stuffed resume scores via coverage — the intended naive behavior.

    Phase 1 has no semantic anti-stuffing defense (Requirement 5.8 forbids
    embeddings/LLMs), so a resume that simply lists the JD's lexicon skills has
    those skills counted as matched and earns a non-trivial keyword-coverage
    component. This test asserts that *actual* behavior rather than a defense
    the scorer does not have; resistance to stuffing is a Phase 2+ concern.
    """
    example = _load_example("keyword_stuffed.json")
    result = scorer.score(example["resume"], example["job_description"])

    assert isinstance(result.score, int) and not isinstance(result.score, bool)
    assert 0 <= result.score <= 100

    # The stuffed lexicon skills ARE counted as matched (no anti-stuffing
    # defense in Phase 1).
    matched_terms = _terms(result.matched_keywords)
    for skill in example["jd_key_skills"]:
        assert skill in matched_terms, f"stuffed skill {skill!r} should count as matched"

    # Stuffing genuinely lifts the keyword-coverage component above zero
    # (Requirement 5.2): coverage equals the matched fraction of the analyzed
    # set, which is non-empty here.
    assert result.breakdown.keyword_coverage_component > 0.0


# ---------------------------------------------------------------------------
# Cross-pair sanity: the relative ordering the design implies (5.2, 5.3)
# ---------------------------------------------------------------------------


def test_relative_ordering_strong_beats_stuffed_beats_mismatch(scorer: Match_Scorer) -> None:
    """Strong match > keyword-stuffed > clear mismatch.

    Two robust, behavior-level orderings that do not depend on exact scores:

    * A genuine match (high similarity *and* high coverage) outscores a
      keyword-stuffed resume, which earns coverage but little real similarity.
    * A keyword-stuffed resume still outscores a clearly-unrelated resume,
      because the stuffed keywords raise its coverage component — the known,
      intended limitation of the naive Phase 1 scorer (Requirements 5.2, 5.3).
    """
    strong = _load_example("strong_match.json")
    stuffed = _load_example("keyword_stuffed.json")
    mismatch = _load_example("clear_mismatch.json")

    strong_score = scorer.score(strong["resume"], strong["job_description"]).score
    stuffed_score = scorer.score(stuffed["resume"], stuffed["job_description"]).score
    mismatch_score = scorer.score(mismatch["resume"], mismatch["job_description"]).score

    assert strong_score > stuffed_score > mismatch_score, (
        f"unexpected ordering: strong={strong_score}, "
        f"stuffed={stuffed_score}, mismatch={mismatch_score}"
    )


# ---------------------------------------------------------------------------
# Breakdown explainability across all three pairs (5.3, 5.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["strong_match.json", "clear_mismatch.json", "keyword_stuffed.json"],
)
def test_breakdown_recomputes_to_the_reported_score(scorer: Match_Scorer, filename: str) -> None:
    """The weighted combine in the breakdown re-derives the reported score (5.3).

    The score is ``round(100 * (w_similarity * similarity + w_keyword *
    coverage))`` clamped to ``[0, 100]``. Recomputing it from the breakdown on
    each real pair confirms the documented weighting (Requirement 5.3) and that
    the breakdown is genuinely explainable (Requirement 5.5) — and that
    coverage equals the matched fraction of the analyzed set (Requirement 5.2).
    """
    example = _load_example(filename)
    result = scorer.score(example["resume"], example["job_description"])

    # Requirement 5.3: the breakdown re-derives the final score.
    assert _recompute_score(result) == result.score
    assert result.breakdown.final_score == result.score

    # Requirement 5.2: coverage component == |matched| / |analyzed|.
    analyzed = len(result.matched_keywords) + len(result.missing_keywords)
    if analyzed:
        expected_coverage = len(result.matched_keywords) / analyzed
        assert result.breakdown.keyword_coverage_component == pytest.approx(expected_coverage)
