"""Feature: phase-4-agentic — Property 10.

Property 10: Analysis_Result completeness and status fidelity.

    *For any* combination of upstream agent outputs (each independently
    normal or degraded) and per-agent status flags, the Synthesizer's
    ``Analysis_Result`` contains the ATS score, breakdown,
    Confidence_Level, and Scorer_Version; every Skill_Gap_Report entry;
    every Improvement_Report action and rewrite; the Candidate_Profile;
    and exactly one trace summary per contributing agent whose status
    equals that agent's state flag, whose latency is a non-negative
    integer, and which carries the structured failure reason exactly
    when the agent degraded.

**Validates: Requirements 7.1, 7.2, 7.4, 7.5**

The generator produces the state a *successful* graph run hands the
Synthesizer: all four upstream outputs present — each independently
normal or degraded (Requirement 7.2: degraded upstream output is still
assembled, never dropped) — and all four status flags present, each
flag's structured failure reason present exactly when its status is
``degraded`` (that is what ``BaseAgent.__call__`` writes: ``reason`` is
non-null iff the agent took its degraded path). The property then drives
the SynthesizerAgent through its full ``__call__`` lifecycle and checks
the assembled result against the state field-for-field:

* **Completeness (Requirements 7.1, 7.4):** ``result.ats`` equals the
  upstream ``ATSOutput`` — score, breakdown, Confidence_Level, and
  Scorer_Version; ``result.skill_gaps`` carries every Skill_Gap_Report
  entry; ``result.improvements`` carries every Improvement_Report action
  *and* rewrite; ``result.profile`` equals the Candidate_Profile.
  Model-dump equality is the strongest form of each containment clause.
* **Trace fidelity (Requirements 7.2, 7.5):** exactly one
  ``AgentTraceSummary`` per contributing agent (the four upstream
  agents, no more, no fewer, no duplicates); per summary the status
  equals that agent's state flag, the latency is a non-negative ``int``
  equal to the lifecycle-recorded flag value, and the structured failure
  reason is carried exactly when the agent degraded.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 10: Analysis_Result completeness and status fidelity

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    AnalysisResult,
    ATSOutput,
    CandidateProfile,
    ExperienceEntry,
    FailureDetail,
    ImprovementAction,
    ImprovementReport,
    RewriteSuggestion,
    SkillGapEntry,
    SkillGapReport,
)
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent

_UPSTREAM_AGENTS = ("resume_analysis", "ats", "skill_gap", "improvement")

# ---------------------------------------------------------------------------
# Fakes: pure lifecycle dependencies.
# ---------------------------------------------------------------------------


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
    del agent_name, state, output, status, reason, latency_ms


def _deps() -> AgentDeps:
    return AgentDeps(
        node_timeout_s=5.0,
        persist_agent_run=_persist_noop,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )


# ---------------------------------------------------------------------------
# Generators: every upstream output present, independently normal/degraded;
# every status flag present, failure reason iff degraded.
# ---------------------------------------------------------------------------

_skill = st.one_of(
    st.sampled_from(["python", "aws", "sql", "react", "kubernetes", "go"]),
    st.text(alphabet="abcABC+# ", min_size=1, max_size=8),
)

_profiles = st.builds(
    CandidateProfile,
    sections=st.lists(st.text(max_size=15), max_size=3),
    skills=st.lists(_skill, max_size=8),
    experiences=st.lists(
        st.builds(
            ExperienceEntry,
            role=st.none() | st.text(max_size=15),
            organization=st.none() | st.text(max_size=15),
            duration=st.none() | st.text(max_size=10),
        ),
        max_size=3,
    ),
    gaps=st.lists(st.text(max_size=15), max_size=3),
    degraded=st.booleans(),
    derived_from_degraded_input=st.booleans(),
)

_ats_outputs = st.builds(
    ATSOutput,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.dictionaries(
        st.sampled_from(["similarity", "keyword", "semantic"]),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        max_size=3,
    ),
    confidence=st.sampled_from(["high", "medium", "low"]),
    scorer_version=st.sampled_from(["1.0.0", "2.0.0+lexv2+embX+spacyY"]),
    degraded=st.booleans(),
)

_gap_reports = st.builds(
    lambda skills, degraded, derived: SkillGapReport(
        gaps=[
            SkillGapEntry(
                skill=skill,
                classification="missing" if rank % 2 else "weak",
                rank=rank,
            )
            for rank, skill in enumerate(skills, start=1)
        ],
        degraded=degraded,
        derived_from_degraded_input=derived,
    ),
    st.lists(_skill, unique=True, max_size=6),
    st.booleans(),
    st.booleans(),
)

_improvement_reports = st.builds(
    ImprovementReport,
    actions=st.lists(
        st.builds(ImprovementAction, rank=st.integers(1, 10), text=st.text(max_size=30)),
        max_size=4,
    ),
    rewrites=st.lists(
        st.builds(
            RewriteSuggestion,
            excerpt=st.text(max_size=30),
            replacement=st.text(max_size=30),
            rationale=st.text(max_size=30),
        ),
        max_size=3,
    ),
    degraded=st.booleans(),
    derived_from_degraded_input=st.booleans(),
)

_failure_details = st.builds(
    FailureDetail,
    trigger=st.sampled_from(
        [
            "error",
            "timeout",
            "schema_validation",
            "quota_exhausted",
            "breaker_open",
            "empty_input",
            "degraded_construction_error",
        ]
    ),
    detail=st.none() | st.text(max_size=40),
)


@st.composite
def _status_flags(draw: st.DrawFn) -> AgentStatusFlag:
    """A lifecycle-written flag: failure reason present iff degraded.

    Mirrors ``BaseAgent.__call__``: ``reason`` is non-null exactly when
    the agent took its degraded path — the precondition under which
    Requirement 7.5's "carries the failure reason exactly when the agent
    degraded" is the pass-through the trace summaries must preserve.
    """
    status = draw(st.sampled_from(list(AgentCompletion)))
    reason = draw(_failure_details) if status is AgentCompletion.DEGRADED else None
    return AgentStatusFlag(
        status=status,
        failure_reason=reason,
        latency_ms=draw(st.integers(min_value=0, max_value=30_000)),
    )


@st.composite
def _synthesizer_input_states(draw: st.DrawFn) -> AgentState:
    """The state a successful graph run hands the Synthesizer.

    All four upstream outputs present (each independently normal or
    degraded — any combination), all four status flags present.
    """
    return AgentState(
        job_id=draw(st.uuids(version=4).map(str)),
        match_id=draw(st.uuids(version=4).map(str)),
        user_id=draw(st.uuids(version=4).map(str)),
        redacted_resume_text=draw(st.none() | st.text(max_size=100)),
        job_description_skills=draw(st.lists(_skill, max_size=10)),
        candidate_profile=draw(_profiles),
        ats_output=draw(_ats_outputs),
        skill_gap_report=draw(_gap_reports),
        improvement_report=draw(_improvement_reports),
        agent_status={name: draw(_status_flags()) for name in _UPSTREAM_AGENTS},
    )


# ---------------------------------------------------------------------------
# Driving the Synthesizer through its full lifecycle.
# ---------------------------------------------------------------------------


async def _synthesize(state: AgentState) -> AnalysisResult:
    update = await SynthesizerAgent(_deps())(state.model_copy(deep=True))
    result = update["analysis_result"]
    assert isinstance(result, AnalysisResult)
    return result


def _run_sync[T](coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        return runner.run(coro_factory())


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 10: Analysis_Result completeness and status fidelity
@settings(max_examples=120, deadline=None)
@given(state=_synthesizer_input_states())
def test_analysis_result_completeness_and_status_fidelity(state: AgentState) -> None:
    """The assembled AnalysisResult carries all four upstream outputs
    field-for-field (Requirements 7.1, 7.4) — normal and degraded alike
    (Requirement 7.2) — and exactly one trace summary per contributing
    agent mirroring that agent's state flag (Requirements 7.2, 7.5)."""
    result = _run_sync(lambda: _synthesize(state))

    # ---- completeness (Requirements 7.1, 7.4) ----------------------------
    # ATS score, breakdown, Confidence_Level, Scorer_Version — the whole
    # output, degraded marker included (Requirement 7.2).
    assert state.ats_output is not None
    assert result.ats.model_dump() == state.ats_output.model_dump()
    # Every Skill_Gap_Report entry, ordering and ranks intact.
    assert state.skill_gap_report is not None
    assert result.skill_gaps.model_dump() == state.skill_gap_report.model_dump()
    # Every Improvement_Report action and rewrite.
    assert state.improvement_report is not None
    assert result.improvements.model_dump() == state.improvement_report.model_dump()
    # The Candidate_Profile.
    assert state.candidate_profile is not None
    assert result.profile.model_dump() == state.candidate_profile.model_dump()

    # ---- trace fidelity (Requirements 7.2, 7.5) ---------------------------
    # Exactly one summary per contributing agent: no absences, no
    # duplicates, no extras (the Synthesizer itself carries none).
    names = [trace.agent_name for trace in result.agent_traces]
    assert sorted(names) == sorted(_UPSTREAM_AGENTS)

    for trace in result.agent_traces:
        flag = state.agent_status[trace.agent_name]
        # Status equals that agent's state flag.
        assert trace.status == flag.status
        # Latency is a non-negative integer equal to the recorded value.
        assert isinstance(trace.latency_ms, int)
        assert trace.latency_ms >= 0
        assert trace.latency_ms == flag.latency_ms
        # The structured failure reason travels exactly when the agent
        # degraded — present with the flag's content on degraded, absent
        # on completed.
        if trace.status is AgentCompletion.DEGRADED:
            assert trace.failure_reason is not None
            assert flag.failure_reason is not None
            assert trace.failure_reason.model_dump() == flag.failure_reason.model_dump()
        else:
            assert trace.failure_reason is None
