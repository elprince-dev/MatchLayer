"""The resume analysis agent — LLM candidate profiling (phase-4-agentic).

:class:`ResumeAnalysisAgent` is the LLM node that turns the worker-redacted
resume text into a structured
:class:`~matchlayer_api.ml.agents.state.CandidateProfile` (design section
"3. The five concrete agents"). It extends
:class:`~matchlayer_api.ml.agents.llm_agent.LLMAgent`, so the inherited
final ``run`` delegates every provider interaction to the Phase 3
orchestrator — quota, redaction, caching, spend control, invocation
logging, and output schema validation are never bypassed, and at most one
provider call happens per execution with zero calls on a cache hit
(Requirements 3.1, 9.1-9.7).

Prompt identity (Requirement 3.3)
---------------------------------

:meth:`ResumeAnalysisAgent.feature_spec` carries
``LLMFeature.AGENT_RESUME_ANALYSIS`` — the agent's own feature value, so
its persisted results, invocation-log rows, and cache entries live in an
agent-specific namespace (Requirement 9.7). Unlike the Improvement_Agent,
this feature **owns its template lineage**: its versioned Prompt_Template
files are ``agent_resume_analysis.vN.txt`` (structured message roles,
delimited user-content region, the Phase 3 injection defenses), resolved
exclusively through the registry's ``ACTIVE_PROMPT_VERSIONS`` entry —
no prompt name or version appears as a string literal in this module
(Requirement 9.6).

Prompt input (Requirements 3.1, 3.6)
------------------------------------

:meth:`ResumeAnalysisAgent.build_prompt_input` returns
``state.redacted_resume_text`` — the resume text the Agent_Worker already
transformed with the PII_Redactor before building Agent_State (state
deliberately has no field for raw ``extracted_text``, Requirement 1.3).
Because the text is redacted by construction, the feature spec passes it
through the prompt-assembly pipeline with ``redaction=None``, exactly like
the other agents' derived content; it still travels entirely inside the
single delimited ``<user_content kind="resume">`` region so the Phase 3
prompt-injection defenses apply unchanged (Requirement 3.3). If the
redacted text is empty or unavailable, ``build_prompt_input`` raises
:class:`~matchlayer_api.ml.agents.base.EmptyInputError` **before** any
orchestrator interaction — the node degrades with the ``empty_input``
reason, zero provider calls, and zero Daily_Quota consumption
(Requirement 3.6).

Degradation (Requirements 3.4, 3.5): an LLM failure, output
schema-validation failure, or per-node timeout routes
``BaseAgent.__call__`` to :meth:`ResumeAnalysisAgent.build_degraded` — a
CandidateProfile whose skills are the persisted Match_Result's matched +
missing skills (the Phase 2 Skill_Extractor results projected into the
MatchSnapshot by the worker), with empty sections/experiences/gaps and
``degraded=True`` — and the Agent_Graph continues, carrying the profile
forward on the ``candidate_profile`` state field for the Skill_Gap_Agent
and Improvement_Agent.
"""

from __future__ import annotations

from typing import Final

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.agents.base import EmptyInputError
from matchlayer_api.ml.agents.llm_agent import LLMAgent
from matchlayer_api.ml.agents.state import AgentState, CandidateProfile, MatchSnapshot
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.schemas import FailureReason

__all__ = ["RESUME_ANALYSIS_FEATURE_SPEC", "ResumeAnalysisAgent"]


def _skills_from(matched: list[str], missing: list[str]) -> list[str]:
    """Combine matched + missing skill lists, de-duplicated, order-preserving.

    The persisted lists are the Phase 2 Skill_Extractor's lexicon analysis
    (matched first, then missing, in stored order); a skill can appear in
    only one list, but the guard keeps the output well-formed regardless.
    """
    seen: set[str] = set()
    skills: list[str] = []
    for skill in (*matched, *missing):
        text = skill.strip()
        if text and text not in seen:
            seen.add(text)
            skills.append(text)
    return skills


def _degraded_profile(*, matched: list[str], missing: list[str]) -> CandidateProfile:
    """Build the Degraded_Output content (Requirement 3.4).

    Skills come from the Phase 2 Skill_Extractor results persisted on the
    Match_Result (matched + missing lexicon skills); sections, experiences,
    and gaps stay empty — no LLM saw the resume, so there is no structured
    reading to report — and the profile is marked ``degraded=True``.
    """
    return CandidateProfile(
        sections=[],
        skills=_skills_from(matched, missing),
        experiences=[],
        gaps=[],
        degraded=True,
    )


def _build_inputs(match: MatchResult, feature_input: str) -> PromptInputs:
    """Wrap the agent-assembled user content for prompt assembly.

    ``feature_input`` is ``state.redacted_resume_text`` — already
    PII_Redactor-transformed by the Agent_Worker before it entered
    Agent_State (Requirement 3.1), so it passes through with
    ``redaction=None`` and lands verbatim inside the delimited
    ``<user_content kind="resume">`` region (Requirement 3.3). The
    template has no placeholder slots, so ``values`` is empty; the
    MatchResult contributes nothing here (state is the agent's single
    input source).
    """
    del match  # state carries everything the prompt needs
    return PromptInputs(
        values={},
        sections=(PromptSection(kind="resume", text=feature_input, redaction=None),),
    )


def _build_fallback(
    match: MatchResult, feature_input: str, reason: FailureReason
) -> CandidateProfile:
    """The orchestrator-level Fallback_Response (never user-visible here).

    The inherited ``LLMAgent.run`` raises
    :class:`~matchlayer_api.ml.agents.llm_agent.LLMFallbackError` on any
    fallback envelope so the *agent's*
    :meth:`ResumeAnalysisAgent.build_degraded` produces the surfaced
    Degraded_Output (Requirement 3.4) — but the spec contract requires a
    schema-valid builder, so this derives the same skills-only content
    from the bound MatchResult's stored columns.
    """
    del feature_input, reason  # envelope concerns; content is match-derived
    return _degraded_profile(
        matched=[str(entry) for entry in match.matched_keywords],
        missing=[str(entry) for entry in match.missing_keywords],
    )


RESUME_ANALYSIS_FEATURE_SPEC: Final[LLMFeatureSpec[str, CandidateProfile]] = LLMFeatureSpec(
    feature=LLMFeature.AGENT_RESUME_ANALYSIS,
    result_schema=CandidateProfile,
    build_inputs=_build_inputs,
    build_fallback=_build_fallback,
    reuse_persisted=False,
)
"""The Resume_Analysis_Agent's parameterization of the Phase 3 pipeline.

``feature`` is the agent's own registry value, which owns its template
lineage — ``agent_resume_analysis.vN.txt`` resolved through the
registry's active-version entry, never a string literal here
(Requirements 3.3, 9.6) — and gives cache, persistence, and
invocation-log rows an agent-specific namespace (Requirement 9.7).
``reuse_persisted`` stays ``False``: persisted-result reuse is the
Resume_Coach's step (Phase 3 design D7); the agent's repeat suppression
comes from the orchestrator cache instead."""


class ResumeAnalysisAgent(LLMAgent[CandidateProfile]):
    """LLM resume-profiling node (design "3. The five concrete agents").

    Supplies only the feature parameterization, the prompt-input builder,
    and the degraded/minimal builders; the invocation lifecycle (timeout,
    degradation, Agent_Run persistence, spans) is ``BaseAgent.__call__``
    and the LLM plumbing is the inherited final ``LLMAgent.run``
    (Requirement 3.1).
    """

    name = "resume_analysis"
    output_field = "candidate_profile"

    # ---- prompt identity and input (Requirements 3.1, 3.3, 3.6) ----------

    def feature_spec(self) -> LLMFeatureSpec[str, CandidateProfile]:
        """The registry-resolved ``agent_resume_analysis`` spec (Req 3.3)."""
        return RESUME_ANALYSIS_FEATURE_SPEC

    def build_prompt_input(self, state: AgentState) -> str:
        """The worker-redacted resume text for the delimited region.

        Raises :class:`~matchlayer_api.ml.agents.base.EmptyInputError`
        when the redacted text is empty (including whitespace-only) or
        unavailable, so ``BaseAgent.__call__`` degrades the node with the
        ``empty_input`` reason, zero provider calls, and zero Daily_Quota
        consumption — the raise precedes any orchestrator interaction
        (Requirement 3.6).
        """
        text = state.redacted_resume_text
        if text is None or not text.strip():
            msg = "redacted resume text is empty or unavailable"
            raise EmptyInputError(msg)
        return text

    # ---- degraded outputs (Requirements 3.4, 8.6) --------------------------

    def build_degraded(self, state: AgentState) -> CandidateProfile:
        """Skill_Extractor-derived profile from the MatchSnapshot (Req 3.4)."""
        snapshot = _require_snapshot(state)
        return _degraded_profile(
            matched=snapshot.matched_skills,
            missing=snapshot.missing_skills,
        )

    def build_minimal(self) -> CandidateProfile:
        """Last-resort schema-valid output (Requirement 8.6): no persisted data."""
        return CandidateProfile(sections=[], skills=[], experiences=[], gaps=[], degraded=True)


def _require_snapshot(state: AgentState) -> MatchSnapshot:
    """The state's MatchSnapshot, or raise.

    The Agent_Worker loads the persisted Match_Result projection before
    graph invocation, so a missing snapshot is a pre-validation gap — the
    raise routes ``_build_degraded_safely`` to the minimal output with the
    ``degraded_construction_error`` reason (Requirement 8.6) rather than
    fabricating a profile from nothing.
    """
    snapshot = state.match_snapshot
    if snapshot is None:
        msg = "AgentState carries no MatchSnapshot; the worker must load it before invocation"
        raise ValueError(msg)
    return snapshot
