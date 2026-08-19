"""Feature: phase-4-agentic — Property 9.

Property 9: Deterministic agents re-execute to identical output.

    *For any* valid input state, executing the Skill_Gap_Agent or the
    Synthesizer twice on field-for-field identical inputs produces
    field-for-field identical outputs (including ordering and ranks),
    with no dependence on wall-clock time, randomness, or I/O — which is
    also the deterministic half of the run-reproducibility bar
    (re-executing against persisted ``input_state_json`` reproduces the
    persisted ``output_state_json``).

**Validates: Requirements 5.3, 7.3, 12.5**

The ATS_Agent is included alongside the two named agents, exercised on
its **reuse path** through a pure fake ``ScorerAdapter`` (constant
values, no I/O): the persisted-score reuse branch (Requirement 4.3) is
the deterministic re-execution surface the reproducibility contract
covers — a fresh score depends on the Phase 2 scoring service, which
sits outside this property.

Method: for every generated valid ``AgentState``, each agent is driven
through its full ``__call__`` lifecycle **twice**, each time on a deep
copy of the same state and with a **fresh agent instance** (so identical
outputs cannot come from instance-level memoization). The two invocation
outcomes must match field-for-field:

* the written output model — every field, including gap ordering and
  ranks — via ``model_dump()`` equality;
* the recorded ``AgentStatusFlag`` (status + structured failure reason),
  so degraded paths re-degrade identically;
* for the Synthesizer, a raise on the first invocation (its sanctioned
  Requirement 7.6 behaviour on incomplete upstream state) must be the
  identical exception type on the second.

Latency is lifecycle metadata measured on the injected clock, not agent
output; a constant fake clock pins it so the comparison covers exactly
the deterministic surface (design: ``DeterministicAgent.run``
implementations are pure functions — no clock, no randomness, no I/O).

The generated states span normal and degraded paths alike: present or
absent ``MatchSnapshot`` (ATS/Skill_Gap degraded ladders), normal or
degraded or absent ``CandidateProfile`` (Skill_Gap's three branches),
arbitrary skill lists with duplicates and mixed case (the prioritization
tie-breaks of Requirement 5.3), and complete or incomplete upstream
outputs and status flags (the Synthesizer's assembly and re-raise paths,
Requirement 7.3).

Async note: each example drives its coroutines inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 9: Deterministic agents re-execute to identical output

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    ATSOutput,
    CandidateProfile,
    FailureDetail,
    ImprovementAction,
    ImprovementReport,
    MatchSnapshot,
    SkillGapEntry,
    SkillGapReport,
)
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent

# ---------------------------------------------------------------------------
# Fakes: pure dependencies — constant clock, no-op persistence, pure scorer.
# ---------------------------------------------------------------------------

_ACTIVE_SCORER_VERSION = "2.0.0+test"


class _FakeClock:
    """Constant monotonic clock: latency is lifecycle metadata, not output."""

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


class _FakeScorer:
    """Pure ScorerAdapter fake: constant values, no I/O, no randomness.

    ``active_scorer_version`` matches the generated snapshots' version so
    the ATS_Agent takes its persisted-score **reuse** path (Requirement
    4.3) whenever a snapshot is present; a missing snapshot exercises the
    degraded ladder instead.
    """

    active_scorer_version = _ACTIVE_SCORER_VERSION
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    async def score(self) -> ScoredMatch:
        return ScoredMatch(
            score=42.0,
            breakdown={"similarity": 0.42},
            scorer_version=_ACTIVE_SCORER_VERSION,
            semantic=True,
        )


# ---------------------------------------------------------------------------
# Generators: valid AgentStates spanning normal, degraded, and failure paths.
# ---------------------------------------------------------------------------

# Mixed case + duplicates so gap classification, ordering, and rank
# tie-breaking (Requirement 5.3) are exercised, not just trivial inputs.
_skill = st.one_of(
    st.sampled_from(["python", "Python", "aws", "sql", "react", "kubernetes", "go"]),
    st.text(alphabet="abcABC+# ", min_size=1, max_size=8),
)

_snapshots = st.builds(
    MatchSnapshot,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.dictionaries(
        st.sampled_from(["similarity", "keyword"]),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        max_size=2,
    ),
    scorer_version=st.just(_ACTIVE_SCORER_VERSION),
    matched_skills=st.lists(_skill, max_size=6),
    missing_skills=st.lists(_skill, max_size=6),
    suggestions=st.lists(st.text(max_size=20), max_size=3),
)

_profiles = st.builds(
    CandidateProfile,
    skills=st.lists(_skill, max_size=8),
    degraded=st.booleans(),
)

_ats_outputs = st.builds(
    ATSOutput,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.just({}),
    confidence=st.sampled_from(["high", "medium", "low"]),
    scorer_version=st.just(_ACTIVE_SCORER_VERSION),
    degraded=st.booleans(),
)

_gap_reports = st.lists(_skill, unique=True, max_size=5).map(
    lambda skills: SkillGapReport(
        gaps=[
            SkillGapEntry(skill=skill, classification="missing", rank=rank)
            for rank, skill in enumerate(skills, start=1)
        ]
    )
)

_improvement_reports = st.builds(
    ImprovementReport,
    actions=st.lists(
        st.builds(ImprovementAction, rank=st.integers(1, 10), text=st.text(max_size=30)),
        max_size=4,
    ),
    degraded=st.booleans(),
)

_flags = st.builds(
    AgentStatusFlag,
    status=st.sampled_from(list(AgentCompletion)),
    latency_ms=st.integers(min_value=0, max_value=30_000),
)


@st.composite
def _agent_states(draw: st.DrawFn) -> AgentState:
    """Any valid AgentState: inputs and upstream outputs each may be absent.

    Absent inputs push the agents down their degraded (and, for the
    Synthesizer, re-raise) paths; present ones exercise the normal paths
    — re-execution must be identical on every one of them.
    """
    agent_names = ("resume_analysis", "ats", "skill_gap", "improvement")
    flagged = draw(st.sets(st.sampled_from(agent_names)))
    return AgentState(
        job_id=draw(st.uuids(version=4).map(str)),
        match_id=draw(st.uuids(version=4).map(str)),
        user_id=draw(st.uuids(version=4).map(str)),
        redacted_resume_text=draw(st.none() | st.text(max_size=100)),
        job_description_skills=draw(st.lists(_skill, max_size=10)),
        match_snapshot=draw(st.none() | _snapshots),
        candidate_profile=draw(st.none() | _profiles),
        ats_output=draw(st.none() | _ats_outputs),
        skill_gap_report=draw(st.none() | _gap_reports),
        improvement_report=draw(st.none() | _improvement_reports),
        agent_status={name: draw(_flags) for name in flagged},
    )


# ---------------------------------------------------------------------------
# Invocation capture: one lifecycle run reduced to comparable plain data.
# ---------------------------------------------------------------------------

type _Outcome = tuple[str, dict[str, object], dict[str, object]] | tuple[str, str]
"""Either ("ok", output.model_dump(), status_flag.model_dump()) or
("raised", exception type name) — plain data, so ``==`` is exactly
field-for-field comparison."""


async def _invoke_once(
    agent_factory: Callable[[], BaseAgent[BaseModel]], state: AgentState
) -> _Outcome:
    """Drive one fresh agent instance through ``__call__`` on a state copy."""
    agent = agent_factory()
    try:
        update = await agent(state.model_copy(deep=True))
    except Exception as exc:  # Synthesizer re-raise path (Requirement 7.6)
        return ("raised", type(exc).__name__)
    output = update[agent.output_field]
    assert isinstance(output, BaseModel)
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map[agent.name]
    assert isinstance(flag, AgentStatusFlag)
    return ("ok", output.model_dump(), flag.model_dump())


async def _assert_reexecution_identical(state: AgentState) -> None:
    """Each deterministic agent, run twice on identical inputs, matches."""
    factories: dict[str, Callable[[], BaseAgent[BaseModel]]] = {
        "ats": lambda: ATSAgent(_deps(), _FakeScorer()),
        "skill_gap": lambda: SkillGapAgent(_deps()),
        "synthesizer": lambda: SynthesizerAgent(_deps()),
    }
    for name, factory in factories.items():
        first = await _invoke_once(factory, state)
        second = await _invoke_once(factory, state)
        assert first == second, (
            f"{name} re-executed to a different outcome on field-for-field "
            f"identical inputs (Requirements 5.3, 7.3, 12.5):\n"
            f"first:  {first}\nsecond: {second}"
        )


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 9: Deterministic agents re-execute to identical output
@settings(max_examples=120, deadline=None)
@given(state=_agent_states())
def test_deterministic_agents_reexecute_to_identical_output(state: AgentState) -> None:
    """Two invocations on field-for-field identical inputs yield
    field-for-field identical outputs — every output field including gap
    ordering and ranks (Requirement 5.3), the assembled AnalysisResult and
    its trace summaries (Requirement 7.3), and the status flag — which is
    the deterministic half of the run-reproducibility bar (Requirement
    12.5). Fresh instances per invocation rule out memoization; the raise
    path (Synthesizer, Requirement 7.6) must also reproduce identically.
    """
    _run_sync(lambda: _assert_reexecution_identical(state))
