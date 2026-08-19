"""The Requirement 12.5 reproducibility demonstration (phase-4-agentic 10.9).

Requirement 12.5 mandates an automated test demonstrating, for at least
one completed Agent_Job, that the persisted data suffices to reproduce
the run from inputs alone:

1. **Deterministic re-execution** — for each Deterministic_Agent (ATS,
   Skill_Gap, Synthesizer), re-executing the agent against its persisted
   ``input_state_json`` produces output field-for-field identical to its
   persisted ``output_state_json``.
2. **LLM prompt reconstruction** — for each LLM_Agent (Resume_Analysis,
   Improvement), the transmitted prompt is exactly reconstructable from
   the persisted input state, the recorded prompt version, and the
   recorded PII_Redactor version, confirmed by the recomputed input hash
   equaling the input hash recorded in the corresponding
   LLM_Invocation_Log entry.

The completed job is simulated end to end: worker-shaped state
construction (real :func:`redact` over a PII-bearing resume), the real
five agents through the real ``build_agents`` / ``compile_graph``
composition layer, the real Phase 3 :class:`LLMOrchestrator` behind the
production :class:`MatchScopedOrchestrator` handle (fake quota, cache,
breaker, session, scripted ``LLMClient`` — the
``tests/unit/test_llm_orchestrator.py`` harness convention), and a
persistence recorder capturing rows byte-for-byte as
``services/agent_jobs/runs.py`` serializes them
(``model_dump(mode="json")``). No live database is required: the
"persisted" rows are the captured documents and the invocation-log rows
staged on the fake session — exactly the columns the real tables store.

The Hypothesis-driven generalizations of the two halves live in
``tests/property/test_deterministic_reexecution.py`` (Property 9) and
``tests/property/test_prompt_reconstruction.py`` (Property 19); this test
is the concrete one-completed-job demonstration Requirement 12.5's
closing clause asks for.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMInvocationLog, MatchResult
from matchlayer_api.ml.agents.ats_agent import ATSAgent, ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps, BaseAgent
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.improvement_agent import ImprovementAgent
from matchlayer_api.ml.agents.llm_agent import LLMAgent, MatchScopedOrchestrator
from matchlayer_api.ml.agents.resume_analysis_agent import ResumeAnalysisAgent
from matchlayer_api.ml.agents.skill_gap_agent import SkillGapAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.agents.synthesizer import SynthesizerAgent
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.orchestrator import (
    LLMOrchestrator,
    compute_input_hash,
)
from matchlayer_api.services.llm.quota import QuotaDecision
from matchlayer_api.services.llm.redaction import REDACTOR_VERSION, redact
from matchlayer_api.services.llm.spend import BreakerState

# ---------------------------------------------------------------------------
# Settings (the unit-harness convention).
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "environment": "development",
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    "jwt_secret": _TEST_SECRET,
}


# ---------------------------------------------------------------------------
# Fakes — the exact surfaces the real orchestrator and agents touch.
# ---------------------------------------------------------------------------


class _FakeQuota:
    async def gate(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=5)

    async def reserve(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=4)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class _FakeBreaker:
    async def evaluate(self, session: Any) -> BreakerState:
        return BreakerState(
            is_open=False,
            tracked_spend=Decimal("0"),
            limit=Decimal("10"),
            cause=None,
        )

    def record_persist_failure(self) -> BreakerState:
        return BreakerState(is_open=True, tracked_spend=None, limit=Decimal("10"), cause=None)


class _FakeSavepoint:
    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()

    def rows(self, model: type) -> list[Any]:
        return [row for row in self.added if isinstance(row, model)]


class _FakeLLMClient:
    """Scripted ``LLMClient`` replaying one valid JSON completion.

    ``{}`` validates against both agent output schemas (every field has a
    default), so one factory serves both features.
    """

    def __init__(self) -> None:
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        text = "{}"
        try:
            yield LLMStreamChunk(delta=text)
        finally:
            self._completion = LLMCompletion(
                text=text,
                usage=LLMUsage(
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=Decimal("0.000123"),
                    cost_basis="provider_reported",
                ),
                latency_ms=42,
            )

    async def result(self) -> LLMCompletion:
        assert self._completion is not None
        return self._completion


class _TickingClock:
    """Monotonic fake advancing on every read, so latencies are non-trivial."""

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        self._now += 0.007
        return self._now


_ACTIVE_SCORER_VERSION = "2.0.0+lex2+emb1+spacy1"


class _DeterministicScorer:
    """ScorerAdapter fake with fixed observables — deterministic on replay.

    The snapshot below carries the active Scorer_Version, so the ATS agent
    takes the persisted-score reuse path (Requirement 4.3) and its ``run``
    is a pure function of ``(state, adapter observables)`` — the same
    adapter is injected at replay, mirroring the production replay setup.
    """

    active_scorer_version = _ACTIVE_SCORER_VERSION
    semantic_active = True
    resume_len = 240
    jd_len = 500

    async def score(self) -> ScoredMatch:
        return ScoredMatch(
            score=71.5,
            breakdown={"similarity": 0.6, "keyword": 0.4},
            scorer_version=_ACTIVE_SCORER_VERSION,
            semantic=True,
        )


class _PersistedRun(BaseModel):
    """One captured agent_runs row, serialized exactly as runs.py stores it."""

    agent_name: str
    input_state_json: dict[str, Any]
    output_state_json: dict[str, Any]
    status: str
    latency_ms: int


class _RunRecorder:
    """Captures every persist_agent_run callback as its JSON documents."""

    def __init__(self) -> None:
        self.rows: dict[str, _PersistedRun] = {}

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        del reason
        assert agent_name not in self.rows, f"duplicate persist callback for {agent_name}"
        self.rows[agent_name] = _PersistedRun(
            agent_name=agent_name,
            input_state_json=state.model_dump(mode="json"),
            output_state_json=output.model_dump(mode="json"),
            status=status.value,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# The one completed Agent_Job.
# ---------------------------------------------------------------------------

_RAW_RESUME = (
    "Jane Doe\n"
    "Reach me at jane.doe@example.com or 555-123-4567.\n"
    "Built Python services on AWS and led Kubernetes migrations."
)


def _initial_state() -> AgentState:
    """Worker-shaped state: PII_Redactor over the raw text, snapshot loaded."""
    redacted = redact(_RAW_RESUME, kind="resume").text
    return AgentState(
        job_id=str(uuid4()),
        match_id=str(uuid4()),
        user_id=str(uuid4()),
        redacted_resume_text=redacted,
        job_description_skills=["python", "kubernetes", "terraform"],
        match_snapshot=MatchSnapshot(
            score=71.5,
            breakdown={"similarity": 0.6, "keyword": 0.4},
            scorer_version=_ACTIVE_SCORER_VERSION,
            matched_skills=["python", "aws"],
            missing_skills=["kubernetes", "terraform"],
            suggestions=["Add a Kubernetes project to your experience section."],
        ),
    )


_DETERMINISTIC_AGENTS = ("ats", "skill_gap", "synthesizer")
_LLM_AGENT_FEATURES = {
    "resume_analysis": LLMFeature.AGENT_RESUME_ANALYSIS,
    "improvement": LLMFeature.AGENT_IMPROVEMENT,
}


def _reconstruct_input_hash(
    agent: LLMAgent[Any],
    input_state_json: dict[str, Any],
    *,
    template_version: int,
    model: str,
) -> str:
    """Requirement 12.5's reconstruction: persisted state → recomputed hash.

    Deserializes the persisted document, re-assembles the transmitted
    prompt input via ``build_prompt_input``, rebuilds the redacted
    sections exactly as the orchestrator's pipeline does, and recomputes
    the digest through :func:`compute_input_hash` — the same hashing path
    the orchestrator used at call time.
    """
    restored = AgentState.model_validate(input_state_json)
    feature_input = agent.build_prompt_input(restored)
    spec = agent.feature_spec()
    inputs = spec.build_inputs(cast("MatchResult", object()), feature_input)
    sections: list[tuple[str, str]] = []
    for section in inputs.sections:
        if section.redaction is None:
            sections.append((section.kind, section.text))
        else:
            kind = cast('Literal["resume", "job_description", "bullet"]', section.redaction)
            sections.append((section.kind, redact(section.text, kind=kind).text))
    return compute_input_hash(
        feature=spec.feature,
        template_version=template_version,
        model=model,
        values=dict(inputs.values),
        sections=sections,
    )


async def test_completed_job_is_reproducible_from_persisted_data() -> None:
    """Requirement 12.5's automated demonstration, both checks, one job.

    Executes one Agent_Job end to end (all five agents complete normally,
    both LLM agents make genuine provider calls through the real Phase 3
    pipeline), then reproduces the run from the persisted documents alone:
    deterministic agents re-execute to field-for-field identical output,
    and each transmitted LLM prompt reconstructs to the recorded input
    hash.
    """
    settings_obj = Settings(**_BASE_SETTINGS_KWARGS)
    state = _initial_state()

    # ---- execute the job: real agents, real graph, real LLM pipeline ------
    session = _FakeSession()
    orchestrator = LLMOrchestrator(
        session=session,  # type: ignore[arg-type]
        quota=_FakeQuota(),  # type: ignore[arg-type]
        cache=LLMCache(_FakeRedis(), ttl_seconds=60),  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        client_factory=_FakeLLMClient,
        settings=settings_obj,
        key_present=lambda: True,
    )
    match = MatchResult(id=uuid7(), user_id=UUID(state.user_id))
    scoped = MatchScopedOrchestrator(
        orchestrator=orchestrator, match=match, model=settings_obj.llm_model
    )
    scorer = _DeterministicScorer()
    recorder = _RunRecorder()
    deps = AgentDeps(
        node_timeout_s=30.0,
        persist_agent_run=recorder,
        tracer=NoOpTracer(),
        clock=_TickingClock(),
    )
    compiled = compile_graph(build_agents(deps, scoped, scorer))
    result: dict[str, Any] = await compiled.ainvoke(state)

    # ---- the job completed: every node ran and completed normally ---------
    assert result["analysis_result"] is not None
    assert set(recorder.rows) == {*_DETERMINISTIC_AGENTS, *_LLM_AGENT_FEATURES}
    for row in recorder.rows.values():
        assert row.status == "completed", f"{row.agent_name} did not complete normally"

    # Both LLM agents made exactly one provider call each, recorded in the
    # (captured) LLM_Invocation_Log.
    logs = session.rows(LLMInvocationLog)
    log_by_feature = {row.feature: row for row in logs}
    assert sorted(log_by_feature) == sorted(
        feature.value for feature in _LLM_AGENT_FEATURES.values()
    )

    # ---- check 1: deterministic agents re-execute to identical output -----
    # Fresh instances (independent of the run-time ones) with the same
    # injected dependencies, driven against the persisted input state.
    replay_deps = AgentDeps(
        node_timeout_s=30.0,
        persist_agent_run=recorder,  # unused: `run` is invoked directly
        tracer=NoOpTracer(),
        clock=_TickingClock(),
    )
    replay_agents: dict[str, BaseAgent[Any]] = {
        "ats": ATSAgent(replay_deps, scorer),
        "skill_gap": SkillGapAgent(replay_deps),
        "synthesizer": SynthesizerAgent(replay_deps),
    }
    for name in _DETERMINISTIC_AGENTS:
        row = recorder.rows[name]
        restored = AgentState.model_validate(row.input_state_json)
        replayed = await replay_agents[name].run(restored)
        assert replayed.model_dump(mode="json") == row.output_state_json, (
            f"re-executing {name} against its persisted input state did not "
            f"reproduce its persisted output state field-for-field (Requirement 12.5)"
        )

    # ---- check 2: LLM prompts reconstruct to the recorded input hash ------
    replay_llm_agents: dict[str, LLMAgent[Any]] = {
        "resume_analysis": ResumeAnalysisAgent(replay_deps, scoped),
        "improvement": ImprovementAgent(replay_deps, scoped),
    }
    for name, feature in _LLM_AGENT_FEATURES.items():
        log_row = log_by_feature[feature.value]
        assert log_row.failure_category is None  # a successful provider call
        # The recorded PII_Redactor version is the reconstruction's third
        # input; it names the redactor whose behaviour applies here.
        assert log_row.redactor_version == REDACTOR_VERSION

        recomputed = _reconstruct_input_hash(
            replay_llm_agents[name],
            recorder.rows[name].input_state_json,
            template_version=log_row.prompt_template_version,
            model=log_row.llm_model,
        )
        assert recomputed == log_row.input_hash, (
            f"{name}: the prompt reconstructed from persisted input state, "
            f"recorded prompt version, and recorded PII_Redactor version "
            f"recomputes to {recomputed}, but the LLM_Invocation_Log recorded "
            f"{log_row.input_hash} (Requirement 12.5)"
        )
