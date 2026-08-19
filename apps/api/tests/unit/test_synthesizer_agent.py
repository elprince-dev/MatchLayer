"""Unit tests for the SynthesizerAgent (phase-4-agentic task 5.5).

Covers the contract in ``ml/agents/synthesizer.py`` with fake deps (no
models, no database, no LLM):

* Assembly — the four upstream outputs land verbatim on the
  AnalysisResult, plus one AgentTraceSummary per contributing agent with
  name, status from the state flags, lifecycle-recorded latency, and the
  structured failure reason exactly when the agent degraded
  (Requirements 7.1, 7.2, 7.4, 7.5).
* Determinism — field-for-field identical state yields a field-for-field
  identical AnalysisResult across repeated invocations (Requirement 7.3).
* The exception to degradation — any ``run`` failure propagates out of
  ``__call__`` instead of degrading, and ``build_degraded`` /
  ``build_minimal`` raise ``NotImplementedError`` (Requirement 7.6).
* Hierarchy contract — extends ``DeterministicAgent``, does not override
  ``__call__``, writes the ``analysis_result`` state field
  (Requirements 1.6, 7.1).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    AnalysisResult,
    ATSOutput,
    CandidateProfile,
    FailureDetail,
    ImprovementAction,
    ImprovementReport,
    SkillGapEntry,
    SkillGapReport,
)
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


class _PersistSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentCompletion, FailureDetail | None]] = []

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        self.calls.append((agent_name, status, reason))


def _deps(persist: _PersistSpy | None = None) -> AgentDeps:
    return AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=persist if persist is not None else _PersistSpy(),
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )


def _flags(*, improvement_degraded: bool = False) -> dict[str, AgentStatusFlag]:
    improvement_flag = (
        AgentStatusFlag(
            status=AgentCompletion.DEGRADED,
            failure_reason=FailureDetail(trigger="timeout", detail="node timed out"),
            latency_ms=20_000,
        )
        if improvement_degraded
        else AgentStatusFlag(status=AgentCompletion.COMPLETED, latency_ms=850)
    )
    return {
        "resume_analysis": AgentStatusFlag(status=AgentCompletion.COMPLETED, latency_ms=1_200),
        "ats": AgentStatusFlag(status=AgentCompletion.COMPLETED, latency_ms=40),
        "skill_gap": AgentStatusFlag(status=AgentCompletion.COMPLETED, latency_ms=5),
        "improvement": improvement_flag,
    }


def _state(
    *,
    improvement_degraded: bool = False,
    drop_output: str | None = None,
    drop_flag: str | None = None,
) -> AgentState:
    state = AgentState(
        job_id="job-1",
        match_id="match-1",
        user_id="user-1",
        candidate_profile=CandidateProfile(skills=["python"]),
        ats_output=ATSOutput(
            score=71.5,
            breakdown={"similarity": 0.6},
            confidence="high",
            scorer_version="2.0.0+lexv2+emb-minilm+spacy-3.8",
        ),
        skill_gap_report=SkillGapReport(
            gaps=[SkillGapEntry(skill="kubernetes", classification="missing", rank=1)]
        ),
        improvement_report=ImprovementReport(
            actions=[ImprovementAction(rank=1, text="Add a Kubernetes project.")],
            degraded=improvement_degraded,
        ),
        agent_status=_flags(improvement_degraded=improvement_degraded),
    )
    if drop_output is not None:
        setattr(state, drop_output, None)
    if drop_flag is not None:
        del state.agent_status[drop_flag]
    return state


def _result_of(update: dict[str, object]) -> AnalysisResult:
    result = update["analysis_result"]
    assert isinstance(result, AnalysisResult)
    return result


class TestAssembly:
    async def test_combines_the_four_upstream_outputs_verbatim(self) -> None:
        # Requirements 7.1, 7.4: the AnalysisResult carries the ATS output,
        # every skill-gap entry, every improvement entry, and the profile.
        state = _state()
        update = await SynthesizerAgent(_deps())(state)

        result = _result_of(update)
        assert result.ats == state.ats_output
        assert result.skill_gaps == state.skill_gap_report
        assert result.improvements == state.improvement_report
        assert result.profile == state.candidate_profile

    async def test_one_trace_summary_per_contributing_agent(self) -> None:
        # Requirements 7.2, 7.5: name, status from the state flags,
        # lifecycle-recorded latency, and no failure reason when normal.
        update = await SynthesizerAgent(_deps())(_state())

        traces = _result_of(update).agent_traces
        assert [trace.agent_name for trace in traces] == [
            "resume_analysis",
            "ats",
            "skill_gap",
            "improvement",
        ]
        assert all(trace.status is AgentCompletion.COMPLETED for trace in traces)
        assert [trace.latency_ms for trace in traces] == [1_200, 40, 5, 850]
        assert all(trace.failure_reason is None for trace in traces)

    async def test_degraded_agent_trace_carries_its_failure_reason(self) -> None:
        # Requirements 7.2, 7.5: degraded status and the structured
        # failure reason surface exactly for the degraded agent.
        update = await SynthesizerAgent(_deps())(_state(improvement_degraded=True))

        traces = {trace.agent_name: trace for trace in _result_of(update).agent_traces}
        assert traces["improvement"].status is AgentCompletion.DEGRADED
        assert traces["improvement"].failure_reason is not None
        assert traces["improvement"].failure_reason.trigger == "timeout"
        assert traces["improvement"].latency_ms == 20_000
        assert traces["ats"].status is AgentCompletion.COMPLETED
        assert traces["ats"].failure_reason is None

    async def test_deterministic_over_identical_state(self) -> None:
        # Requirement 7.3: no I/O, no clock, no randomness — repeated
        # invocations over field-for-field identical state produce
        # field-for-field identical results.
        agent = SynthesizerAgent(_deps())
        first = _result_of(await agent(_state(improvement_degraded=True)))
        second = _result_of(await agent(_state(improvement_degraded=True)))
        assert first == second


class TestFailurePropagation:
    @pytest.mark.parametrize(
        "drop_output",
        ["candidate_profile", "ats_output", "skill_gap_report", "improvement_report"],
    )
    async def test_missing_upstream_output_fails_the_invocation(self, drop_output: str) -> None:
        # Requirement 7.6: no degraded path — the failure propagates out
        # of __call__ so the worker fails the job.
        persist = _PersistSpy()
        agent = SynthesizerAgent(_deps(persist))
        with pytest.raises(ValueError, match=drop_output):
            await agent(_state(drop_output=drop_output))
        # The lifecycle exited via the re-raise before persistence: the
        # worker persists the failed Agent_Run row instead (Req 12.7).
        assert persist.calls == []

    async def test_missing_upstream_status_flag_fails_the_invocation(self) -> None:
        agent = SynthesizerAgent(_deps())
        with pytest.raises(ValueError, match="improvement"):
            await agent(_state(drop_flag="improvement"))

    def test_build_degraded_and_build_minimal_are_unreachable(self) -> None:
        # Requirement 7.6 (design hierarchy notes): the Synthesizer has no
        # degraded path; both builders raise and are never reached.
        agent = SynthesizerAgent(_deps())
        with pytest.raises(NotImplementedError):
            agent.build_degraded(_state())
        with pytest.raises(NotImplementedError):
            agent.build_minimal()


class TestHierarchyContract:
    def test_extends_deterministic_agent(self) -> None:
        # Requirement 1.6: no LLM dependency by construction.
        assert issubclass(SynthesizerAgent, DeterministicAgent)

    def test_does_not_override_call(self) -> None:
        assert SynthesizerAgent.__call__ is BaseAgent.__call__

    def test_overrides_build_degraded_safely_to_re_raise(self) -> None:
        # The one sanctioned lifecycle override (Requirement 7.6).
        assert SynthesizerAgent._build_degraded_safely is not BaseAgent._build_degraded_safely

    def test_identity_classvars(self) -> None:
        assert SynthesizerAgent.name == "synthesizer"
        assert SynthesizerAgent.output_field == "analysis_result"

    async def test_output_lands_on_the_analysis_result_state_field(self) -> None:
        update = await SynthesizerAgent(_deps())(_state())
        assert set(update) == {"analysis_result", "agent_status"}
        parsed = AgentState.model_validate(
            {"job_id": "j", "match_id": "m", "user_id": "u", **_state().model_dump(), **update}
        )
        assert parsed.analysis_result is not None


class TestModuleImportBoundary:
    def test_module_source_has_no_llm_imports(self) -> None:
        """No import statement in synthesizer.py reaches an LLM package.

        AST walk (not text grep) mirroring the DeterministicAgent module
        test. Structural half of Requirement 1.6; the dynamic no-LLM-call
        property is task 5.7.
        """
        import ast

        import matchlayer_api.ml.agents.synthesizer as module

        assert module.__file__ is not None
        path = Path(module.__file__)
        forbidden = ("matchlayer_api.ml.llm", "matchlayer_api.services.llm")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    alias.name
                    for alias in node.names
                    if any(alias.name == p or alias.name.startswith(f"{p}.") for p in forbidden)
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and any(node.module == p or node.module.startswith(f"{p}.") for p in forbidden)
            ):
                offenders.append(node.module)
        assert not offenders, (
            f"synthesizer.py must import nothing from ml/llm/ or services/llm/ "
            f"(Requirement 1.6); found: {offenders}"
        )
