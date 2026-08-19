"""Unit tests for the ATSAgent (phase-4-agentic task 5.2).

Covers the contract in ``ml/agents/ats_agent.py`` with a fake scorer
adapter (no models, no database):

* Reuse path — persisted ``scorer_version`` equals the active version →
  persisted score/breakdown reused with zero scorer invocations
  (Requirement 4.3), Confidence_Level still derived and attached
  (Requirement 4.2 via the ``confidence.py`` rule).
* Fresh path — version mismatch → exactly one adapter ``score()`` call,
  output carries the fresh score, breakdown, and the producing engine's
  Scorer_Version (Requirements 4.1, 4.6).
* Degraded_Mode fallback scores (``semantic=False``) are never tagged
  ``high`` (Requirement 4.5).
* Total scoring failure → the lifecycle degrades to the persisted score
  fields with ``confidence="low"``, ``degraded=True`` (Requirement 4.4).
* Missing MatchSnapshot → minimal schema-valid output with the
  ``degraded_construction_error`` trigger (Requirement 8.6).
* Hierarchy contract — extends ``DeterministicAgent``, does not override
  ``__call__``, writes the ``ats_output`` state field (Requirements 1.6,
  4.6).
"""

from __future__ import annotations

from pathlib import Path

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    ATSOutput,
    FailureDetail,
    MatchSnapshot,
)

# In-bounds lengths for the confidence rule (200<=resume<=50_000, 100<=jd<=20_000).
_RESUME_LEN_OK = 1_000
_JD_LEN_OK = 500

_ACTIVE_VERSION = "2.0.0+lexv2+emb-minilm+spacy-3.8"
_STALE_VERSION = "1.0.0"


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


class _FakeScorer:
    """Configurable ScorerAdapter fake; records score() invocations."""

    def __init__(
        self,
        *,
        active_scorer_version: str = _ACTIVE_VERSION,
        semantic_active: bool = True,
        resume_len: int = _RESUME_LEN_OK,
        jd_len: int = _JD_LEN_OK,
        result: ScoredMatch | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self.active_scorer_version = active_scorer_version
        self.semantic_active = semantic_active
        self.resume_len = resume_len
        self.jd_len = jd_len
        self._result = result
        self._raises = raises
        self.score_calls = 0

    async def score(self) -> ScoredMatch:
        self.score_calls += 1
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


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


def _snapshot(scorer_version: str = _ACTIVE_VERSION) -> MatchSnapshot:
    return MatchSnapshot(
        score=71.5,
        breakdown={"similarity": 0.6, "keyword": 0.8},
        scorer_version=scorer_version,
        matched_skills=["python"],
        missing_skills=["kubernetes"],
        suggestions=["Add Kubernetes experience"],
    )


def _state(*, scorer_version: str = _ACTIVE_VERSION, with_snapshot: bool = True) -> AgentState:
    return AgentState(
        job_id="job-1",
        match_id="match-1",
        user_id="user-1",
        match_snapshot=_snapshot(scorer_version) if with_snapshot else None,
    )


def _output_of(update: dict[str, object]) -> ATSOutput:
    output = update["ats_output"]
    assert isinstance(output, ATSOutput)
    return output


def _flag_of(update: dict[str, object]) -> AgentStatusFlag:
    status_map = update["agent_status"]
    assert isinstance(status_map, dict)
    flag = status_map["ats"]
    assert isinstance(flag, AgentStatusFlag)
    return flag


class TestReusePath:
    async def test_matching_version_reuses_persisted_score_without_scoring(self) -> None:
        # Requirement 4.3: persisted version == active version → no invocation.
        scorer = _FakeScorer(semantic_active=True)
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version=_ACTIVE_VERSION))

        assert scorer.score_calls == 0
        output = _output_of(update)
        assert output.score == 71.5
        assert output.breakdown == {"similarity": 0.6, "keyword": 0.8}
        assert output.scorer_version == _ACTIVE_VERSION
        assert output.degraded is False
        assert _flag_of(update).status is AgentCompletion.COMPLETED

    async def test_reuse_still_attaches_confidence(self) -> None:
        # Requirement 4.3: confidence is derived even without scoring —
        # semantic active + in-bounds lengths → high.
        scorer = _FakeScorer(semantic_active=True)
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version=_ACTIVE_VERSION))
        assert _output_of(update).confidence == "high"

    async def test_reused_phase1_score_is_not_high(self) -> None:
        # Requirement 4.5 on the reuse path: the active scorer is the
        # Phase 1 engine (Degraded_Mode) → semantic=False → never high.
        scorer = _FakeScorer(active_scorer_version=_STALE_VERSION, semantic_active=False)
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version=_STALE_VERSION))

        assert scorer.score_calls == 0
        assert _output_of(update).confidence == "medium"  # lengths in bounds only


class TestFreshScoringPath:
    async def test_version_mismatch_invokes_scorer_once(self) -> None:
        # Requirement 4.1: stale persisted version → one adapter invocation.
        scored = ScoredMatch(
            score=83.0,
            breakdown={"similarity": 0.9, "keyword": 0.7},
            scorer_version=_ACTIVE_VERSION,
            semantic=True,
        )
        scorer = _FakeScorer(result=scored)
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version=_STALE_VERSION))

        assert scorer.score_calls == 1
        output = _output_of(update)
        assert output.score == 83.0
        assert output.breakdown == {"similarity": 0.9, "keyword": 0.7}
        assert output.scorer_version == _ACTIVE_VERSION
        assert output.confidence == "high"
        assert output.degraded is False

    async def test_ladder_fallback_score_is_never_high(self) -> None:
        # Requirement 4.5: a Degraded_Mode fallback rung produced the score
        # (semantic=False, Phase 1 version stamp) → confidence caps at medium.
        scored = ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.5, "keyword": 0.6},
            scorer_version=_STALE_VERSION,
            semantic=False,
        )
        scorer = _FakeScorer(result=scored)
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version="0.9.0"))

        output = _output_of(update)
        assert output.confidence == "medium"
        assert output.scorer_version == _STALE_VERSION  # the true producer's stamp

    async def test_out_of_bounds_lengths_lower_confidence(self) -> None:
        # Requirement 4.2: semantic but resume too short → medium.
        scored = ScoredMatch(
            score=60.0, breakdown={}, scorer_version=_ACTIVE_VERSION, semantic=True
        )
        scorer = _FakeScorer(result=scored, resume_len=50)
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version=_STALE_VERSION))
        assert _output_of(update).confidence == "medium"


class TestDegradedPath:
    async def test_total_scoring_failure_degrades_to_persisted_fields(self) -> None:
        # Requirement 4.4: scorer raises after exhausting its ladder →
        # persisted score fields, confidence low, marked degraded.
        scorer = _FakeScorer(raises=RuntimeError("every rung failed"))
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(scorer_version=_STALE_VERSION))

        output = _output_of(update)
        assert output.score == 71.5
        assert output.breakdown == {"similarity": 0.6, "keyword": 0.8}
        assert output.scorer_version == _STALE_VERSION
        assert output.confidence == "low"
        assert output.degraded is True

        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "error"

    async def test_missing_snapshot_yields_minimal_output(self) -> None:
        # Requirement 8.6: run and build_degraded both need the snapshot,
        # so a missing snapshot falls through to the minimal output.
        scorer = _FakeScorer()
        agent = ATSAgent(_deps(), scorer)
        update = await agent(_state(with_snapshot=False))

        output = _output_of(update)
        assert output.degraded is True
        assert output.confidence == "low"
        assert output.score == 0.0
        assert output.scorer_version == "unknown"

        flag = _flag_of(update)
        assert flag.status is AgentCompletion.DEGRADED
        assert flag.failure_reason is not None
        assert flag.failure_reason.trigger == "degraded_construction_error"

    def test_build_degraded_carries_persisted_fields(self) -> None:
        agent = ATSAgent(_deps(), _FakeScorer())
        output = agent.build_degraded(_state())
        assert output == ATSOutput(
            score=71.5,
            breakdown={"similarity": 0.6, "keyword": 0.8},
            confidence="low",
            scorer_version=_ACTIVE_VERSION,
            degraded=True,
        )

    def test_build_minimal_is_schema_valid_without_state(self) -> None:
        output = ATSAgent(_deps(), _FakeScorer()).build_minimal()
        assert output.degraded is True
        assert output.confidence == "low"


class TestHierarchyContract:
    def test_extends_deterministic_agent(self) -> None:
        # Requirement 1.6: no LLM dependency by construction.
        assert issubclass(ATSAgent, DeterministicAgent)

    def test_does_not_override_call(self) -> None:
        assert ATSAgent.__call__ is BaseAgent.__call__

    def test_identity_classvars(self) -> None:
        assert ATSAgent.name == "ats"
        assert ATSAgent.output_field == "ats_output"

    async def test_output_lands_on_the_ats_output_state_field(self) -> None:
        # Requirement 4.6: the Synthesizer consumes ats_output from state.
        agent = ATSAgent(_deps(), _FakeScorer())
        update = await agent(_state())
        assert set(update) == {"ats_output", "agent_status"}
        parsed = AgentState.model_validate(
            {"job_id": "j", "match_id": "m", "user_id": "u", **update}
        )
        assert parsed.ats_output is not None


class TestModuleImportBoundary:
    def test_module_source_has_no_llm_imports(self) -> None:
        """No import statement in ats_agent.py reaches an LLM package.

        AST walk (not text grep) mirroring the DeterministicAgent module
        test, so docstring prose naming the banned packages cannot
        false-positive. Structural half of Requirement 1.6; the dynamic
        no-LLM-call property is task 5.7.
        """
        import ast

        import matchlayer_api.ml.agents.ats_agent as module

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
            f"ats_agent.py must import nothing from ml/llm/ or services/llm/ "
            f"(Requirement 1.6); found: {offenders}"
        )
