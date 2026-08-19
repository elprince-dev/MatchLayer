"""The improvement agent — LLM rewrites and additions (phase-4-agentic).

:class:`ImprovementAgent` is the LLM node that turns the upstream
Candidate_Profile plus the persisted Match_Result skill analysis into an
:class:`~matchlayer_api.ml.agents.state.ImprovementReport` (design section
"3. The five concrete agents"). It extends
:class:`~matchlayer_api.ml.agents.llm_agent.LLMAgent`, so the inherited
final ``run`` delegates every provider interaction to the Phase 3
orchestrator — quota, redaction, caching, spend control, invocation
logging, and output schema validation are never bypassed (Requirements
6.1, 9.1-9.7).

Prompt identity (Requirement 6.1)
---------------------------------

:meth:`ImprovementAgent.feature_spec` carries
``LLMFeature.AGENT_IMPROVEMENT`` — the agent's own feature value, so its
persisted results, invocation-log rows, and cache entries live in an
agent-specific namespace (Requirement 9.7) and never contaminate the
Phase 3 ``resume_coach`` rows (phase-4 Requirement 16.5). The feature's
template files resolve to the **Phase 3 resume-coach Prompt_Template
lineage** through the registry's ``PROMPT_TEMPLATE_LINEAGE`` map plus its
``ACTIVE_PROMPT_VERSIONS`` entry — prompt content is reused, never
duplicated, and no prompt name or version appears as a string literal in
this module (Requirement 9.6).

Prompt input (Requirement 6.3)
------------------------------

:meth:`ImprovementAgent.build_prompt_input` assembles the serialized
Candidate_Profile and the Match_Result's persisted matched/missing skills
(from the MatchSnapshot loaded into state by the worker). The whole
assembly travels inside one delimited ``<user_content>`` region with
structured message roles — the Phase 3 prompt-injection defenses apply
unchanged. There is deliberately **no dependency on the
Skill_Gap_Report**: the Improvement_Agent runs in parallel with the
Skill_Gap_Agent (Requirement 1.5), so its input closure is exactly
``(candidate_profile, match_snapshot)``. The content is derived data by
construction — the Candidate_Profile came from an LLM that only ever saw
PII_Redactor-transformed text, and the snapshot skill lists are stored
PII-free analysis — so, like the coach's skill sections, it passes
through with ``redaction=None`` and rewrites can only ever quote redacted
excerpts (Requirement 6.2).

Degraded input is not degraded output (Requirement 6.5): a degraded
Candidate_Profile is serialized identically to a normal one — same
schema, no shape-branching (Requirement 8.2) — and the resulting report
is marked ``derived_from_degraded_input=True`` via the
:meth:`finalize_output` hook, uniformly on fresh calls and cache hits.

Degradation (Requirement 6.4): an LLM failure, schema-validation failure,
or per-node timeout routes ``BaseAgent.__call__`` to
:meth:`ImprovementAgent.build_degraded` — an ImprovementReport built from
the Match_Result's stored rule-based suggestions (as actions, ranked in
stored order) and missing skills, with empty rewrites and
``degraded=True`` — and the Agent_Graph continues.

Output (Requirement 6.6): every completion — normal or degraded — writes
a schema-valid ImprovementReport to the ``improvement_report`` state
field, where the Synthesizer consumes it.
"""

from __future__ import annotations

from typing import Final

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.llm_agent import LLMAgent
from matchlayer_api.ml.agents.state import (
    AgentState,
    CandidateProfile,
    ImprovementAction,
    ImprovementReport,
    MatchSnapshot,
)
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.schemas import FailureReason

__all__ = ["IMPROVEMENT_FEATURE_SPEC", "ImprovementAgent"]

_MIN_IMPROVEMENTS: Final[int] = 3
_MAX_IMPROVEMENTS: Final[int] = 10
"""Values for the resume-coach template's ``{min_improvements}`` /
``{max_improvements}`` placeholders (the reused lineage documents both) —
PII-free constants mirroring the coach's instruction bounds. The agent's
own :class:`ImprovementReport` schema imposes no count bounds, so these
shape the instruction text only."""

_EMPTY_SKILLS_TEXT: Final[str] = "(none)"
"""Placeholder for an empty stored skill list, so the labeled lines inside
the delimited region are never blank (mirrors the coach's convention)."""


def _format_skills(skills: list[str]) -> str:
    """Format a snapshot skill list verbatim for the delimited region.

    The stored values pass through unmodified apart from the comma-space
    join (the prompt is grounded in the persisted Phase 2 analysis, never
    a re-computation); an empty list renders as ``(none)``.
    """
    if not skills:
        return _EMPTY_SKILLS_TEXT
    return ", ".join(skills)


def _build_inputs(match: MatchResult, feature_input: str) -> PromptInputs:
    """Wrap the agent-assembled user content for prompt assembly.

    ``feature_input`` is the string :meth:`ImprovementAgent.build_prompt_input`
    assembled from Agent_State (serialized Candidate_Profile + snapshot
    skills). It is derived, already-redacted content by construction —
    the profile came from an LLM that only saw PII_Redactor-transformed
    text, and the skill lists are stored PII-free analysis — so it passes
    through with ``redaction=None``, exactly like the coach's stored
    skill sections. ``values`` fills the reused coach template's two
    placeholders; the MatchResult contributes nothing here (state is the
    agent's single input source, Requirement 6.3).
    """
    del match  # state carries everything the prompt needs (Req 6.3)
    return PromptInputs(
        values={
            "min_improvements": str(_MIN_IMPROVEMENTS),
            "max_improvements": str(_MAX_IMPROVEMENTS),
        },
        sections=(PromptSection(kind="resume", text=feature_input, redaction=None),),
    )


def _build_fallback(
    match: MatchResult, feature_input: str, reason: FailureReason
) -> ImprovementReport:
    """The orchestrator-level Fallback_Response (never user-visible here).

    The inherited ``LLMAgent.run`` raises
    :class:`~matchlayer_api.ml.agents.llm_agent.LLMFallbackError` on any
    fallback envelope so the *agent's* :meth:`ImprovementAgent.build_degraded`
    produces the surfaced Degraded_Output (Requirement 6.4) — but the
    spec contract requires a schema-valid builder, so this derives the
    same suggestions-and-missing-skills content from the bound
    MatchResult's stored columns.
    """
    del feature_input, reason  # envelope concerns; content is match-derived
    return _degraded_report(
        suggestions=[str(entry) for entry in match.suggestions],
        missing_skills=[str(entry) for entry in match.missing_keywords],
    )


IMPROVEMENT_FEATURE_SPEC: Final[LLMFeatureSpec[str, ImprovementReport]] = LLMFeatureSpec(
    feature=LLMFeature.AGENT_IMPROVEMENT,
    result_schema=ImprovementReport,
    build_inputs=_build_inputs,
    build_fallback=_build_fallback,
    reuse_persisted=False,
)
"""The Improvement_Agent's parameterization of the Phase 3 pipeline.

``feature`` is the agent's own registry value: its template files resolve
to the resume-coach lineage through ``PROMPT_TEMPLATE_LINEAGE`` and the
registry's active-version entry — never a string literal here
(Requirements 6.1, 9.6) — while cache, persistence, and invocation-log
namespaces stay agent-specific (Requirement 9.7). ``reuse_persisted``
stays ``False``: persisted-result reuse is the Resume_Coach's step
(Phase 3 design D7), and the agent's repeat suppression comes from the
orchestrator cache instead."""


def _missing_skill_action(skill: str) -> str:
    """One deterministic improvement action for a stored missing skill."""
    return (
        f"The job description asks for {skill}, which MatchLayer's analysis "
        "did not find in your resume — add concrete evidence of it if you "
        "have that experience."
    )


def _degraded_report(*, suggestions: list[str], missing_skills: list[str]) -> ImprovementReport:
    """Build the Degraded_Output content (Requirement 6.4).

    Actions carry the stored rule-based suggestions first, in stored
    order, followed by one action per stored missing skill; ranks are
    assigned sequentially from 1 over the combined list. Rewrites are
    empty — no LLM saw the resume, so there is nothing to quote — and the
    report is marked ``degraded=True``.
    """
    texts = [text for text in (entry.strip() for entry in suggestions) if text]
    texts.extend(
        _missing_skill_action(skill)
        for skill in (entry.strip() for entry in missing_skills)
        if skill
    )
    return ImprovementReport(
        actions=[
            ImprovementAction(rank=rank, text=text) for rank, text in enumerate(texts, start=1)
        ],
        rewrites=[],
        degraded=True,
    )


class ImprovementAgent(LLMAgent[ImprovementReport]):
    """LLM improvement node (design "3. The five concrete agents").

    Supplies only the feature parameterization, the prompt-input builder,
    and the degraded/minimal builders; the invocation lifecycle (timeout,
    degradation, Agent_Run persistence, spans) is ``BaseAgent.__call__``
    and the LLM plumbing is the inherited final ``LLMAgent.run``
    (Requirement 6.1).
    """

    name = "improvement"
    output_field = "improvement_report"

    # ---- prompt identity and input (Requirements 6.1, 6.3) ---------------

    def feature_spec(self) -> LLMFeatureSpec[str, ImprovementReport]:
        """The registry-resolved resume-coach-lineage spec (Requirement 6.1)."""
        return IMPROVEMENT_FEATURE_SPEC

    def build_prompt_input(self, state: AgentState) -> str:
        """Serialized Candidate_Profile + snapshot matched/missing skills.

        Everything lands inside the single delimited user-content region
        (Requirement 6.3); the Skill_Gap_Report is deliberately never
        read (parallel branch, Requirement 1.5). A degraded profile is
        serialized identically to a normal one — same schema, no
        shape-branching (Requirements 6.5, 8.2). Raises on missing state
        inputs so ``BaseAgent.__call__`` degrades the node with zero
        provider calls and zero Daily_Quota consumption (the raise
        precedes any orchestrator interaction).
        """
        profile = _require_profile(state)
        snapshot = _require_snapshot(state)
        return (
            "Candidate profile (structured JSON derived from the resume):\n"
            f"{profile.model_dump_json()}\n\n"
            f"Matched skills: {_format_skills(snapshot.matched_skills)}\n"
            f"Missing skills: {_format_skills(snapshot.missing_skills)}"
        )

    # ---- degraded-input marking (Requirement 6.5) -------------------------

    def finalize_output(self, state: AgentState, output: ImprovementReport) -> ImprovementReport:
        """Mark reports derived from a degraded Candidate_Profile.

        Pure function of ``(state, output)``, applied uniformly to fresh
        provider calls and cache hits by the inherited final ``run``.
        """
        if state.candidate_profile is not None and state.candidate_profile.degraded:
            return output.model_copy(update={"derived_from_degraded_input": True})
        return output

    # ---- degraded outputs (Requirements 6.4, 8.6) --------------------------

    def build_degraded(self, state: AgentState) -> ImprovementReport:
        """Stored suggestions + missing skills as ranked actions (Req 6.4)."""
        snapshot = _require_snapshot(state)
        report = _degraded_report(
            suggestions=snapshot.suggestions,
            missing_skills=snapshot.missing_skills,
        )
        if state.candidate_profile is not None and state.candidate_profile.degraded:
            return report.model_copy(update={"derived_from_degraded_input": True})
        return report

    def build_minimal(self) -> ImprovementReport:
        """Last-resort schema-valid output (Requirement 8.6): no persisted data."""
        return ImprovementReport(actions=[], rewrites=[], degraded=True)


def _require_profile(state: AgentState) -> CandidateProfile:
    """The state's CandidateProfile, or raise.

    The graph runs ``improvement`` strictly after ``resume_analysis``
    (design "Graph topology"), which always writes a Candidate_Profile —
    normal or degraded (Requirement 3.5). A missing profile therefore
    signals a broken invocation; the raise routes through the standard
    degradation lifecycle before any orchestrator interaction.
    """
    profile = state.candidate_profile
    if profile is None:
        msg = "AgentState carries no CandidateProfile; resume_analysis must run first"
        raise ValueError(msg)
    return profile


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
