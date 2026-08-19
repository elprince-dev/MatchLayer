"""The skill-gap agent — deterministic gap analysis (phase-4-agentic).

:class:`SkillGapAgent` is the deterministic node that turns the upstream
Candidate_Profile, the persisted Match_Result skill analysis, and the
Job_Description's extracted skills into a prioritized
:class:`~matchlayer_api.ml.agents.state.SkillGapReport` (design section
"3. The five concrete agents"). It extends
:class:`~matchlayer_api.ml.agents.deterministic_agent.DeterministicAgent`
— no LLM dependency by construction (Requirement 1.6) — and holds no
collaborators at all: its constructor is exactly ``BaseAgent.__init__``.

Pure function over exactly three inputs (Requirement 5.1)
---------------------------------------------------------

``run`` reads only ``state.candidate_profile``, the
``state.match_snapshot`` matched skills, and
``state.job_description_skills``, and delegates every rule to the pure
free functions in :mod:`matchlayer_api.ml.agents.gap_rules`
(classification: Requirement 5.1; prioritization + ranks: Requirement
5.2; both documented in ``docs/agent-rules.md``). No I/O, no clock, no
randomness — field-for-field identical inputs yield field-for-field
identical reports, including ordering and ranks (Requirement 5.3).

Degraded *input* is not degraded *output* (Requirement 5.4): when the
upstream Candidate_Profile is itself a Degraded_Output
(``profile.degraded``), the report is derived from the Match_Result's
persisted skill analysis alone — the degraded profile's skill list is
untrusted and ignored, so only the matched skills count as coverage —
and the report carries ``derived_from_degraded_input=True`` with
``degraded=False``. The classification and prioritization rules still
apply.

An empty gap list — every JD skill covered — is a **valid** result,
never a failure and never a route to the degraded path (Requirement
5.7): ``run`` simply returns a schema-valid report with ``gaps=[]``.

Degradation (Requirement 5.5): if ``run`` raises (e.g. the worker failed
to load the snapshot or profile), exceeds the per-node timeout, or its
output fails schema validation, ``BaseAgent.__call__`` routes to
:meth:`SkillGapAgent.build_degraded` — the persisted missing skills, each
classified ``missing``, ranked sequentially from 1 **in persisted order
without the prioritization rule**, marked ``degraded=True`` — and the
Agent_Graph continues.

Output (Requirement 5.6): every completion — normal, from degraded
input, or degraded — writes a schema-valid ``SkillGapReport`` to the
``skill_gap_report`` state field, where the Synthesizer consumes it.

Import discipline: like its base module, this module imports nothing
from ``ml/llm/`` or ``services/llm/`` — the structural half of
Requirement 1.6.
"""

from __future__ import annotations

from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.gap_rules import build_skill_gap_entries
from matchlayer_api.ml.agents.state import (
    AgentState,
    CandidateProfile,
    MatchSnapshot,
    SkillGapEntry,
    SkillGapReport,
)

__all__ = ["SkillGapAgent"]


class SkillGapAgent(DeterministicAgent[SkillGapReport]):
    """Deterministic skill-gap node (design "3. The five concrete agents").

    ``run`` is a pure function of the Candidate_Profile, the Match_Result
    skill snapshot, and the JD skill list, composed entirely from the
    ``gap_rules`` free functions (Requirements 5.1-5.3). Timeout,
    degradation, Agent_Run persistence, and span emission are inherited
    unchanged from ``BaseAgent.__call__``.
    """

    name = "skill_gap"
    output_field = "skill_gap_report"

    # ---- pure agent logic --------------------------------------------------

    async def run(self, state: AgentState) -> SkillGapReport:
        """Classify and prioritize every JD skill into a ranked report.

        Raises on missing inputs (snapshot or profile not loaded into
        state) so ``BaseAgent.__call__`` classifies the failure and routes
        to :meth:`build_degraded` (Requirement 5.5).
        """
        snapshot = _require_snapshot(state)
        profile = _require_profile(state)
        if profile.degraded:
            # Requirement 5.4: degraded profile → Match_Result skill
            # analysis alone. The profile's skill list is untrusted, so
            # only matched skills count as coverage — uncovered JD skills
            # are all `missing`. Rules still apply; report is NOT degraded.
            gaps = build_skill_gap_entries(
                jd_skills=state.job_description_skills,
                profile_skills=(),
                matched_skills=snapshot.matched_skills,
            )
            return SkillGapReport(gaps=gaps, derived_from_degraded_input=True)
        gaps = build_skill_gap_entries(
            jd_skills=state.job_description_skills,
            profile_skills=profile.skills,
            matched_skills=snapshot.matched_skills,
        )
        # An empty gap list is a valid, non-degraded result (Requirement 5.7).
        return SkillGapReport(gaps=gaps)

    def build_degraded(self, state: AgentState) -> SkillGapReport:
        """Persisted missing skills, sequential ranks, no prioritization (Req 5.5)."""
        snapshot = _require_snapshot(state)
        return SkillGapReport(
            gaps=[
                SkillGapEntry(skill=skill, classification="missing", rank=rank)
                for rank, skill in enumerate(snapshot.missing_skills, start=1)
            ],
            degraded=True,
        )

    def build_minimal(self) -> SkillGapReport:
        """Last-resort schema-valid output (Requirement 8.6): no persisted data."""
        return SkillGapReport(gaps=[], degraded=True)


def _require_snapshot(state: AgentState) -> MatchSnapshot:
    """The state's MatchSnapshot, or raise.

    The Agent_Worker loads the persisted Match_Result projection before
    graph invocation, so a missing snapshot is a pre-validation gap — the
    raise routes through the standard degradation lifecycle rather than
    producing an output from nothing.
    """
    snapshot = state.match_snapshot
    if snapshot is None:
        msg = "AgentState carries no MatchSnapshot; the worker must load it before invocation"
        raise ValueError(msg)
    return snapshot


def _require_profile(state: AgentState) -> CandidateProfile:
    """The state's CandidateProfile, or raise.

    The graph runs ``skill_gap`` strictly after ``resume_analysis``
    (design section "Graph topology"), which always writes a
    Candidate_Profile — normal or degraded (Requirement 3.5). A missing
    profile therefore signals a broken invocation, not a degraded
    upstream; the raise routes through the standard degradation lifecycle.
    """
    profile = state.candidate_profile
    if profile is None:
        msg = "AgentState carries no CandidateProfile; resume_analysis must run first"
        raise ValueError(msg)
    return profile
