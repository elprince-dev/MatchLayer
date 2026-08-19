"""Feature: phase-4-agentic — Property 6.

Property 6: Confidence rule is total, deterministic, and caps fallback scores.

    *For any* combination of scoring mode (semantic or Degraded_Mode
    fallback) and non-negative resume/JD text lengths, the confidence rule
    returns exactly one of ``high``/``medium``/``low``, repeated evaluation
    on identical inputs returns the identical level, and whenever the score
    was produced by a fallback scorer the result is never ``high``.

**Validates: Requirements 4.2, 4.5**

The rule under test is :func:`matchlayer_api.ml.agents.confidence.confidence_level`
(design decision D8) — a pure function over ``(semantic, resume_len, jd_len)``.
Requirement 4.2 demands totality and determinism (no randomness, clocks, or
I/O); Requirement 4.5 demands that a Degraded_Mode fallback score
(``semantic=False``) can never be tagged ``high``.

The length generator is boundary-aware: alongside arbitrary non-negative
integers it anchors on the exported inclusive bounds (``RESUME_LEN_MIN/MAX``,
``JD_LEN_MIN/MAX``) and their off-by-one neighbours, so every run exercises
the in-bounds/out-of-bounds transitions where a totality or cap bug would
hide. The confidence module is framework-free: this test imports it directly
and touches no settings, FastAPI, or database.
"""

# Feature: phase-4-agentic, Property 6: Confidence rule is total, deterministic, and caps fallback scores  # noqa: E501

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.ml.agents.confidence import (
    JD_LEN_MAX,
    JD_LEN_MIN,
    RESUME_LEN_MAX,
    RESUME_LEN_MIN,
    confidence_level,
)

_VALID_LEVELS = frozenset({"high", "medium", "low"})

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Non-negative lengths, anchored on the rule's inclusive bounds and their
# off-by-one neighbours so boundary transitions are always exercised.
_RESUME_ANCHORS = [
    0,
    RESUME_LEN_MIN - 1,
    RESUME_LEN_MIN,
    RESUME_LEN_MAX,
    RESUME_LEN_MAX + 1,
]
_JD_ANCHORS = [0, JD_LEN_MIN - 1, JD_LEN_MIN, JD_LEN_MAX, JD_LEN_MAX + 1]

_resume_len = st.one_of(
    st.sampled_from(_RESUME_ANCHORS),
    st.integers(min_value=0, max_value=2 * RESUME_LEN_MAX),
)
_jd_len = st.one_of(
    st.sampled_from(_JD_ANCHORS),
    st.integers(min_value=0, max_value=2 * JD_LEN_MAX),
)

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 6: Confidence rule is total, deterministic, and caps fallback scores  # noqa: E501
@settings(max_examples=200, deadline=None)
@example(semantic=True, resume_len=RESUME_LEN_MIN, jd_len=JD_LEN_MIN)
@example(semantic=True, resume_len=RESUME_LEN_MAX, jd_len=JD_LEN_MAX)
@example(semantic=False, resume_len=RESUME_LEN_MIN, jd_len=JD_LEN_MIN)
@example(semantic=False, resume_len=0, jd_len=0)
@given(semantic=st.booleans(), resume_len=_resume_len, jd_len=_jd_len)
def test_confidence_rule_is_total_and_deterministic(
    semantic: bool, resume_len: int, jd_len: int
) -> None:
    """Totality + determinism (Requirement 4.2): every ``(bool, int, int)``
    input maps to exactly one of the three levels, and repeated evaluation
    on identical inputs returns the identical level."""
    first = confidence_level(semantic, resume_len, jd_len)

    assert first in _VALID_LEVELS
    assert confidence_level(semantic, resume_len, jd_len) == first


# Feature: phase-4-agentic, Property 6: Confidence rule is total, deterministic, and caps fallback scores  # noqa: E501
@settings(max_examples=200, deadline=None)
@example(resume_len=RESUME_LEN_MIN, jd_len=JD_LEN_MIN)
@example(resume_len=RESUME_LEN_MAX, jd_len=JD_LEN_MAX)
@given(resume_len=_resume_len, jd_len=_jd_len)
def test_fallback_scores_are_never_high(resume_len: int, jd_len: int) -> None:
    """Fallback cap (Requirement 4.5): a score produced by a Degraded_Mode
    fallback scorer (``semantic=False``) is never tagged ``high``, even when
    both text lengths fall within bounds (the hard case, pinned by the
    explicit examples above)."""
    assert confidence_level(False, resume_len, jd_len) != "high"
