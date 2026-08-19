"""Feature: phase-4-agentic — Property 3.

Property 3: Deterministic agents make no LLM call.

    *For any* valid ``AgentState``, executing the ATS_Agent, Skill_Gap_Agent,
    or Synthesizer records zero invocations on a spy ``LLMClient``.

**Validates: Requirements 1.6, 5.1, 7.1**

Instrumentation: every real LLM entry point is replaced with a sentinel
that records the touch and raises — construction and both pipeline phases
of the Phase 3 ``LLMOrchestrator`` (the one sanctioned gateway, design D1)
plus construction and every ``LLMClient`` protocol method of the
``OpenRouterClient`` (the only concrete provider adapter). The three
deterministic agents are then driven through their full ``__call__``
lifecycle (normal, degraded, and — for the Synthesizer — failure paths
alike) over generated states, and the property asserts the sentinel log
stays empty on every path.

The generated states span the whole input space the deterministic branch
can see: present/absent MatchSnapshot (so ATS reuse, fresh-score, and
degraded-construction paths all occur), normal/degraded/absent
Candidate_Profile (Skill_Gap's three branches, Requirement 5.1's pure
input closure), matching/mismatching scorer versions, arbitrary skill
lists, and complete/incomplete upstream outputs and status flags (so the
Synthesizer both assembles successfully — Requirement 7.1 — and re-raises
per Requirement 7.6, still without any LLM interaction).

The complementary *structural* half of Requirement 1.6 — no LLM import in
any deterministic module, no constructor slot for an orchestrator — is
the hierarchy contract suite (task 5.6,
``tests/unit/test_agent_hierarchy_contract.py``) plus
``tests/unit/test_deterministic_agent.py``.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 3: Deterministic agents make no LLM call

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from typing import Any
from unittest import mock

from hypothesis import example, given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
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
from matchlayer_api.ml.llm.openrouter import OpenRouterClient
from matchlayer_api.services.llm.orchestrator import LLMOrchestrator

# ---------------------------------------------------------------------------
# The LLM sentinel: every real entry point records and fails when touched.
# ---------------------------------------------------------------------------


class _LLMTouchedError(AssertionError):
    """A deterministic agent reached the LLM machinery (Property 3 violation)."""


# (owner class, attribute) pairs covering construction and every call path:
# the orchestrator is the single sanctioned gateway (design D1) and
# OpenRouterClient is the only concrete LLMClient adapter.
_LLM_ENTRY_POINTS: tuple[tuple[type, str], ...] = (
    (LLMOrchestrator, "__init__"),
    (LLMOrchestrator, "prepare"),
    (LLMOrchestrator, "execute"),
    (OpenRouterClient, "__init__"),
    (OpenRouterClient, "validate_credentials"),
    (OpenRouterClient, "stream"),
    (OpenRouterClient, "result"),
)


def _sentinel(label: str, calls: list[str]) -> Callable[..., Any]:
    def _record(*args: Any, **kwargs: Any) -> Any:
        calls.append(label)
        raise _LLMTouchedError(f"{label} invoked during a deterministic agent run (Property 3)")

    return _record


# ---------------------------------------------------------------------------
# Fakes: deps and the ATS scorer adapter (pure values, no LLM anywhere).
# ---------------------------------------------------------------------------

_ACTIVE_SCORER_VERSION = "2.0.0+test"


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


class _FakeScorer:
    """ScorerAdapter fake: deterministic values, exercises reuse and rescore."""

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
# Generators: the full valid-AgentState space the deterministic branch sees.
# ---------------------------------------------------------------------------

_skill = st.one_of(
    st.sampled_from(["python", "Python", "aws", "sql", "react", "kubernetes", "go"]),
    st.text(alphabet="abcABC+# ", min_size=1, max_size=8),
)

# Mix the adapter's active version with stale ones so the ATS agent takes
# both its reuse path (versions equal) and its fresh-score path.
_scorer_version = st.sampled_from([_ACTIVE_SCORER_VERSION, "1.0.0", "2.0.0+lexv2"])

_snapshots = st.builds(
    MatchSnapshot,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.dictionaries(
        st.sampled_from(["similarity", "keyword"]),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        max_size=2,
    ),
    scorer_version=_scorer_version,
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
    scorer_version=_scorer_version,
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
    Synthesizer, failure) paths; present ones exercise the normal paths —
    the property must hold on all of them.
    """
    # Upstream status flags: any subset of the four contributing agents.
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


def _complete_state() -> AgentState:
    """A fully-populated state: the Synthesizer's success path."""
    return AgentState(
        job_id="00000000-0000-4000-8000-000000000001",
        match_id="00000000-0000-4000-8000-000000000002",
        user_id="00000000-0000-4000-8000-000000000003",
        redacted_resume_text="[NAME_1] built services in Python.",
        job_description_skills=["python", "kubernetes"],
        match_snapshot=MatchSnapshot(
            score=71.5,
            breakdown={"similarity": 0.6},
            scorer_version=_ACTIVE_SCORER_VERSION,
            matched_skills=["python"],
            missing_skills=["kubernetes"],
            suggestions=["Add a Kubernetes project."],
        ),
        candidate_profile=CandidateProfile(skills=["python"]),
        ats_output=ATSOutput(score=71.5, confidence="high", scorer_version=_ACTIVE_SCORER_VERSION),
        skill_gap_report=SkillGapReport(
            gaps=[SkillGapEntry(skill="kubernetes", classification="missing", rank=1)]
        ),
        improvement_report=ImprovementReport(
            actions=[ImprovementAction(rank=1, text="Add a Kubernetes project.")]
        ),
        agent_status={
            name: AgentStatusFlag(status=AgentCompletion.COMPLETED, latency_ms=10)
            for name in ("resume_analysis", "ats", "skill_gap", "improvement")
        },
    )


# ---------------------------------------------------------------------------
# Driving the three deterministic agents through their full lifecycle.
# ---------------------------------------------------------------------------


async def _drive_deterministic_agents(state: AgentState) -> None:
    """Run ATS, Skill_Gap, and Synthesizer over ``state`` via ``__call__``.

    ATS and Skill_Gap never raise out of the lifecycle (their degradation
    ladder bottoms out at ``build_minimal``). The Synthesizer legitimately
    re-raises on incomplete upstream state (Requirement 7.6) — that raise
    is swallowed here because the property under test is *zero LLM
    interactions*, which the sentinel log asserts either way; a sentinel
    touch is re-raised so the counterexample surfaces immediately.
    """
    deps = _deps()
    await ATSAgent(deps, _FakeScorer())(state.model_copy(deep=True))
    await SkillGapAgent(deps)(state.model_copy(deep=True))
    try:
        await SynthesizerAgent(deps)(state.model_copy(deep=True))
    except _LLMTouchedError:
        raise
    except Exception:  # Req 7.6 re-raise on incomplete state: expected, not under test
        pass


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 3: Deterministic agents make no LLM call
@settings(max_examples=120, deadline=None)
@example(state=_complete_state())  # Synthesizer success path (Req 7.1)
@example(state=AgentState(job_id="j", match_id="m", user_id="u"))  # all-degraded/failure paths
@given(state=_agent_states())
def test_deterministic_agents_record_zero_llm_invocations(state: AgentState) -> None:
    """Zero sentinel touches across ATS, Skill_Gap, and Synthesizer runs.

    Requirement 1.6: the three deterministic agents make no LLM_Provider
    call under any input. Requirement 5.1: the Skill_Gap_Agent is a pure
    function of its three state inputs. Requirement 7.1: the Synthesizer
    assembles upstream outputs without invoking any LLM.
    """
    calls: list[str] = []
    with ExitStack() as stack:
        for owner, attr in _LLM_ENTRY_POINTS:
            stack.enter_context(
                mock.patch.object(owner, attr, _sentinel(f"{owner.__name__}.{attr}", calls))
            )
        _run_sync(lambda: _drive_deterministic_agents(state))
        assert calls == [], (
            f"deterministic agents touched the LLM machinery (Requirement 1.6): {calls}"
        )
