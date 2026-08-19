"""Feature: phase-4-agentic — Property 11.

# Feature: phase-4-agentic, Property 11: LLM call, quota, and invocation-log accounting agree

Property 11: LLM call, quota, and invocation-log accounting agree.

    *For any* run scenario (cache hit or miss, breaker open or closed,
    quota available or exhausted at call time, empty or non-empty
    redacted input, injected LLM failures), the number of provider calls
    made equals both the number of Daily_Quota units consumed and the
    number of new LLM_Invocation_Log rows written; each LLM agent makes
    at most one provider call; no-call paths (cache hit, breaker open,
    empty input, quota-reserve failure, degraded-without-call) consume
    zero units; and an analyze request with fewer than 2 units remaining
    is rejected 429 with a UTC reset time, creating no job row and
    enqueuing no message.

**Validates: Requirements 3.1, 3.6, 6.1, 9.3, 9.4, 9.5, 9.9**

What is driven, and how
-----------------------
The **real** Agent_Graph (``build_agents`` + ``compile_graph``) runs the
real ``ResumeAnalysisAgent`` and ``ImprovementAgent`` through the real
Phase 3 :class:`LLMOrchestrator` (via the production
:class:`MatchScopedOrchestrator` handle), so the three accounting
surfaces under comparison are the genuine articles:

* **provider calls** — a scripted, counting :class:`LLMClient` fake (the
  ``tests/property/test_repeat_request_suppression.py`` convention);
* **Daily_Quota units** — the real :class:`DailyQuota` over an in-memory
  Redis fake whose ``register_script`` emulation preserves the Lua
  script's atomic INCR-if-below-limit semantics (the
  ``test_quota_call_accounting.py`` convention); the final counter value
  IS the number of units consumed;
* **invocation-log rows** — the real ``record_invocation`` write path
  landing rows on an in-memory fake ``AsyncSession``.

Hypothesis generates the scenario dimensions the design's Property 11
enumerates: the quota limit (0..4 — 0/1 force gate exhaustion before or
between the two LLM calls), the Spend_Circuit_Breaker state, an injected
per-feature LLM failure mode (clean JSON, provider error, or
schema-invalid output — the latter two are calls that still count,
Requirement 9.3's "the reservation stays counted"), and empty vs
non-empty redacted resume text (Requirement 3.6: empty input degrades
with zero calls and zero units). A shadow model replays the pipeline's
normative gate order (quota gate → cache → breaker → reserve → call) to
predict the exact expected count, so the equality is asserted against an
independent derivation rather than against itself.

A second property drives the same successful scenario **twice** with a
shared cache/quota/log: the repeat run is served entirely from the
Agent_Cache (the orchestrator cache under the agent-specific feature
namespaces, Requirement 9.7) with zero additional calls, units, or rows.

A third property drives the real ``analyze_match`` handler directly with
scripted collaborators for the read-only quota precheck (Requirement
9.4): fewer than 2 remaining units → :class:`QuotaExceededError` (the
429 envelope) carrying a UTC reset time, with **no** job row staged and
**no** message enqueued; at least 2 (or an unreadable counter — the
Requirement 9.9 fail-safe pass-through) → the job is committed before
exactly one enqueue.

Async note: each example drives its coroutine inside an
:class:`asyncio.Runner` (the suite's established pattern).
"""

# Feature: phase-4-agentic, Property 11: LLM call, quota, and
# invocation-log accounting agree

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from opentelemetry.trace import NoOpTracer
from pydantic import BaseModel
from uuid_utils.compat import uuid7

from matchlayer_api.api.matches.router import analyze_match
from matchlayer_api.config import Settings
from matchlayer_api.core.errors import QuotaExceededError
from matchlayer_api.db.models import AgentJob, LLMInvocationLog, MatchResult, User
from matchlayer_api.ml.agents.ats_agent import ScoredMatch
from matchlayer_api.ml.agents.base import AgentDeps
from matchlayer_api.ml.agents.graph import build_agents, compile_graph
from matchlayer_api.ml.agents.llm_agent import MatchScopedOrchestrator
from matchlayer_api.ml.agents.state import (
    AgentCompletion,
    AgentState,
    AgentStatusFlag,
    FailureDetail,
    MatchSnapshot,
)
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.orchestrator import LLMOrchestrator
from matchlayer_api.services.llm.quota import DailyQuota, QuotaDecision
from matchlayer_api.services.llm.spend import BreakerState

# ---------------------------------------------------------------------------
# Settings (the unit-harness convention from test_repeat_request_suppression).
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


_SETTINGS = _build_settings()


# ---------------------------------------------------------------------------
# Fakes: session, Redis (quota + cache), breaker, counting LLM client,
# scorer, agent deps.
# ---------------------------------------------------------------------------


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
    """In-memory fake ``AsyncSession`` recording persisted rows.

    Covers exactly the surface the orchestrator's write path touches
    for the agent features (``reuse_persisted=False``, so no select is
    ever issued): ``add``, ``flush``, ``begin_nested``.
    """

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


class _FakeReserveScript:
    """Python emulation of the atomic INCR-if-below-limit Lua script.

    Executes synchronously end-to-end (no awaits between the read and
    the write), mirroring the atomicity Redis guarantees a Lua script —
    the final counter value therefore equals exactly the number of
    granted reservations (the Daily_Quota units consumed).
    """

    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    async def __call__(self, *, keys: list[str], args: list[int]) -> list[int]:
        key = keys[0]
        limit = int(args[0])
        count = int(self._store.get(key, b"0"))
        if count >= limit:
            return [0, 0]
        new_count = count + 1
        self._store[key] = str(new_count).encode()
        return [1, max(limit - new_count, 0)]


class _FakeQuotaRedis:
    """Minimal ``redis.asyncio.Redis`` stand-in for :class:`DailyQuota`."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def register_script(self, _source: str) -> _FakeReserveScript:
        return _FakeReserveScript(self.store)

    def units_consumed(self) -> int:
        """Total granted reservations across all keys (one user, one day)."""
        return sum(int(value) for value in self.store.values())


class _FakeCacheRedis:
    """In-memory redis fake covering the get/set surface LLMCache uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class _FakeBreaker:
    """SpendCircuitBreaker fake, open or closed on command."""

    def __init__(self, *, is_open: bool) -> None:
        self._is_open = is_open

    async def evaluate(self, session: Any) -> BreakerState:
        return BreakerState(
            is_open=self._is_open,
            tracked_spend=Decimal("0"),
            limit=Decimal("10"),
            cause=None,
        )

    def record_persist_failure(self) -> BreakerState:
        return BreakerState(is_open=True, tracked_spend=None, limit=Decimal("10"), cause=None)


# Schema-valid completion payloads per agent feature (keyed by the JSON
# schema title the orchestrator sends with each request, so the shared
# client can answer each feature with its own script).
_OK_PAYLOADS: dict[str, str] = {
    "CandidateProfile": json.dumps(
        {
            "sections": ["experience"],
            "skills": ["python"],
            "experiences": [],
            "gaps": [],
        }
    ),
    "ImprovementReport": json.dumps(
        {
            "actions": [{"rank": 1, "text": "Add a Kubernetes project."}],
            "rewrites": [],
        }
    ),
}


class _CountingLLMClient:
    """Scripted ``LLMClient`` counting provider calls, failing on command.

    ``modes`` maps the request schema's ``title`` to one of ``ok`` (valid
    JSON for that schema), ``provider_error`` (an :class:`LLMError`
    raised mid-stream — a call that was initiated and must stay
    counted), or ``bad_json`` (a completed call whose output fails
    terminal schema validation — likewise counted).
    """

    def __init__(self, modes: dict[str, str]) -> None:
        self._modes = modes
        self.calls = 0
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self.calls += 1
        title = str(request.output_schema.get("title", ""))
        mode = self._modes.get(title, "ok")
        text = "{not valid json" if mode == "bad_json" else _OK_PAYLOADS[title]
        try:
            if mode == "provider_error":
                raise LLMError("provider_error", "injected provider failure")
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


class _OkScorer:
    """ScorerAdapter fake — the ATS agent is not under test here."""

    active_scorer_version = "2.0.0+active"
    semantic_active = True
    resume_len = 1_000
    jd_len = 500

    async def score(self) -> ScoredMatch:
        return ScoredMatch(
            score=55.0,
            breakdown={"similarity": 0.55},
            scorer_version="2.0.0+active",
            semantic=True,
        )


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0


async def _noop_persist_run(
    agent_name: str,
    state: AgentState,
    output: BaseModel,
    status: AgentCompletion,
    reason: FailureDetail | None,
    latency_ms: int,
) -> None:
    del agent_name, state, output, status, reason, latency_ms


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Harness: one real graph run over the real Phase 3 orchestrator.
# ---------------------------------------------------------------------------


@dataclass
class _Collaborators:
    """The shared accounting surfaces one (or two) graph runs write to."""

    session: _FakeSession = field(default_factory=_FakeSession)
    quota_redis: _FakeQuotaRedis = field(default_factory=_FakeQuotaRedis)
    cache_redis: _FakeCacheRedis = field(default_factory=_FakeCacheRedis)


def _make_match(user_id: UUID) -> MatchResult:
    """A persisted-Match_Result stand-in with the columns fallbacks read."""
    return MatchResult(
        id=uuid7(),
        user_id=user_id,
        matched_keywords=["python"],
        missing_keywords=["kubernetes"],
        suggestions=["Add Kubernetes experience."],
    )


def _make_state(*, user_id: UUID, empty_input: bool) -> AgentState:
    return AgentState(
        job_id=str(uuid7()),
        match_id=str(uuid7()),
        user_id=str(user_id),
        redacted_resume_text="" if empty_input else "[NAME_1] built services in Python.",
        job_description_skills=["python", "kubernetes"],
        match_snapshot=MatchSnapshot(
            score=71.5,
            breakdown={"similarity": 0.6},
            scorer_version="2.0.0+active",
            matched_skills=["python"],
            missing_skills=["kubernetes"],
            suggestions=["Add a Kubernetes project."],
        ),
    )


async def _run_graph(
    *,
    collab: _Collaborators,
    state: AgentState,
    match: MatchResult,
    quota_limit: int,
    breaker_open: bool,
    client: _CountingLLMClient,
) -> dict[str, Any]:
    """One full Agent_Graph execution over the REAL orchestrator pipeline."""
    orchestrator = LLMOrchestrator(
        session=collab.session,  # type: ignore[arg-type]
        quota=DailyQuota(collab.quota_redis, limit=quota_limit),  # type: ignore[arg-type]
        cache=LLMCache(collab.cache_redis, ttl_seconds=60),  # type: ignore[arg-type]
        breaker=_FakeBreaker(is_open=breaker_open),  # type: ignore[arg-type]
        client_factory=lambda: client,
        settings=_SETTINGS,
        key_present=lambda: True,
    )
    handle = MatchScopedOrchestrator(
        orchestrator=orchestrator, match=match, model=_SETTINGS.llm_model
    )
    deps = AgentDeps(
        node_timeout_s=30.0,
        persist_agent_run=_noop_persist_run,
        tracer=NoOpTracer(),
        clock=_FakeClock(),
    )
    agents = build_agents(deps, handle, _OkScorer())
    compiled = compile_graph(agents)
    result: dict[str, Any] = await compiled.ainvoke(state)
    return result


# ---------------------------------------------------------------------------
# Shadow model: replay the pipeline's normative gate order to predict the
# exact per-agent accounting outcome independently of the implementation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Expected:
    calls: int
    trigger_by_agent: dict[str, str | None]  # None == completed normally


def _shadow_model(
    *,
    quota_limit: int,
    breaker_open: bool,
    empty_input: bool,
    modes: dict[str, str],
) -> _Expected:
    """Predict calls and per-agent outcomes for one fresh-cache run.

    The two LLM nodes execute in graph-dependency order (resume_analysis
    in the first superstep, improvement after it), so quota interplay is
    sequential. The cache is always empty on a fresh run, so the cache
    stage never suppresses a call here (the repeat property covers hits).
    """
    quota_count = 0
    calls = 0
    triggers: dict[str, str | None] = {}
    for agent, title in (
        ("resume_analysis", "CandidateProfile"),
        ("improvement", "ImprovementReport"),
    ):
        if agent == "resume_analysis" and empty_input:
            triggers[agent] = "empty_input"
            continue
        if quota_count >= quota_limit:
            triggers[agent] = "quota_exhausted"
            continue
        if breaker_open:
            triggers[agent] = "breaker_open"
            continue
        quota_count += 1
        calls += 1
        # A failed call still counts (Requirement 9.3) but degrades the
        # node with the generic "error" trigger (LLMFallbackError).
        triggers[agent] = None if modes[title] == "ok" else "error"
    return _Expected(calls=calls, trigger_by_agent=triggers)


# ---------------------------------------------------------------------------
# Strategies.
# ---------------------------------------------------------------------------

_mode = st.sampled_from(["ok", "provider_error", "bad_json"])

_scenarios = st.fixed_dictionaries(
    {
        "quota_limit": st.integers(min_value=0, max_value=4),
        "breaker_open": st.booleans(),
        "empty_input": st.booleans(),
        "resume_mode": _mode,
        "improvement_mode": _mode,
    }
)


# ---------------------------------------------------------------------------
# Property, part 1: calls == units == log rows, per generated scenario.
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 11: LLM call, quota, and
# invocation-log accounting agree
@settings(max_examples=100, deadline=None)
@example(  # empty input: zero everything from resume_analysis (Req 3.6)
    scenario={
        "quota_limit": 4,
        "breaker_open": False,
        "empty_input": True,
        "resume_mode": "ok",
        "improvement_mode": "ok",
    }
)
@example(  # quota exhausts between the two calls (limit 1)
    scenario={
        "quota_limit": 1,
        "breaker_open": False,
        "empty_input": False,
        "resume_mode": "ok",
        "improvement_mode": "ok",
    }
)
@example(  # breaker open: both LLM nodes degrade with zero calls (Req 9.5)
    scenario={
        "quota_limit": 4,
        "breaker_open": True,
        "empty_input": False,
        "resume_mode": "ok",
        "improvement_mode": "ok",
    }
)
@example(  # failed calls still count exactly once each (Req 9.3)
    scenario={
        "quota_limit": 4,
        "breaker_open": False,
        "empty_input": False,
        "resume_mode": "provider_error",
        "improvement_mode": "bad_json",
    }
)
@given(scenario=_scenarios)
def test_provider_calls_quota_units_and_log_rows_agree(scenario: dict[str, Any]) -> None:
    """calls == Daily_Quota units == invocation-log rows, and ≤ 2 per run.

    Property 11 (Requirements 3.1, 3.6, 6.1, 9.3, 9.5, 9.9): across any
    combination of breaker state, quota limit, empty input, and injected
    LLM failure, the three accounting surfaces agree exactly — and agree
    with an independent shadow-model derivation — with at most one call
    per LLM agent and zero consumption on every no-call path.
    """
    quota_limit = int(scenario["quota_limit"])
    breaker_open = bool(scenario["breaker_open"])
    empty_input = bool(scenario["empty_input"])
    modes = {
        "CandidateProfile": str(scenario["resume_mode"]),
        "ImprovementReport": str(scenario["improvement_mode"]),
    }
    expected = _shadow_model(
        quota_limit=quota_limit,
        breaker_open=breaker_open,
        empty_input=empty_input,
        modes=modes,
    )

    async def _run() -> None:
        collab = _Collaborators()
        client = _CountingLLMClient(modes)
        user_id = uuid4()
        state = _make_state(user_id=user_id, empty_input=empty_input)
        match = _make_match(user_id)

        result = await _run_graph(
            collab=collab,
            state=state,
            match=match,
            quota_limit=quota_limit,
            breaker_open=breaker_open,
            client=client,
        )

        log_rows = collab.session.rows(LLMInvocationLog)

        # --- the three accounting surfaces agree, and match the model ---
        assert client.calls == expected.calls, "provider calls diverge from the shadow model"
        assert collab.quota_redis.units_consumed() == client.calls, (
            "Daily_Quota units consumed must equal provider calls initiated"
        )
        assert len(log_rows) == client.calls, (
            "one LLM_Invocation_Log row per provider call, none for no-call paths"
        )
        assert client.calls <= 2, "at most one provider call per LLM agent (≤ 2 per run)"

        # --- per-agent outcomes match the model (no-call paths degrade
        # with the corresponding trigger; failed calls degrade too but
        # remain counted, Requirement 9.3) ---
        flags: dict[str, AgentStatusFlag] = result["agent_status"]
        for agent, expected_trigger in expected.trigger_by_agent.items():
            flag = flags[agent]
            if expected_trigger is None:
                assert flag.status is AgentCompletion.COMPLETED
                assert flag.failure_reason is None
            else:
                assert flag.status is AgentCompletion.DEGRADED
                assert flag.failure_reason is not None
                assert flag.failure_reason.trigger == expected_trigger

        # --- each counted call landed in its agent-specific namespace
        # (Requirements 3.1, 6.1: the agents' own feature values) ---
        expected_features: list[str] = []
        if expected.trigger_by_agent["resume_analysis"] in (None, "error"):
            expected_features.append(LLMFeature.AGENT_RESUME_ANALYSIS.value)
        if expected.trigger_by_agent["improvement"] in (None, "error"):
            expected_features.append(LLMFeature.AGENT_IMPROVEMENT.value)
        assert sorted(row.feature for row in log_rows) == sorted(expected_features)

    _run_sync(_run)


# ---------------------------------------------------------------------------
# Property, part 2: a repeat run is served from the Agent_Cache — zero
# additional calls, units, and rows (Requirement 9.5's cache-hit no-call
# path over the agent-specific namespaces of Requirement 9.7).
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 11: LLM call, quota, and
# invocation-log accounting agree
@settings(max_examples=25, deadline=None)
@given(quota_limit=st.integers(min_value=4, max_value=8))
def test_repeat_run_is_cache_served_with_zero_additional_accounting(quota_limit: int) -> None:
    """The second identical run consumes zero of everything a call would.

    Both LLM agents hit the orchestrator cache (their agent-specific
    namespaces) on the repeat, so provider calls, Daily_Quota units, and
    invocation-log rows all stay exactly at the first run's totals.
    """
    modes = {"CandidateProfile": "ok", "ImprovementReport": "ok"}

    async def _run() -> None:
        collab = _Collaborators()
        client = _CountingLLMClient(modes)
        user_id = uuid4()
        match = _make_match(user_id)
        state = _make_state(user_id=user_id, empty_input=False)

        first = await _run_graph(
            collab=collab,
            state=state,
            match=match,
            quota_limit=quota_limit,
            breaker_open=False,
            client=client,
        )
        assert client.calls == 2
        assert collab.quota_redis.units_consumed() == 2
        assert len(collab.session.rows(LLMInvocationLog)) == 2

        second = await _run_graph(
            collab=collab,
            state=state.model_copy(update={"job_id": str(uuid7())}),
            match=match,
            quota_limit=quota_limit,
            breaker_open=False,
            client=client,
        )

        # Zero additional accounting on the cache-served repeat.
        assert client.calls == 2
        assert collab.quota_redis.units_consumed() == 2
        assert len(collab.session.rows(LLMInvocationLog)) == 2

        # And the repeat completed normally with the same validated outputs.
        flags: dict[str, AgentStatusFlag] = second["agent_status"]
        assert flags["resume_analysis"].status is AgentCompletion.COMPLETED
        assert flags["improvement"].status is AgentCompletion.COMPLETED
        assert second["candidate_profile"] == first["candidate_profile"]

    _run_sync(_run)


# ---------------------------------------------------------------------------
# Property, part 3: the analyze quota precheck (Requirements 9.4, 9.9).
# ---------------------------------------------------------------------------


class _AnalyzeFakeResult:
    def __init__(self, scalar: Any) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _AnalyzeFakeSession:
    """Session fake for the analyze handler's read + create + commit path.

    ``execute`` answers the single owned-Match_Result lookup; job
    creation flows through ``begin_nested``/``add``/``flush``; ``commit``
    is recorded onto the shared event log so persist-before-enqueue
    ordering is observable.
    """

    def __init__(self, match: MatchResult, events: list[str]) -> None:
        self._match = match
        self._events = events
        self.added: list[Any] = []

    async def execute(self, _stmt: Any) -> _AnalyzeFakeResult:
        return _AnalyzeFakeResult(self._match)

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self._events.append("commit")


class _ScriptedGateQuota:
    """DailyQuota fake for the read-only precheck: remaining or unreadable."""

    def __init__(self, remaining: int | None) -> None:
        self._remaining = remaining

    async def gate(self, user_id: str) -> QuotaDecision:
        if self._remaining is None:
            from matchlayer_api.services.llm.quota import QuotaAccountingError

            raise QuotaAccountingError("quota counter unavailable")
        return QuotaDecision(allowed=self._remaining > 0, remaining=self._remaining)


class _RecordingQueue:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.enqueued: list[Any] = []

    async def enqueue(self, message: Any) -> None:
        self._events.append("enqueue")
        self.enqueued.append(message)


# Feature: phase-4-agentic, Property 11: LLM call, quota, and
# invocation-log accounting agree
@settings(max_examples=100, deadline=None)
@given(remaining=st.one_of(st.none(), st.integers(min_value=0, max_value=6)))
def test_analyze_precheck_rejects_below_two_units_creating_nothing(
    remaining: int | None,
) -> None:
    """< 2 remaining units → 429 with a UTC reset time, no job, no message.

    Property 11's endpoint clause (Requirements 9.4, 9.9): the read-only
    Daily_Quota precheck rejects an analyze request with fewer than 2
    remaining units via :class:`QuotaExceededError` (the 429
    ``quota_exceeded`` envelope) whose detail carries the UTC reset
    instant — staging no Agent_Job row and enqueuing no message. With
    ≥ 2 units — or an unreadable counter, the fail-safe pass-through of
    Requirement 9.9 — the job is committed before exactly one enqueue.
    """

    async def _run() -> None:
        user = User(id=uuid7(), email="p11@example.com", password_hash="x")
        match = _make_match(user.id)
        events: list[str] = []
        session = _AnalyzeFakeSession(match, events)
        quota = _ScriptedGateQuota(remaining)
        queue = _RecordingQueue(events)

        if remaining is not None and remaining < 2:
            with pytest.raises(QuotaExceededError) as exc_info:
                await analyze_match(
                    str(match.id),
                    user,
                    session,  # type: ignore[arg-type]
                    quota,  # type: ignore[arg-type]
                    queue,  # type: ignore[arg-type]
                )
            # The 429 envelope with a UTC reset instant (Requirement 9.4).
            assert exc_info.value.status_code == 429
            assert exc_info.value.error_type == "quota_exceeded"
            reset = _extract_reset_instant(exc_info.value.detail)
            assert reset.tzinfo is not None and reset.utcoffset() == UTC.utcoffset(None)
            assert (reset.hour, reset.minute, reset.second) == (0, 0, 0)
            assert reset > datetime.now(UTC)
            # No job row staged, nothing committed, no message enqueued.
            assert session.added == []
            assert events == []
            assert queue.enqueued == []
        else:
            response = await analyze_match(
                str(match.id),
                user,
                session,  # type: ignore[arg-type]
                quota,  # type: ignore[arg-type]
                queue,  # type: ignore[arg-type]
            )
            # Accepted: one queued job, committed BEFORE the one enqueue.
            jobs = [row for row in session.added if isinstance(row, AgentJob)]
            assert len(jobs) == 1
            assert response.status == "queued"
            assert response.id == str(jobs[0].id)
            assert events == ["commit", "enqueue"]
            assert len(queue.enqueued) == 1
            message = queue.enqueued[0]
            assert message.job_id == str(jobs[0].id)
            assert message.match_id == str(match.id)
            assert message.user_id == str(user.id)

    _run_sync(_run)


def _extract_reset_instant(detail: str) -> datetime:
    """Parse the ISO 8601 reset instant out of the 429 detail copy."""
    marker = "Quota resets at "
    assert marker in detail, f"429 detail must carry the UTC reset time; got {detail!r}"
    iso = detail.split(marker, 1)[1].rstrip(".").strip()
    return datetime.fromisoformat(iso)
