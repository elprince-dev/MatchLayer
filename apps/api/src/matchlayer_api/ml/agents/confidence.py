"""The Confidence_Level rule — the ATS_Agent's deterministic score-confidence tag.

Phase 4 design decision D8: the Confidence_Level is a documented, data-driven
rule implemented as a pure function so it is trivially property-testable
(design Property 6) and reproducible from persisted trace data alone. The
ATS_Agent (``ml/agents/ats_agent.py``) attaches the returned level to every
score it writes to Agent_State; the human-readable statement of the rule lives
in ``docs/agent-rules.md``.

Rule (Requirements 4.2, 4.5 — inputs are limited to observable values):

* ``high``   — semantic scoring produced the score AND both text lengths fall
  within bounds (``200 <= resume_len <= 50_000`` and
  ``100 <= jd_len <= 20_000``).
* ``medium`` — exactly one of {semantic scoring, both-lengths-in-bounds}
  holds.
* ``low``    — neither holds.

Consequences baked into the shape of the rule:

* The function is **total**: every ``(bool, int, int)`` input maps to exactly
  one of the three levels, and identical inputs always yield the identical
  level (Requirement 4.2 — no randomness, no clocks, no I/O).
* A score produced by a Degraded_Mode fallback scorer (``semantic=False``)
  can never be tagged ``high`` (Requirement 4.5): ``high`` requires
  ``semantic`` to hold.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "JD_LEN_MAX",
    "JD_LEN_MIN",
    "RESUME_LEN_MAX",
    "RESUME_LEN_MIN",
    "confidence_level",
]

#: Inclusive resume-text length bounds (characters) for the in-bounds check.
RESUME_LEN_MIN = 200
RESUME_LEN_MAX = 50_000

#: Inclusive job-description length bounds (characters) for the in-bounds check.
JD_LEN_MIN = 100
JD_LEN_MAX = 20_000


def confidence_level(
    semantic: bool, resume_len: int, jd_len: int
) -> Literal["high", "medium", "low"]:
    """Derive the Confidence_Level for an ATS score.

    Pure and deterministic: the result depends only on the three arguments
    (Requirement 4.2). ``semantic=False`` (a Degraded_Mode fallback scorer
    produced the score) structurally excludes ``high`` (Requirement 4.5).

    Args:
        semantic: ``True`` when semantic scoring produced the score,
            ``False`` when a Degraded_Mode fallback scorer did.
        resume_len: Length of the resume text in characters.
        jd_len: Length of the job-description text in characters.

    Returns:
        Exactly one of ``"high"``, ``"medium"``, or ``"low"``.
    """
    lengths_in_bounds = (
        RESUME_LEN_MIN <= resume_len <= RESUME_LEN_MAX and JD_LEN_MIN <= jd_len <= JD_LEN_MAX
    )
    if semantic and lengths_in_bounds:
        return "high"
    if semantic != lengths_in_bounds:  # exactly one of the two signals holds
        return "medium"
    return "low"
