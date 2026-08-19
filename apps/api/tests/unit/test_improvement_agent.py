"""Unit tests for the ImprovementAgent (phase-4-agentic task 5.4).

Covers the contract in ``ml/agents/improvement_agent.py`` with a fake
job-scoped orchestrator (no models, no database, no provider):

* Feature spec — the agent's own ``agent_improvement`` feature value whose
  template files resolve to the Phase 3 resume-coach lineage exclusively
  through the registry, never a string literal (Requirements 6.1, 9.6);
  persisted-result reuse stays off (that step is the Resume_Coach's).
* Prompt input — serialized Candidate_Profile plus the snapshot's
  matched/missing skills, no dependency on the Skill_Gap_Report
  (Requirement 6.3); missing state inputs raise before any orchestrator
  interaction (zero calls, zero quota).
* Degraded-input marking — a degraded profile is consumed without shape
  branching and the resulting report carries
  ``derived_from_degraded_input=True`` (Requirement 6.5).
* Degradation — a fallback envelope routes to ``build_degraded``: stored
  rule-based suggestions plus missing skills as sequentially ranked
  actions, empty rewrites, ``degraded=True`` (Requirement 6.4).
* Hierarchy contract — extends ``LLMAgent``, does not override
  ``__call__``, writes the ``improvement_report`` state field
  (Requirements 6.1, 6.6).
"""

from __future__ import annotations

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.improvement_agent import (
    IMPROVEMENT_FEATURE_SPEC,
    ImprovementAgent,
)
from matchlayer_api.ml.agents.llm_agent import LLMAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    CandidateProfile,
    FailureDetail,
    ImprovementAction,
    ImprovementReport,
    MatchSnapshot,
    RewriteSuggestion,
    SkillGapEntry,
    SkillGapReport,
)
from matchlayer_api.ml.prompts.registry import (
    ACTIVE_PROMPT_VERSIONS,
    LLMFeature,
    template_filename,
)
from matchlayer_api.services.llm.orchestrator import LLMFeatureSpec, LLMOutcome
from matchlayer_api.services.llm.prompting import load_template
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope

_ImprovementEnvelope = LLMResultEnvelope[ImprovementReport]


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


def _report() -> ImprovementReport:
    return ImprovementReport(
        actions=[ImprovementAction(rank=1, text="Quantify the migration win.")],
        rewrites=[
            RewriteSuggestion(
                excerpt="Worked on backend services.",
                replacement="Built FastAPI services handling 2M requests/day.",
                rationale="Concrete scale beats a duty statement.",
            )
        ],
    )


def _success_outcome(report: ImprovementReport) -> LLMOutcome[ImprovementReport]:
    return LLMOutcome(
        envelope=_ImprovementEnvelope(is_fallback=False, result=report),
        quota_remaining=4,
    )


def _fallback_outcome() -> LLMOutcome[ImprovementReport]:
    return LLMOutcome(
        envelope=_ImprovementEnvelope(
            is_fallback=True,
            fallback_reason=FailureReason.PROVIDER_ERROR,
            result=ImprovementReport(degraded=True),
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

    def __init__(self, outcome: LLMOutcome[ImprovementReport]) -> None:
        self._outcome = outcome
        self.prepare_calls: list[tuple[LLMFeatureSpec[str, ImprovementReport], str, str]] = []

    async def prepare(
        self,
        spec: LLMFeatureSpec[str, ImprovementReport],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[ImprovementReport]:
        self.prepare_calls.append((spec, user_id, feature_input))
        return self._outcome

    async def execute(self, plan: object) -> LLMOutcome[ImprovementReport]:
        raise AssertionError("prepare resolved every request; execute must not run")


def _snapshot() -> MatchSnapshot:
    return MatchSnapshot(
        score=71.5,
        breakdown={"similarity": 0.6},
        scorer_version="2.0.0+lexv2+emb-minilm+spacy-3.8",
        matched_skills=["python", "fastapi"],
        missing_skills=["kubernetes", "terraform"],
        suggestions=["Add Kubernetes experience", "Quantify outcomes"],
    )


def _profile(*, degraded: bool = False) -> CandidateProfile:
    return CandidateProfile(
        sections=["experience"],
        skills=["python", "fastapi"],
        experiences=[],
        gaps=[],
        degraded=degraded,
    )


def _state(
    *,
    with_profile: bool = True,
    profile_degraded: bool = False,
    with_snapshot: bool = True,
) -> AgentState:
    return AgentState(
        job_id="job-1",
        match_id="match-1",
        user_id="user-1",
        candidate_profile=_profile(degraded=profile_degraded) if with_profile else None,
        match_snapshot=_snapshot() if with_snapshot else None,
    )


def _output_of(update: dict[str, object]) -> ImprovementReport:
    output = update["improvement_report"]
    assert isinstance(output, ImprovementReport)
    return output


def _flag_of(update: dict[str, object]) -> AgentStatusFlag:
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map["improvement"]
    assert isinstance(flag, AgentStatusFlag)
    return flag


class TestFeatureSpec:
    def test_uses_the_agent_specific_feature_namespace(self) -> None:
        # Requirement 9.7: cache/persistence/log namespaces are the
        # agent's own — never the Phase 3 resume_coach key.
        agent = ImprovementAgent(_deps(), _FakeOrchestrator(_success_outcome(_report())))
        spec = agent.feature_spec()
        assert spec is IMPROVEMENT_FEATURE_SPEC
        assert spec.feature is LLMFeature.AGENT_IMPROVEMENT
        assert spec.result_schema is ImprovementReport
        assert spec.reuse_persisted is False

    def test_template_resolves_to_the_resume_coach_lineage_via_registry(self) -> None:
        # Requirement 6.1: the Phase 3 resume-coach Prompt_Template
        # lineage, resolved through the registry — the loaded template is
        # exactly the coach lineage file at the registry-active version.
        template = load_template(IMPROVEMENT_FEATURE_SPEC.feature)
        active_version = ACTIVE_PROMPT_VERSIONS[LLMFeature.AGENT_IMPROVEMENT]
        assert template.version == active_version
        assert template.name == template_filename(LLMFeature.AGENT_IMPROVEMENT, active_version)
        assert template.name.startswith(f"{LLMFeature.RESUME_COACH.value}.v")
        # The reused lineage's placeholders are fillable by the spec's
        # input builder (an unfilled placeholder would mean nothing is
        # ever transmitted).
        inputs = IMPROVEMENT_FEATURE_SPEC.build_inputs(None, "content")  # type: ignore[arg-type]
        assert set(inputs.values) == {"min_improvements", "max_improvements"}


class TestPromptInput:
    def test_carries_profile_json_and_snapshot_skills(self) -> None:
        # Requirement 6.3: serialized Candidate_Profile + persisted
        # matched/missing skills.
        agent = ImprovementAgent(_deps(), _FakeOrchestrator(_success_outcome(_report())))
        state = _state()
        assert state.candidate_profile is not None
        prompt_input = agent.build_prompt_input(state)
        assert state.candidate_profile.model_dump_json() in prompt_input
        assert "python, fastapi" in prompt_input
        assert "kubernetes, terraform" in prompt_input

    def test_does_not_depend_on_the_skill_gap_report(self) -> None:
        # Requirement 6.3: parallel branch — adding a Skill_Gap_Report to
        # state changes nothing about the assembled prompt input.
        agent = ImprovementAgent(_deps(), _FakeOrchestrator(_success_outcome(_report())))
        without_gap = agent.build_prompt_input(_state())
        state = _state()
        state.skill_gap_report = SkillGapReport(
            gaps=[SkillGapEntry(skill="kubernetes", classification="missing", rank=1)]
        )
        assert agent.build_prompt_input(state) == without_gap

    async def test_missing_profile_degrades_with_zero_orchestrator_calls(self) -> None:
        # The raise precedes any orchestrator interaction: zero provider
        # calls, zero Daily_Quota consumption.
        orchestrator = _FakeOrchestrator(_success_outcome(_report()))
        agent = ImprovementAgent(_deps(), orchestrator)
        update = await agent(_state(with_profile=False))

        assert orchestrator.prepare_calls == []
        assert _output_of(update).degraded is True
        assert _flag_of(update).status is AgentCompletion.DEGRADED


class TestSuccessPath:
    async def test_validated_output_lands_on_state(self) -> None:
        # Requirements 6.1, 6.6: one orchestrator round trip; the report
        # lands on the improvement_report state field.
        report = _report()
        orchestrator = _FakeOrchestrator(_success_outcome(report))
        agent = ImprovementAgent(_deps(), orchestrator)
        update = await agent(_state())

        assert len(orchestrator.prepare_calls) == 1
        spec, user_id, feature_input = orchestrator.prepare_calls[0]
        assert spec is IMPROVEMENT_FEATURE_SPEC
        assert user_id == "user-1"
        assert "Matched skills:" in feature_input
        output = _output_of(update)
        assert output.actions == report.actions
        assert output.rewrites == report.rewrites
        assert output.degraded is False
        assert output.derived_from_degraded_input is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED

    async def test_degraded_profile_marks_derived_from_degraded_input(self) -> None:
        # Requirement 6.5: same schema, no shape-branching — the degraded
        # profile is serialized identically and only the output marker
        # changes.
        orchestrator = _FakeOrchestrator(_success_outcome(_report()))
        agent = ImprovementAgent(_deps(), orchestrator)
        update = await agent(_state(profile_degraded=True))

        output = _output_of(update)
        assert output.derived_from_degraded_input is True
        assert output.degraded is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED


class TestDegradedPath:
    async def test_fallback_envelope_routes_to_build_degraded(self) -> None:
        # Requirement 6.4: an LLM failure surfaces as the agent's own
        # Degraded_Output — stored suggestions + missing skills, empty
        # rewrites, degraded=True — and the graph continues.
        orchestrator = _FakeOrchestrator(_fallback_outcome())
        agent = ImprovementAgent(_deps(), orchestrator)
        update = await agent(_state())

        output = _output_of(update)
        assert output.degraded is True
        assert output.rewrites == []
        texts = [action.text for action in output.actions]
        assert texts[:2] == ["Add Kubernetes experience", "Quantify outcomes"]
        assert any("kubernetes" in text for text in texts[2:])
        assert any("terraform" in text for text in texts[2:])
        assert [action.rank for action in output.actions] == [1, 2, 3, 4]

        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "error"

    def test_build_degraded_marks_derived_from_degraded_input(self) -> None:
        agent = ImprovementAgent(_deps(), _FakeOrchestrator(_fallback_outcome()))
        output = agent.build_degraded(_state(profile_degraded=True))
        assert output.degraded is True
        assert output.derived_from_degraded_input is True

    async def test_missing_snapshot_yields_minimal_output(self) -> None:
        # Requirement 8.6: run and build_degraded both need the snapshot,
        # so a missing snapshot falls through to the minimal output.
        agent = ImprovementAgent(_deps(), _FakeOrchestrator(_success_outcome(_report())))
        update = await agent(_state(with_snapshot=False))

        output = _output_of(update)
        assert output == ImprovementReport(actions=[], rewrites=[], degraded=True)
        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "degraded_construction_error"

    def test_build_minimal_is_schema_valid_without_state(self) -> None:
        output = ImprovementAgent(_deps(), _FakeOrchestrator(_fallback_outcome())).build_minimal()
        assert output == ImprovementReport(actions=[], rewrites=[], degraded=True)


class TestHierarchyContract:
    def test_extends_llm_agent(self) -> None:
        assert issubclass(ImprovementAgent, LLMAgent)

    def test_does_not_override_call_or_run(self) -> None:
        assert ImprovementAgent.__call__ is BaseAgent.__call__
        assert ImprovementAgent.run is LLMAgent.run  # type: ignore[comparison-overlap]

    def test_identity_classvars(self) -> None:
        assert ImprovementAgent.name == "improvement"
        assert ImprovementAgent.output_field == "improvement_report"

    async def test_output_lands_on_the_improvement_report_state_field(self) -> None:
        # Requirement 6.6: the Synthesizer consumes improvement_report
        # from state.
        agent = ImprovementAgent(_deps(), _FakeOrchestrator(_success_outcome(_report())))
        update = await agent(_state())
        assert set(update) == {"improvement_report", "agent_status"}
        parsed = AgentState.model_validate(
            {"job_id": "j", "match_id": "m", "user_id": "u", **update}
        )
        assert parsed.improvement_report is not None
