"""Feature: phase-4-agentic — Property 19.

Property 19: LLM prompt reconstruction round-trip.

    *For any* completed job whose LLM agents made provider calls,
    reconstructing each transmitted prompt from the persisted
    ``input_state_json``, the recorded prompt version, and the recorded
    PII_Redactor version yields a recomputed input hash equal to the
    input hash recorded in the corresponding LLM_Invocation_Log entry.

**Validates: Requirements 12.5**

How the round trip is exercised
-------------------------------

The **record side** is the real Phase 3 pipeline: each LLM agent
(Resume_Analysis, Improvement) is driven through its full ``__call__``
lifecycle against a real :class:`LLMOrchestrator` (fake quota, cache,
breaker, session, and a scripted ``LLMClient`` — the
``tests/unit/test_llm_orchestrator.py`` harness convention) behind the
production :class:`MatchScopedOrchestrator` handle. ``prepare`` therefore
computes the input hash through the production path — build_inputs →
section redaction → template load → ``compute_input_hash`` — and
``execute`` writes a real ``LLMInvocationLog`` row (prompt version, model,
redactor version, input hash) onto the fake session.

The **reconstruction side** starts from exactly what Requirement 12.5
says suffices: the ``input_state_json`` document the ``persist_agent_run``
callback captured (``model_dump(mode="json")`` — byte-for-byte what
``services/agent_jobs/runs.py`` stores), the recorded
``prompt_template_version``, ``llm_model``, and ``redactor_version`` from
the invocation-log row. The state is deserialized, the agent's
``build_prompt_input`` re-assembles the transmitted user content, the
feature spec's ``build_inputs`` plus the orchestrator's section-redaction
rule rebuild the redacted sections, and
:func:`~matchlayer_api.services.llm.orchestrator.compute_input_hash` —
the exact function the orchestrator used — recomputes the digest. The
property asserts the recomputed hash equals the recorded one, and that
the recorded PII_Redactor version is the one whose behaviour the
reconstruction relied on (:data:`REDACTOR_VERSION`).

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern) so the event
loop closes deterministically under ``filterwarnings = ["error"]``.
"""

# Feature: phase-4-agentic, Property 19: LLM prompt reconstruction round-trip

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal, cast
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMInvocationLog, MatchResult
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.improvement_agent import ImprovementAgent
from matchlayer_api.ml.agents.llm_agent import LLMAgent, MatchScopedOrchestrator
from matchlayer_api.ml.agents.resume_analysis_agent import ResumeAnalysisAgent
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    CandidateProfile,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
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


def _build_settings() -> Settings:
    return Settings(**_BASE_SETTINGS_KWARGS)


# ---------------------------------------------------------------------------
# Fakes — the exact surfaces the real orchestrator touches.
# ---------------------------------------------------------------------------


class _FakeQuota:
    """Always-allowing DailyQuota: both gates pass."""

    async def gate(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=5)

    async def reserve(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=4)


class _FakeRedis:
    """In-memory redis fake (fresh per invocation, so every run is a miss)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class _FakeBreaker:
    """Closed SpendCircuitBreaker."""

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
    """Async context manager standing in for ``AsyncSessionTransaction``."""

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
    """In-memory fake ``AsyncSession`` recording added rows."""

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


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


class _InputStateRecorder:
    """Captures the persisted ``input_state_json`` per node invocation."""

    def __init__(self) -> None:
        self.input_state_json: dict[str, Any] | None = None
        self.status: AgentCompletion | None = None

    async def __call__(
        self,
        agent_name: str,
        state: AgentState,
        output: BaseModel,
        status: AgentCompletion,
        reason: FailureDetail | None,
        latency_ms: int,
    ) -> None:
        del agent_name, output, reason, latency_ms
        assert self.input_state_json is None, "exactly one invocation expected"
        self.input_state_json = state.model_dump(mode="json")
        self.status = status


# ---------------------------------------------------------------------------
# Generators: worker-shaped AgentStates with the LLM agents' inputs present.
# ---------------------------------------------------------------------------

_skill = st.one_of(
    st.sampled_from(["python", "aws", "sql", "react", "kubernetes"]),
    st.text(min_size=1, max_size=12),
)

_snapshots = st.builds(
    MatchSnapshot,
    score=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    breakdown=st.dictionaries(
        st.sampled_from(["similarity", "keyword"]),
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        max_size=2,
    ),
    scorer_version=st.sampled_from(["1.0.0", "2.0.0+lex2+emb1"]),
    matched_skills=st.lists(_skill, max_size=5),
    missing_skills=st.lists(_skill, max_size=5),
    suggestions=st.lists(st.text(max_size=30), max_size=3),
)

_profiles = st.builds(
    CandidateProfile,
    sections=st.lists(st.text(max_size=20), max_size=3),
    skills=st.lists(_skill, max_size=6),
    gaps=st.lists(st.text(max_size=20), max_size=3),
    degraded=st.booleans(),
)


@st.composite
def _states(draw: st.DrawFn) -> AgentState:
    """A worker-shaped state carrying both LLM agents' inputs."""
    return AgentState(
        job_id=draw(st.uuids(version=4).map(str)),
        match_id=draw(st.uuids(version=4).map(str)),
        user_id=draw(st.uuids(version=4).map(str)),
        redacted_resume_text=draw(
            st.text(min_size=1, max_size=200).filter(lambda s: bool(s.strip()))
        ),
        job_description_skills=draw(st.lists(_skill, max_size=6)),
        match_snapshot=draw(_snapshots),
        candidate_profile=draw(_profiles),
    )


# ---------------------------------------------------------------------------
# Record and reconstruct.
# ---------------------------------------------------------------------------


def _reconstruct_input_hash(
    agent: LLMAgent[Any],
    input_state_json: dict[str, Any],
    *,
    template_version: int,
    model: str,
) -> str:
    """Requirement 12.5's reconstruction: persisted state → recomputed hash.

    Deserializes the persisted document, re-assembles the transmitted
    prompt input via the agent's ``build_prompt_input``, rebuilds the
    redacted sections exactly as the orchestrator's pipeline does
    (``build_inputs`` then per-section redaction — sections declaring a
    redaction kind go through :func:`redact`, sections with ``None`` are
    already-redacted derived data read verbatim), and recomputes the
    digest through :func:`compute_input_hash` — the same hashing path the
    orchestrator used at call time.
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


async def _record_one_call(
    agent_cls: type[ResumeAnalysisAgent] | type[ImprovementAgent],
    state: AgentState,
    settings_obj: Settings,
) -> tuple[LLMAgent[Any], dict[str, Any], LLMInvocationLog]:
    """Drive one agent through the real pipeline; return the persisted pair.

    Returns the agent, the captured ``input_state_json``, and the single
    LLM_Invocation_Log row the real orchestrator recorded for the
    provider call.
    """
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
    recorder = _InputStateRecorder()
    deps = AgentDeps(
        node_timeout_s=30.0,
        persist_agent_run=recorder,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )
    agent = agent_cls(deps, scoped)
    await agent(state)

    assert recorder.status is AgentCompletion.COMPLETED, "the provider call must succeed"
    assert recorder.input_state_json is not None
    logs = session.rows(LLMInvocationLog)
    assert len(logs) == 1, "exactly one invocation-log row per provider call"
    return agent, recorder.input_state_json, logs[0]


async def _round_trip(state: AgentState) -> None:
    settings_obj = _build_settings()
    for agent_cls in (ResumeAnalysisAgent, ImprovementAgent):
        agent, input_state_json, row = await _record_one_call(agent_cls, state, settings_obj)

        # The recorded PII_Redactor version is the one whose behaviour the
        # reconstruction relies on (Requirement 12.5's third input).
        assert row.redactor_version == REDACTOR_VERSION
        assert row.failure_category is None

        recomputed = _reconstruct_input_hash(
            agent,
            input_state_json,
            template_version=row.prompt_template_version,
            model=row.llm_model,
        )
        assert recomputed == row.input_hash, (
            f"{agent.name}: reconstructed prompt input recomputes to {recomputed}, "
            f"but the invocation log recorded {row.input_hash} (Requirement 12.5)"
        )


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 19: LLM prompt reconstruction round-trip
@settings(max_examples=100, deadline=None)
@given(state=_states())
def test_llm_prompt_reconstruction_round_trip(state: AgentState) -> None:
    """For any state whose LLM agents make provider calls, the prompt
    reconstructed from the persisted input state + recorded prompt version
    + recorded PII_Redactor version recomputes to the exact input hash the
    real orchestrator recorded in the LLM_Invocation_Log (Requirement
    12.5)."""
    with asyncio.Runner() as runner:
        runner.run(_round_trip(state))
