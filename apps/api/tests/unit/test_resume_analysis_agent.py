"""Unit tests for the ResumeAnalysisAgent (phase-4-agentic task 5.1).

Covers the contract in ``ml/agents/resume_analysis_agent.py`` with a fake
job-scoped orchestrator (no models, no database, no provider):

* Feature spec — the agent's own ``agent_resume_analysis`` feature value
  owning its template lineage, resolved exclusively through the registry
  (Requirements 3.3, 9.6); persisted-result reuse stays off.
* Prompt input — the worker-redacted resume text; empty/None/whitespace
  text raises ``EmptyInputError`` before any orchestrator interaction
  (zero provider calls, zero Daily_Quota consumption — Requirement 3.6).
* Success path — the validated CandidateProfile lands on the
  ``candidate_profile`` state field (Requirements 3.1, 3.5).
* Degradation — a fallback envelope routes to ``build_degraded``: skills
  from the snapshot's matched + missing Skill_Extractor results, empty
  sections/experiences/gaps, ``degraded=True`` (Requirement 3.4); a
  missing snapshot falls through to the minimal output (Requirement 8.6).
* Hierarchy contract — extends ``LLMAgent``, does not override
  ``__call__`` or ``run``, writes the ``candidate_profile`` state field.
"""

from __future__ import annotations

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent, EmptyInputError
from matchlayer_api.ml.agents.llm_agent import LLMAgent
from matchlayer_api.ml.agents.resume_analysis_agent import (
    RESUME_ANALYSIS_FEATURE_SPEC,
    ResumeAnalysisAgent,
)
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    CandidateProfile,
    ExperienceEntry,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.prompts.registry import (
    ACTIVE_PROMPT_VERSIONS,
    LLMFeature,
    template_filename,
)
from matchlayer_api.services.llm.orchestrator import LLMFeatureSpec, LLMOutcome
from matchlayer_api.services.llm.prompting import load_template
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope

_ProfileEnvelope = LLMResultEnvelope[CandidateProfile]

_REDACTED_TEXT = "[NAME_1]\nBuilt FastAPI services at [ORG_1] for three years."


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


async def _persist_noop(
    agent_name: str,
    state: AgentState,
    output: BaseModel,
    status: AgentCompletion,
    reason: FailureDetail | None,
    latency_ms: int,
) -> None:
    return None


def _deps() -> AgentDeps:
    return AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=_persist_noop,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )


def _profile() -> CandidateProfile:
    return CandidateProfile(
        sections=["summary", "experience"],
        skills=["python", "fastapi"],
        experiences=[
            ExperienceEntry(role="Backend Engineer", organization="[ORG_1]", duration="3 years")
        ],
        gaps=["no quantified outcomes"],
    )


def _success_outcome(profile: CandidateProfile) -> LLMOutcome[CandidateProfile]:
    return LLMOutcome(
        envelope=_ProfileEnvelope(is_fallback=False, result=profile),
        quota_remaining=4,
    )


def _fallback_outcome() -> LLMOutcome[CandidateProfile]:
    return LLMOutcome(
        envelope=_ProfileEnvelope(
            is_fallback=True,
            fallback_reason=FailureReason.PROVIDER_ERROR,
            result=CandidateProfile(degraded=True),
        ),
        quota_remaining=4,
    )


class _FakeOrchestrator:
    """AgentLLMOrchestrator fake resolving every request in ``prepare``.

    Returning an ``LLMOutcome`` from ``prepare`` exercises the same final
    ``LLMAgent.run`` path as a cache hit — no ``ProviderCallPlan``
    machinery needed for these contracts.
    """

    model = "test-model"

    def __init__(self, outcome: LLMOutcome[CandidateProfile]) -> None:
        self._outcome = outcome
        self.prepare_calls: list[tuple[LLMFeatureSpec[str, CandidateProfile], str, str]] = []

    async def prepare(
        self,
        spec: LLMFeatureSpec[str, CandidateProfile],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[CandidateProfile]:
        self.prepare_calls.append((spec, user_id, feature_input))
        return self._outcome

    async def execute(self, plan: object) -> LLMOutcome[CandidateProfile]:
        raise AssertionError("prepare resolved every request; execute must not run")


def _snapshot() -> MatchSnapshot:
    return MatchSnapshot(
        score=71.5,
        breakdown={"similarity": 0.6},
        scorer_version="2.0.0+lexv2+emb-minilm+spacy-3.8",
        matched_skills=["python", "fastapi"],
        missing_skills=["kubernetes", "terraform"],
        suggestions=["Add Kubernetes experience"],
    )


def _state(
    *,
    redacted_text: str | None = _REDACTED_TEXT,
    with_snapshot: bool = True,
) -> AgentState:
    return AgentState(
        job_id="job-1",
        match_id="match-1",
        user_id="user-1",
        redacted_resume_text=redacted_text,
        match_snapshot=_snapshot() if with_snapshot else None,
    )


def _output_of(update: dict[str, object]) -> CandidateProfile:
    output = update["candidate_profile"]
    assert isinstance(output, CandidateProfile)
    return output


def _flag_of(update: dict[str, object]) -> AgentStatusFlag:
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map["resume_analysis"]
    assert isinstance(flag, AgentStatusFlag)
    return flag


class TestFeatureSpec:
    def test_uses_the_agent_specific_feature_namespace(self) -> None:
        # Requirement 9.7: cache/persistence/log namespaces are the
        # agent's own feature value.
        agent = ResumeAnalysisAgent(_deps(), _FakeOrchestrator(_success_outcome(_profile())))
        spec = agent.feature_spec()
        assert spec is RESUME_ANALYSIS_FEATURE_SPEC
        assert spec.feature is LLMFeature.AGENT_RESUME_ANALYSIS
        assert spec.result_schema is CandidateProfile
        assert spec.reuse_persisted is False

    def test_template_is_the_agents_own_lineage_via_registry(self) -> None:
        # Requirement 3.3: `agent_resume_analysis.v1` (or later) resolved
        # through the registry — the feature owns its lineage, unlike
        # AGENT_IMPROVEMENT which maps to the resume-coach lineage.
        template = load_template(RESUME_ANALYSIS_FEATURE_SPEC.feature)
        active_version = ACTIVE_PROMPT_VERSIONS[LLMFeature.AGENT_RESUME_ANALYSIS]
        assert template.version == active_version
        assert template.name == template_filename(LLMFeature.AGENT_RESUME_ANALYSIS, active_version)
        assert template.name.startswith(f"{LLMFeature.AGENT_RESUME_ANALYSIS.value}.v")

    def test_template_has_the_delimited_region_and_no_placeholders(self) -> None:
        # Requirement 3.3: the redacted text sits entirely within the
        # delimited user-content region; the template needs no runtime
        # placeholder values (build_inputs supplies none).
        template = load_template(RESUME_ANALYSIS_FEATURE_SPEC.feature)
        assert '<user_content kind="resume">' in template.text
        inputs = RESUME_ANALYSIS_FEATURE_SPEC.build_inputs(None, "content")  # type: ignore[arg-type]
        assert dict(inputs.values) == {}
        assert len(inputs.sections) == 1
        section = inputs.sections[0]
        assert section.kind == "resume"
        assert section.text == "content"
        # Already redacted by the worker before entering state.
        assert section.redaction is None


class TestPromptInput:
    def test_returns_the_redacted_text(self) -> None:
        # Requirement 3.1: the redacted resume text is the entire
        # user-content region.
        agent = ResumeAnalysisAgent(_deps(), _FakeOrchestrator(_success_outcome(_profile())))
        assert agent.build_prompt_input(_state()) == _REDACTED_TEXT

    async def test_empty_text_degrades_with_zero_orchestrator_calls(self) -> None:
        # Requirement 3.6: empty/None/whitespace redacted text → degraded
        # path, zero provider calls, zero Daily_Quota consumption.
        for text in (None, "", "   \n\t "):
            orchestrator = _FakeOrchestrator(_success_outcome(_profile()))
            agent = ResumeAnalysisAgent(_deps(), orchestrator)
            update = await agent(_state(redacted_text=text))

            assert orchestrator.prepare_calls == []
            assert _output_of(update).degraded is True
            flag = _flag_of(update)
            assert flag.status is AgentCompletion.DEGRADED
            assert flag.failure_reason is not None
            assert flag.failure_reason.trigger == "empty_input"

    def test_empty_text_raises_empty_input_error(self) -> None:
        agent = ResumeAnalysisAgent(_deps(), _FakeOrchestrator(_success_outcome(_profile())))
        for text in (None, "", "   "):
            try:
                agent.build_prompt_input(_state(redacted_text=text))
            except EmptyInputError:
                continue
            raise AssertionError(f"expected EmptyInputError for {text!r}")


class TestSuccessPath:
    async def test_validated_profile_lands_on_state(self) -> None:
        # Requirements 3.1, 3.2, 3.5: one orchestrator round trip; the
        # schema-validated profile lands on candidate_profile.
        profile = _profile()
        orchestrator = _FakeOrchestrator(_success_outcome(profile))
        agent = ResumeAnalysisAgent(_deps(), orchestrator)
        update = await agent(_state())

        assert len(orchestrator.prepare_calls) == 1
        spec, user_id, feature_input = orchestrator.prepare_calls[0]
        assert spec is RESUME_ANALYSIS_FEATURE_SPEC
        assert user_id == "user-1"
        assert feature_input == _REDACTED_TEXT
        output = _output_of(update)
        assert output == profile
        assert output.degraded is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED


class TestDegradedPath:
    async def test_fallback_envelope_routes_to_build_degraded(self) -> None:
        # Requirement 3.4: an LLM failure surfaces as the agent's own
        # Degraded_Output — skills from the Skill_Extractor results
        # persisted on the Match_Result, everything else empty — and the
        # graph continues.
        orchestrator = _FakeOrchestrator(_fallback_outcome())
        agent = ResumeAnalysisAgent(_deps(), orchestrator)
        update = await agent(_state())

        output = _output_of(update)
        assert output.degraded is True
        assert output.skills == ["python", "fastapi", "kubernetes", "terraform"]
        assert output.sections == []
        assert output.experiences == []
        assert output.gaps == []

        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "error"

    def test_build_degraded_deduplicates_overlapping_skills(self) -> None:
        agent = ResumeAnalysisAgent(_deps(), _FakeOrchestrator(_fallback_outcome()))
        state = _state()
        assert state.match_snapshot is not None
        state.match_snapshot = state.match_snapshot.model_copy(
            update={"matched_skills": ["python", "python"], "missing_skills": ["python", "go"]}
        )
        output = agent.build_degraded(state)
        assert output.skills == ["python", "go"]

    async def test_missing_snapshot_yields_minimal_output(self) -> None:
        # Requirement 8.6: build_degraded needs the snapshot, so a
        # missing snapshot falls through to the minimal output.
        orchestrator = _FakeOrchestrator(_fallback_outcome())
        agent = ResumeAnalysisAgent(_deps(), orchestrator)
        update = await agent(_state(with_snapshot=False))

        output = _output_of(update)
        assert output == CandidateProfile(
            sections=[], skills=[], experiences=[], gaps=[], degraded=True
        )
        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "degraded_construction_error"

    def test_build_minimal_is_schema_valid_without_state(self) -> None:
        output = ResumeAnalysisAgent(
            _deps(), _FakeOrchestrator(_fallback_outcome())
        ).build_minimal()
        assert output == CandidateProfile(
            sections=[], skills=[], experiences=[], gaps=[], degraded=True
        )


class TestHierarchyContract:
    def test_extends_llm_agent(self) -> None:
        assert issubclass(ResumeAnalysisAgent, LLMAgent)

    def test_does_not_override_call_or_run(self) -> None:
        assert ResumeAnalysisAgent.__call__ is BaseAgent.__call__
        assert ResumeAnalysisAgent.run is LLMAgent.run  # type: ignore[comparison-overlap]

    def test_identity_classvars(self) -> None:
        assert ResumeAnalysisAgent.name == "resume_analysis"
        assert ResumeAnalysisAgent.output_field == "candidate_profile"

    async def test_output_lands_on_the_candidate_profile_state_field(self) -> None:
        # Requirement 3.5: downstream agents consume candidate_profile
        # from state.
        agent = ResumeAnalysisAgent(_deps(), _FakeOrchestrator(_success_outcome(_profile())))
        update = await agent(_state())
        assert set(update) == {"candidate_profile", "agent_status"}
        parsed = AgentState.model_validate(
            {"job_id": "j", "match_id": "m", "user_id": "u", **update}
        )
        assert parsed.candidate_profile is not None
