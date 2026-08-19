"""Typed Agent_State and agent output schemas (phase-4-agentic).

The single typed state passed between Agent_Graph nodes, plus the Pydantic
output schema of every agent. Design reference: "1. Agent state schema" in
the phase-4-agentic design. Requirements covered: 1.2, 1.3, 8.2.

Key contracts encoded in these types:

* :class:`AgentState` carries the Resume and Job_Description **by identifier**
  plus redacted or derived content only — there is deliberately no field for
  raw ``extracted_text``, so every serialized state snapshot (checkpoints,
  ``agent_runs`` rows, spans) contains no raw Restricted PII by construction
  (Requirement 1.3).
* Every agent output model carries a ``degraded: bool = False`` marker, so a
  Degraded_Output passes validation against the exact same schema as the
  normal output and downstream nodes never branch on output shape
  (Requirement 8.2).
* Each per-agent output field on :class:`AgentState` is written by exactly
  one node, which is what makes LangGraph's parallel branch merge safe
  (Requirement 1.2).
* :class:`FailureDetail` uses a closed trigger vocabulary and an
  operator-safe detail string — never PII, provider response bodies, or
  prompt content.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

__all__ = [
    "ATSOutput",
    "AgentCompletion",
    "AgentState",
    "AgentStatusFlag",
    "AgentTraceSummary",
    "AnalysisResult",
    "CandidateProfile",
    "ExperienceEntry",
    "FailureDetail",
    "FailureTrigger",
    "ImprovementAction",
    "ImprovementReport",
    "MatchSnapshot",
    "RewriteSuggestion",
    "SkillGapEntry",
    "SkillGapReport",
    "merge_agent_status",
]


class AgentCompletion(StrEnum):
    """How an agent invocation ended: normally or via its degraded path."""

    COMPLETED = "completed"
    DEGRADED = "degraded"


# Closed vocabulary of degradation/failure triggers (Requirement 8.4).
# ``degraded_construction_error`` covers the double-failure case where
# building the Degraded_Output itself raised (Requirement 8.6).
FailureTrigger = Literal[
    "error",
    "timeout",
    "schema_validation",
    "quota_exhausted",
    "breaker_open",
    "empty_input",
    "degraded_construction_error",
]


class FailureDetail(BaseModel):
    """Structured, PII-free reason an agent took its degraded path.

    ``detail`` is operator-safe display text: it never carries resume or
    job-description content, provider response bodies, or secrets — the
    trigger plus identifiers is all an operator needs (``security.md``).
    """

    trigger: FailureTrigger
    detail: str | None = None


class AgentStatusFlag(BaseModel):
    """Per-agent status carried in state; feeds trace summaries.

    ``latency_ms`` is the lifecycle-recorded invocation latency — the same
    node-invocation-start → output-return measurement persisted on the
    Agent_Run row — carried as state metadata so the Synthesizer can build
    each :class:`AgentTraceSummary` without I/O (Requirements 7.3, 7.5).
    """

    status: AgentCompletion
    failure_reason: FailureDetail | None = None
    # Non-negative; 0 until the lifecycle records the measured value.
    latency_ms: int = Field(default=0, ge=0)


class MatchSnapshot(BaseModel):
    """Pydantic projection of the persisted Match_Result.

    Loaded once by the Agent_Worker before graph invocation so no node reads
    the database mid-graph and no node ever touches raw ``extracted_text``.
    Projected from ``match_results`` columns: ``score``, ``score_breakdown``,
    ``scorer_version``, ``matched_keywords`` / ``missing_keywords``, and the
    stored rule-based ``suggestions``.
    """

    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    scorer_version: str
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ExperienceEntry(BaseModel):
    """One experience entry in a Candidate_Profile.

    Each sub-field may be null where not detectable (Requirement 3.2).
    """

    role: str | None = None
    organization: str | None = None
    duration: str | None = None


class CandidateProfile(BaseModel):
    """Structured resume profile produced by the Resume_Analysis_Agent."""

    sections: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    experiences: list[ExperienceEntry] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    degraded: bool = False
    derived_from_degraded_input: bool = False


class ATSOutput(BaseModel):
    """Score, breakdown, confidence, and scorer version from the ATS_Agent."""

    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    confidence: Literal["high", "medium", "low"]
    scorer_version: str
    degraded: bool = False


class SkillGapEntry(BaseModel):
    """One classified, ranked skill gap (Requirement 5.2)."""

    skill: str
    classification: Literal["missing", "weak"]
    # 1-based priority rank; sequential and unique within a report.
    rank: int


class SkillGapReport(BaseModel):
    """Missing/weak skills relative to the Job_Description, prioritized."""

    # Ordered by ascending rank. An empty list is a valid result (full
    # coverage) and never a degradation trigger (Requirement 5.7).
    gaps: list[SkillGapEntry] = Field(default_factory=list)
    degraded: bool = False
    derived_from_degraded_input: bool = False


class ImprovementAction(BaseModel):
    """One prioritized improvement action (Requirement 6.2)."""

    # Integer priority rank; the report orders actions high → low priority.
    rank: int
    text: str


class RewriteSuggestion(BaseModel):
    """A concrete rewrite: redacted excerpt, replacement, and rationale.

    ``excerpt`` is always PII_Redactor-transformed resume content — never raw
    unredacted text (Requirement 6.2).
    """

    excerpt: str
    replacement: str
    rationale: str


class ImprovementReport(BaseModel):
    """Rewrites and prioritized additions from the Improvement_Agent."""

    actions: list[ImprovementAction] = Field(default_factory=list)
    rewrites: list[RewriteSuggestion] = Field(default_factory=list)
    degraded: bool = False
    derived_from_degraded_input: bool = False


class AgentTraceSummary(BaseModel):
    """Per-agent trace summary embedded in the Analysis_Result.

    Name, completion status, invocation latency, and — when the agent
    degraded — the structured failure reason (Requirement 7.5). Never PII.
    """

    agent_name: str
    status: AgentCompletion
    latency_ms: int
    failure_reason: FailureDetail | None = None


class AnalysisResult(BaseModel):
    """Final combined output the Synthesizer assembles (Requirement 7.4)."""

    ats: ATSOutput
    skill_gaps: SkillGapReport
    improvements: ImprovementReport
    profile: CandidateProfile
    agent_traces: list[AgentTraceSummary] = Field(default_factory=list)


def merge_agent_status(
    left: dict[str, AgentStatusFlag],
    right: dict[str, AgentStatusFlag],
) -> dict[str, AgentStatusFlag]:
    """Reducer merging parallel ``agent_status`` partial updates.

    ``agent_status`` is the one :class:`AgentState` field written by
    *every* node, so parallel branches (ATS ∥ Resume_Analysis in the first
    superstep, Skill_Gap ∥ Improvement in the fan-out) each contribute a
    partial update to the same key within one superstep. LangGraph rejects
    concurrent writes to an un-annotated field, so the field carries this
    reducer via ``Annotated`` (design "Research notes": parallel-writable
    fields use per-field reducers). Each agent writes only its own
    ``name`` key, so a plain dict union is a safe, order-independent merge
    (Requirement 1.2).
    """
    return {**left, **right}


class AgentState(BaseModel):
    """The single typed state passed between Agent_Graph nodes.

    Carries identifiers plus redacted/derived content only — there is no
    field for raw ``extracted_text`` (Requirement 1.3). Each per-agent output
    field is written by exactly one node, making LangGraph's parallel merge
    safe (Requirement 1.2).
    """

    # Identifiers only — never raw extracted_text (Requirement 1.3).
    job_id: str
    match_id: str
    user_id: str

    # Redacted / derived inputs loaded once by the worker before invocation.
    redacted_resume_text: str | None = None
    job_description_skills: list[str] = Field(default_factory=list)
    match_snapshot: MatchSnapshot | None = None

    # Per-agent outputs (each written by exactly one node → safe parallel
    # merge).
    candidate_profile: CandidateProfile | None = None
    ats_output: ATSOutput | None = None
    skill_gap_report: SkillGapReport | None = None
    improvement_report: ImprovementReport | None = None
    analysis_result: AnalysisResult | None = None

    # Per-agent status flags, keyed by agent name. Written by every node,
    # so parallel supersteps need the merge reducer (see merge_agent_status).
    agent_status: Annotated[dict[str, AgentStatusFlag], merge_agent_status] = Field(
        default_factory=dict
    )
