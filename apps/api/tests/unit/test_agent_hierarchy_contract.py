"""Cross-agent hierarchy contract tests (phase-4-agentic, task 5.6).

The authoritative suite for the agent class hierarchy's structural
contract, sweeping all five concrete agents at once (the per-agent unit
suites spot-check their own class only):

* **Exactly one branch** — every concrete agent subclasses exactly one of
  ``LLMAgent`` / ``DeterministicAgent``, and lands on the branch the
  design assigns it (Requirement 1.4: one responsibility per agent, fixed
  by the hierarchy; Requirement 1.6: the LLM/no-LLM split is structural).
* **One lifecycle** — no concrete agent (and neither intermediate class)
  overrides ``BaseAgent.__call__``, the final template method that is the
  only entry point the graph sees (design "2. Agent class hierarchy").
* **No LLM reference on the deterministic branch** — deterministic
  agents' constructors expose no orchestrator/client parameter, their
  constructed instances hold no attribute whose type comes from
  ``ml/llm/`` or ``services/llm/``, and their modules contain no import
  statement reaching those packages (Requirement 1.6).
* **Synthesizer re-raises instead of degrading** — the Synthesizer is the
  only agent overriding ``_build_degraded_safely``, and a ``run`` failure
  propagates the *original* exception out of ``__call__`` instead of
  producing a Degraded_Output, while a deterministic sibling with the
  same missing input degrades in place (Requirement 7.6).
* **One responsibility, one output field** — the five agents declare
  distinct names and distinct ``AgentState`` output fields matching the
  design's node table (Requirement 1.4).

Everything runs on fakes (no database, no provider, no settings). The
dynamic zero-LLM-call guarantee is Property 3 (task 5.7,
``tests/property/test_deterministic_no_llm_call.py``).

Validates: Requirements 1.4, 1.6, 7.6.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel

from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.deterministic_agent import DeterministicAgent
from matchlayer_api.ml.agents.improvement_agent import ImprovementAgent
from matchlayer_api.ml.agents.llm_agent import LLMAgent
from matchlayer_api.ml.agents.resume_analysis_agent import ResumeAnalysisAgent
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    FailureDetail,
)
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent

if TYPE_CHECKING:
    from matchlayer_api.services.llm.orchestrator import (
        LLMFeatureSpec,
        LLMOutcome,
        ProviderCallPlan,
    )

# ---------------------------------------------------------------------------
# The hierarchy under test (design "2. Agent class hierarchy").
# ---------------------------------------------------------------------------

_LLM_AGENTS: tuple[type[BaseAgent[Any]], ...] = (ResumeAnalysisAgent, ImprovementAgent)
_DETERMINISTIC_AGENTS: tuple[type[BaseAgent[Any]], ...] = (
    ATSAgent,
    SkillGapAgent,
    SynthesizerAgent,
)
_CONCRETE_AGENTS: tuple[type[BaseAgent[Any]], ...] = _LLM_AGENTS + _DETERMINISTIC_AGENTS

# Requirement 1.4: each agent's single responsibility, pinned as the
# name → output-field mapping from the design's node table.
_EXPECTED_IDENTITY: dict[type[BaseAgent[Any]], tuple[str, str]] = {
    ResumeAnalysisAgent: ("resume_analysis", "candidate_profile"),
    ATSAgent: ("ats", "ats_output"),
    SkillGapAgent: ("skill_gap", "skill_gap_report"),
    ImprovementAgent: ("improvement", "improvement_report"),
    SynthesizerAgent: ("synthesizer", "analysis_result"),
}

_FORBIDDEN_PREFIXES = ("matchlayer_api.ml.llm", "matchlayer_api.services.llm")

# ---------------------------------------------------------------------------
# Fakes: deps, scorer, and a never-called orchestrator for instantiation.
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


class _FakeScorer:
    """Minimal ScorerAdapter fake — pure values, no LLM anywhere."""

    active_scorer_version = "2.0.0+test"
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    async def score(self) -> ScoredMatch:
        return ScoredMatch(
            score=42.0, breakdown={"similarity": 0.42}, scorer_version="2.0.0+test", semantic=True
        )


class _NeverOrchestrator:
    """AgentLLMOrchestrator fake for instantiating the LLM agents only."""

    @property
    def model(self) -> str:
        return "test-model"

    async def prepare[TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[str, TResult],
        *,
        user_id: str,
        feature_input: str,
    ) -> LLMOutcome[TResult] | ProviderCallPlan[str, TResult]:
        raise AssertionError("the hierarchy contract tests never invoke the orchestrator")

    async def execute[TResult: BaseModel](
        self, plan: ProviderCallPlan[str, TResult]
    ) -> LLMOutcome[TResult]:
        raise AssertionError("the hierarchy contract tests never invoke the orchestrator")


def _instantiate(cls: type[BaseAgent[Any]]) -> BaseAgent[Any]:
    """Construct any concrete agent with fakes."""
    if cls is ATSAgent:
        return ATSAgent(_deps(), _FakeScorer())
    if cls in _LLM_AGENTS:
        return cls(_deps(), _NeverOrchestrator())  # type: ignore[call-arg]
    return cls(_deps())


def _module_path(cls: type[BaseAgent[Any]]) -> Path:
    module = inspect.getmodule(cls)
    assert module is not None and module.__file__ is not None
    return Path(module.__file__)


def _snapshotless_state() -> AgentState:
    """A valid AgentState missing every upstream output and the snapshot."""
    return AgentState(job_id="job-1", match_id="match-1", user_id="user-1")


# ---------------------------------------------------------------------------
# Exactly one branch (Requirements 1.4, 1.6).
# ---------------------------------------------------------------------------


class TestBranchMembership:
    @pytest.mark.parametrize("cls", _CONCRETE_AGENTS, ids=lambda cls: cls.__name__)
    def test_subclasses_exactly_one_intermediate_class(self, cls: type[BaseAgent[Any]]) -> None:
        """Every concrete agent extends LLMAgent xor DeterministicAgent."""
        is_llm = issubclass(cls, LLMAgent)
        is_deterministic = issubclass(cls, DeterministicAgent)
        assert is_llm != is_deterministic, (
            f"{cls.__name__} must subclass exactly one of LLMAgent/DeterministicAgent; "
            f"got LLMAgent={is_llm}, DeterministicAgent={is_deterministic}"
        )
        assert issubclass(cls, BaseAgent)

    def test_agents_land_on_their_designed_branch(self) -> None:
        """The LLM/no-LLM split matches the design's node table (Req 1.6)."""
        assert all(issubclass(cls, LLMAgent) for cls in _LLM_AGENTS)
        assert all(issubclass(cls, DeterministicAgent) for cls in _DETERMINISTIC_AGENTS)

    def test_intermediate_classes_stay_abstract(self) -> None:
        assert inspect.isabstract(LLMAgent)
        assert inspect.isabstract(DeterministicAgent)


# ---------------------------------------------------------------------------
# One lifecycle: no __call__ overrides (design D2).
# ---------------------------------------------------------------------------


class TestSingleLifecycle:
    @pytest.mark.parametrize(
        "cls",
        [*_CONCRETE_AGENTS, LLMAgent, DeterministicAgent],
        ids=lambda cls: cls.__name__,
    )
    def test_call_is_the_base_template_method(self, cls: type[BaseAgent[Any]]) -> None:
        """``__call__`` resolves to BaseAgent's final template method."""
        assert cls.__call__ is BaseAgent.__call__, (
            f"{cls.__name__} must not override BaseAgent.__call__ — the lifecycle "
            "(timeout, degradation, persistence, spans) is owned by the base class"
        )
        assert "__call__" not in vars(cls)


# ---------------------------------------------------------------------------
# The deterministic branch holds no LLM reference (Requirement 1.6).
# ---------------------------------------------------------------------------


class TestDeterministicAgentsHoldNoLLMReference:
    @pytest.mark.parametrize("cls", _DETERMINISTIC_AGENTS, ids=lambda cls: cls.__name__)
    def test_constructor_has_no_llm_shaped_parameter(self, cls: type[BaseAgent[Any]]) -> None:
        """No orchestrator/client/llm parameter through which an LLM could arrive."""
        params = inspect.signature(cls.__init__).parameters
        offenders = [
            name
            for name in params
            if any(token in name.lower() for token in ("orchestrator", "llm", "client"))
        ]
        assert offenders == [], (
            f"{cls.__name__}.__init__ exposes LLM-shaped parameters {offenders} "
            "(Requirement 1.6: deterministic agents accept no orchestrator or client)"
        )

    @pytest.mark.parametrize("cls", _DETERMINISTIC_AGENTS, ids=lambda cls: cls.__name__)
    def test_instance_holds_no_llm_typed_attribute(self, cls: type[BaseAgent[Any]]) -> None:
        """No constructed instance attribute has a type from ml/llm or services/llm."""
        instance = _instantiate(cls)
        assert "_orchestrator" not in vars(instance)
        offenders = {
            name: type(value).__module__
            for name, value in vars(instance).items()
            if any(
                type(value).__module__ == p or type(value).__module__.startswith(f"{p}.")
                for p in _FORBIDDEN_PREFIXES
            )
        }
        assert offenders == {}, (
            f"{cls.__name__} instance holds LLM-typed attributes (Requirement 1.6): {offenders}"
        )

    @pytest.mark.parametrize(
        "cls",
        [*_DETERMINISTIC_AGENTS, DeterministicAgent],
        ids=lambda cls: cls.__name__,
    )
    def test_module_has_no_llm_import(self, cls: type[BaseAgent[Any]]) -> None:
        """No import statement in the module source reaches an LLM package.

        AST walk (not text grep) so docstring prose naming the banned
        packages cannot false-positive — the structural half of
        Requirement 1.6, swept across the whole deterministic branch.
        """
        path = _module_path(cls)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    alias.name
                    for alias in node.names
                    if any(
                        alias.name == p or alias.name.startswith(f"{p}.")
                        for p in _FORBIDDEN_PREFIXES
                    )
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and any(
                    node.module == p or node.module.startswith(f"{p}.") for p in _FORBIDDEN_PREFIXES
                )
            ):
                offenders.append(node.module)
        assert offenders == [], (
            f"{path.name} must import nothing from ml/llm/ or services/llm/ "
            f"(Requirement 1.6); found: {offenders}"
        )


# ---------------------------------------------------------------------------
# The Synthesizer re-raises instead of degrading (Requirement 7.6).
# ---------------------------------------------------------------------------


class TestSynthesizerReRaises:
    def test_synthesizer_is_the_only_degradation_override(self) -> None:
        """Exactly one agent overrides _build_degraded_safely: the Synthesizer."""
        overriders = [
            cls.__name__
            for cls in _CONCRETE_AGENTS
            if cls._build_degraded_safely is not BaseAgent._build_degraded_safely
        ]
        assert overriders == ["SynthesizerAgent"], (
            "only the Synthesizer may disable the degradation lifecycle "
            f"(Requirement 7.6); overriders: {overriders}"
        )

    async def test_run_failure_propagates_the_original_exception(self) -> None:
        """A Synthesizer failure exits ``__call__`` as the original exception.

        The state lacks every upstream output, so ``run`` raises ValueError;
        the re-raising ``_build_degraded_safely`` must let exactly that
        exception propagate — no Degraded_Output, no partial state update
        (Requirement 7.6).
        """
        with pytest.raises(ValueError, match="missing upstream outputs") as exc_info:
            await SynthesizerAgent(_deps())(_snapshotless_state())
        assert type(exc_info.value) is ValueError

    async def test_deterministic_sibling_with_same_gap_degrades_instead(self) -> None:
        """Contrast: SkillGapAgent on the same input degrades in place.

        The same snapshotless state makes SkillGapAgent's ``run`` raise, but
        its unmodified lifecycle absorbs the failure into a schema-valid
        degraded update — proving the re-raise is a Synthesizer-specific
        exception to the shared policy, not shared behavior.
        """
        update = await SkillGapAgent(_deps())(_snapshotless_state())
        assert set(update) == {"skill_gap_report", "agent_status"}
        agent_status = update["agent_status"]
        assert isinstance(agent_status, dict)
        flag = agent_status["skill_gap"]
        assert flag.status is AgentCompletion.DEGRADED


# ---------------------------------------------------------------------------
# One responsibility, one output field (Requirement 1.4).
# ---------------------------------------------------------------------------


class TestSingleResponsibility:
    def test_names_and_output_fields_are_distinct(self) -> None:
        """Five distinct agent names, five distinct AgentState output fields."""
        names = [cls.name for cls in _CONCRETE_AGENTS]
        fields = [cls.output_field for cls in _CONCRETE_AGENTS]
        assert len(set(names)) == len(_CONCRETE_AGENTS)
        assert len(set(fields)) == len(_CONCRETE_AGENTS), (
            "two agents writing the same AgentState field would break the "
            "one-writer-per-field discipline (Requirements 1.2, 1.4)"
        )

    @pytest.mark.parametrize("cls", _CONCRETE_AGENTS, ids=lambda cls: cls.__name__)
    def test_identity_matches_the_design_node_table(self, cls: type[BaseAgent[Any]]) -> None:
        expected_name, expected_field = _EXPECTED_IDENTITY[cls]
        assert cls.name == expected_name
        assert cls.output_field == expected_field
        assert cls.output_field in AgentState.model_fields, (
            f"{cls.__name__}.output_field {cls.output_field!r} is not an AgentState field"
        )
