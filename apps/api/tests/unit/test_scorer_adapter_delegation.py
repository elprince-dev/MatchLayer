"""Adapter-delegation tests for the ``ml/`` scorer adapter (phase-1-matching, task 5.2).

The companion static checks in ``test_import_boundaries.py`` prove the
*structural* half of Requirement 10.2 — the adapter imports no scikit-learn and
its module body contains no arithmetic operators. This module proves the
*behavioral* half: at runtime the adapter is a pure pass-through to the
framework-free :class:`~matchlayer_api.scoring.scorer.Match_Scorer`.

What "delegates to the scorer without computing" means here, made testable:

* :func:`~matchlayer_api.ml.scorer_adapter.get_scorer` returns a real
  :class:`Match_Scorer` (the bridge object) and caches it, so the API holds one
  process-wide scorer bound to the committed :class:`Skill_Lexicon` and the
  configured weights/caps.
* :func:`~matchlayer_api.ml.scorer_adapter.score` forwards its two string
  arguments to that scorer's :meth:`Match_Scorer.score` **in order and
  unchanged**, and returns the scorer's :class:`ScoreResult` **by identity** —
  it neither reshapes the inputs nor rebuilds the output. A spy scorer records
  the call and hands back a sentinel result; the adapter returning that exact
  sentinel is what shows it added no computation of its own.

Design reference: design.md "ml/ adapter". Validates: Requirements 10.2 (and the
import direction noted in 10.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from matchlayer_api import ml
from matchlayer_api.ml import scorer_adapter
from matchlayer_api.scoring.lexicon import load_lexicon
from matchlayer_api.scoring.scorer import Match_Scorer, ScoreResult


@pytest.fixture(autouse=True)
def _clear_scorer_cache() -> Any:
    """Reset the ``get_scorer`` lru_cache around each test.

    ``get_scorer`` is ``@lru_cache(maxsize=1)``. Clearing before and after keeps
    a scorer built here (bound to real settings) from leaking into a sibling
    test, and guarantees each test that calls ``get_scorer()`` exercises a fresh
    build rather than a cache entry left by an earlier test.
    """
    scorer_adapter.get_scorer.cache_clear()
    yield
    scorer_adapter.get_scorer.cache_clear()


# ---------------------------------------------------------------------------
# Spy scorer — records the delegated call, returns a sentinel result.
# ---------------------------------------------------------------------------


@dataclass
class _SpyScorer:
    """A stand-in for ``Match_Scorer`` that records one ``score`` call.

    ``score`` returns the pre-built ``sentinel`` :class:`ScoreResult` and stores
    the exact arguments it was handed, so the test can assert the adapter
    forwarded them verbatim and returned the sentinel unchanged (no reshaping).
    """

    sentinel: ScoreResult
    calls: list[tuple[str, str]]

    def score(self, resume_text: str, job_description: str) -> ScoreResult:
        self.calls.append((resume_text, job_description))
        return self.sentinel


def _make_sentinel_result() -> ScoreResult:
    """A distinctive :class:`ScoreResult` the adapter must return by identity."""
    from matchlayer_api.scoring.scorer import ScoreBreakdown

    return ScoreResult(
        score=73,
        breakdown=ScoreBreakdown(
            similarity_component=0.5,
            keyword_coverage_component=0.25,
            weight_similarity=0.6,
            weight_keyword=0.4,
            final_score=73,
        ),
        matched_keywords=[],
        missing_keywords=[],
        suggestions=[],
        scorer_version="sentinel+lex.test",
    )


# ---------------------------------------------------------------------------
# get_scorer: the cached bridge object
# ---------------------------------------------------------------------------


def test_get_scorer_returns_a_match_scorer_bound_to_the_real_lexicon() -> None:
    """``get_scorer()`` hands back a real ``Match_Scorer`` stamped by the lexicon.

    The adapter's only job on construction is to wire the loaded lexicon and the
    configured knobs into a ``Match_Scorer`` — so the returned object is exactly
    that type, and its ``scorer_version`` is the one the committed
    :class:`Skill_Lexicon` composes (no version invented by the adapter).

    Validates: Requirement 10.2.
    """
    scorer = scorer_adapter.get_scorer()
    assert isinstance(scorer, Match_Scorer)
    assert scorer.scorer_version == load_lexicon().scorer_version


def test_get_scorer_is_cached_process_wide() -> None:
    """Repeated ``get_scorer()`` calls return the same cached instance.

    The adapter builds the scorer once (``@lru_cache(maxsize=1)``) and reuses it
    across requests, rather than reconstructing per call.

    Validates: Requirement 10.2.
    """
    assert scorer_adapter.get_scorer() is scorer_adapter.get_scorer()


# ---------------------------------------------------------------------------
# score: pure delegation
# ---------------------------------------------------------------------------


def test_score_delegates_to_the_cached_scorer_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """``score`` forwards its args verbatim and returns the scorer's result by identity.

    With ``get_scorer`` replaced by a spy, the adapter's ``score`` must call the
    spy's ``score`` with the same two strings, in order, and return precisely the
    spy's sentinel result. Returning the sentinel object *by identity* (``is``)
    is the observable proof that the adapter performed no arithmetic, no
    normalization, and no result reshaping of its own — it is a pure bridge
    (Requirement 10.2).
    """
    sentinel = _make_sentinel_result()
    spy = _SpyScorer(sentinel=sentinel, calls=[])
    monkeypatch.setattr(scorer_adapter, "get_scorer", lambda: spy)

    resume_text = "experienced python developer who has shipped fastapi services"
    job_description = "looking for a python engineer with fastapi and postgres"

    result = scorer_adapter.score(resume_text, job_description)

    # Returned by identity — not a copy, not a reshaped value.
    assert result is sentinel
    # Forwarded exactly once, with the two strings in order and unchanged.
    assert spy.calls == [(resume_text, job_description)]


def test_score_preserves_argument_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first positional arg is the resume text, the second the job description.

    Guards against a silent transposition in the adapter: the spy records the
    pair it was handed, and the test asserts the resume text and JD land in the
    documented positions (``score(resume_text, job_description)``).

    Validates: Requirement 10.2.
    """
    spy = _SpyScorer(sentinel=_make_sentinel_result(), calls=[])
    monkeypatch.setattr(scorer_adapter, "get_scorer", lambda: spy)

    scorer_adapter.score("RESUME-TEXT", "JD-TEXT")

    assert spy.calls == [("RESUME-TEXT", "JD-TEXT")]


def test_adapter_reexports_from_scoring_not_the_reverse() -> None:
    """The adapter's public surface is sourced from ``matchlayer_api.scoring``.

    The dependency direction is one-way (Requirement 10.1, 10.2): the ``ml``
    adapter imports the scorer type and result dataclass from the framework-free
    scoring package. This sanity check pins that the symbols the adapter exposes
    are the scoring package's own types.
    """
    assert ml.scorer_adapter.ScoreResult is ScoreResult
    assert ml.scorer_adapter.Match_Scorer is Match_Scorer
