"""Unit tests for the Agent_State and agent output schemas.

Task 3.1 (phase-4-agentic): verifies the structural contracts of
``ml/agents/state.py`` — no raw ``extracted_text`` field on AgentState
(Requirement 1.3), the shared ``degraded`` marker on every output schema
(Requirement 8.2), and the closed FailureDetail trigger vocabulary.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    AgentTraceSummary,
    AnalysisResult,
    ATSOutput,
    CandidateProfile,
    ExperienceEntry,
    FailureDetail,
    ImprovementReport,
    MatchSnapshot,
    SkillGapEntry,
    SkillGapReport,
)


def _minimal_state() -> AgentState:
    return AgentState(job_id="j1", match_id="m1", user_id="u1")


class TestAgentState:
    def test_constructs_from_identifiers_alone(self) -> None:
        state = _minimal_state()
        assert state.redacted_resume_text is None
        assert state.job_description_skills == []
        assert state.match_snapshot is None
        assert state.agent_status == {}

    def test_has_no_field_for_raw_extracted_text(self) -> None:
        # Requirement 1.3: state carries identifiers plus redacted/derived
        # content only. There must be no attribute that could carry raw text.
        fields = set(AgentState.model_fields)
        assert "extracted_text" not in fields
        assert "resume_text" not in fields
        assert "redacted_resume_text" in fields

    def test_identifiers_are_required(self) -> None:
        with pytest.raises(ValidationError):
            AgentState.model_validate({"job_id": "j1", "match_id": "m1"})

    def test_default_collections_are_not_shared_between_instances(self) -> None:
        a, b = _minimal_state(), _minimal_state()
        a.job_description_skills.append("python")
        a.agent_status["ats"] = AgentStatusFlag(status=AgentCompletion.COMPLETED)
        assert b.job_description_skills == []
        assert b.agent_status == {}


class TestOutputSchemas:
    def test_every_output_schema_defaults_degraded_false(self) -> None:
        # Requirement 8.2: degraded and normal outputs share one schema,
        # distinguished only by the marker field.
        outputs: list[CandidateProfile | ATSOutput | SkillGapReport | ImprovementReport] = [
            CandidateProfile(),
            ATSOutput(score=42.0, confidence="low", scorer_version="2.0.0"),
            SkillGapReport(),
            ImprovementReport(),
        ]
        for output in outputs:
            assert output.degraded is False

    def test_derived_from_degraded_input_present_where_specified(self) -> None:
        assert CandidateProfile().derived_from_degraded_input is False
        assert SkillGapReport().derived_from_degraded_input is False
        assert ImprovementReport().derived_from_degraded_input is False
        # ATSOutput deliberately has no such field (it consumes no upstream
        # agent output).
        assert "derived_from_degraded_input" not in ATSOutput.model_fields

    def test_failure_detail_rejects_unknown_trigger(self) -> None:
        with pytest.raises(ValidationError):
            FailureDetail.model_validate({"trigger": "cosmic_rays"})

    def test_failure_detail_accepts_every_closed_trigger(self) -> None:
        triggers = [
            "error",
            "timeout",
            "schema_validation",
            "quota_exhausted",
            "breaker_open",
            "empty_input",
            "degraded_construction_error",
        ]
        for trigger in triggers:
            detail = FailureDetail.model_validate({"trigger": trigger})
            assert detail.trigger == trigger
            assert detail.detail is None

    def test_skill_gap_entry_classification_is_closed(self) -> None:
        assert SkillGapEntry(skill="python", classification="missing", rank=1).rank == 1
        with pytest.raises(ValidationError):
            SkillGapEntry.model_validate({"skill": "python", "classification": "absent", "rank": 1})

    def test_experience_entry_subfields_all_nullable(self) -> None:
        entry = ExperienceEntry()
        assert entry.role is None
        assert entry.organization is None
        assert entry.duration is None

    def test_analysis_result_assembles_all_four_outputs_and_traces(self) -> None:
        result = AnalysisResult(
            ats=ATSOutput(score=42.0, confidence="high", scorer_version="2.0.0"),
            skill_gaps=SkillGapReport(),
            improvements=ImprovementReport(),
            profile=CandidateProfile(),
            agent_traces=[
                AgentTraceSummary(
                    agent_name="ats",
                    status=AgentCompletion.DEGRADED,
                    latency_ms=12,
                    failure_reason=FailureDetail(trigger="timeout"),
                )
            ],
        )
        assert result.agent_traces[0].failure_reason is not None
        assert result.agent_traces[0].failure_reason.trigger == "timeout"

    def test_match_snapshot_round_trips(self) -> None:
        snapshot = MatchSnapshot(
            score=73,
            breakdown={"keyword": 0.4, "semantic": 0.6},
            scorer_version="2.0.0+lex2+emb1+spacy38",
            matched_skills=["python"],
            missing_skills=["kubernetes"],
            suggestions=["Add kubernetes experience"],
        )
        assert snapshot.score == 73.0
        assert MatchSnapshot.model_validate(snapshot.model_dump()) == snapshot
