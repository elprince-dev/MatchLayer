"""Unit tests for ``services/llm/orchestrator.py`` (phase-3-llm-layer task 8.1).

Drives the real :class:`LLMOrchestrator` pipeline end-to-end against
scripted fakes (quota, redis-backed cache, breaker, session, LLM client),
covering the normative stage order and the failure taxonomy:

* Happy path: persist -> cache write -> invocation log -> breaker
  re-evaluation, deltas relayed, quota reserved exactly once.
* Provider error / timeout / schema-validation failure: one invocation-log
  row with the matching failure category, fallback envelope, nothing
  persisted or cached.
* Gate rejections: quota gate 429, breaker-open 503 -- no provider call.
* Pre-call fallbacks: key absent, quota accounting unavailable -- no
  provider call, no invocation-log row.
* Cache hit: no second provider call, no second quota reservation.
* Redaction applied before transmission; canonical input-hash behavior.

The per-feature specs, routers, and SSE layer are covered by their own
tasks (8.2-8.4, 10.x); property tests 8.5-8.10 cover the universal
behaviors across generated inputs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMInvocationLog, LLMResult, MatchResult
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.orchestrator import (
    DailyQuotaExceededError,
    LLMFeatureSpec,
    LLMOrchestrator,
    LLMOutcome,
    PromptInputs,
    PromptSection,
    SpendLimitExceededError,
    compute_input_hash,
)
from matchlayer_api.services.llm.quota import QuotaAccountingError, QuotaDecision
from matchlayer_api.services.llm.schemas import FailureReason
from matchlayer_api.services.llm.spend import BreakerState

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


def _build_settings(**overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {**_BASE_SETTINGS_KWARGS, **overrides}
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class _EchoResult(BaseModel):
    """Minimal result schema standing in for a feature payload."""

    model_config = ConfigDict(extra="forbid")

    message: str


class _FakeQuota:
    """Scripted DailyQuota: configurable gate/reserve outcomes, call counts."""

    def __init__(
        self,
        *,
        gate_allowed: bool = True,
        reserve_allowed: bool = True,
        remaining: int = 5,
        gate_error: bool = False,
        reserve_error: bool = False,
    ) -> None:
        self.gate_allowed = gate_allowed
        self.reserve_allowed = reserve_allowed
        self.remaining = remaining
        self.gate_error = gate_error
        self.reserve_error = reserve_error
        self.gate_calls = 0
        self.reserve_calls = 0

    async def gate(self, user_id: str) -> QuotaDecision:
        self.gate_calls += 1
        if self.gate_error:
            raise QuotaAccountingError("quota counter unavailable")
        return QuotaDecision(allowed=self.gate_allowed, remaining=self.remaining)

    async def reserve(self, user_id: str) -> QuotaDecision:
        self.reserve_calls += 1
        if self.reserve_error:
            raise QuotaAccountingError("quota counter unavailable")
        remaining = self.remaining - 1 if self.reserve_allowed else 0
        return QuotaDecision(allowed=self.reserve_allowed, remaining=remaining)


class _FakeRedis:
    """In-memory redis fake covering the get/set surface LLMCache uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class _FakeBreaker:
    """Scripted SpendCircuitBreaker: fixed state, recorded interactions."""

    def __init__(self, *, is_open: bool = False) -> None:
        self.is_open = is_open
        self.evaluate_calls = 0
        self.persist_failures = 0

    async def evaluate(self, session: Any) -> BreakerState:
        self.evaluate_calls += 1
        return BreakerState(
            is_open=self.is_open,
            tracked_spend=Decimal("0"),
            limit=Decimal("10"),
            cause=None,
        )

    def record_persist_failure(self) -> BreakerState:
        self.persist_failures += 1
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
    """Scripted ``LLMClient``: replays chunks, optionally fails, records usage.

    Mirrors the adapter contract: the accumulated completion is recorded in
    a ``finally`` block so ``result()`` is available after success, failure,
    or abort.
    """

    def __init__(self, *, chunks: list[str] | None = None, error: LLMError | None = None) -> None:
        self.chunks = chunks if chunks is not None else []
        self.error = error
        self.stream_calls = 0
        self.last_request: LLMRequest | None = None
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self.stream_calls += 1
        self.last_request = request
        parts: list[str] = []
        try:
            for chunk in self.chunks:
                parts.append(chunk)
                yield LLMStreamChunk(delta=chunk)
            if self.error is not None:
                raise self.error
        finally:
            self._completion = LLMCompletion(
                text="".join(parts),
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


# ---------------------------------------------------------------------------
# Test wiring helpers.
# ---------------------------------------------------------------------------

_USER_ID = UUID("01890000-0000-7000-8000-000000000001")
_RAW_EMAIL = "jane.doe@example.com"


def _build_inputs(match: MatchResult, feature_input: None) -> PromptInputs:
    return PromptInputs(
        values={"min_improvements": "3", "max_improvements": "10"},
        sections=[
            PromptSection(kind="resume", text=f"Contact: {_RAW_EMAIL}", redaction="resume"),
            PromptSection(kind="missing_skills", text="docker, kubernetes", redaction=None),
        ],
    )


def _build_fallback(match: MatchResult, feature_input: None, reason: FailureReason) -> _EchoResult:
    return _EchoResult(message=f"fallback:{reason.value}")


_SPEC = LLMFeatureSpec(
    feature=LLMFeature.RESUME_COACH,
    result_schema=_EchoResult,
    build_inputs=_build_inputs,
    build_fallback=_build_fallback,
)


class _Harness:
    """One orchestrator with all fakes, ready to run the shared spec."""

    def __init__(
        self,
        *,
        client: _FakeLLMClient | None = None,
        quota: _FakeQuota | None = None,
        breaker: _FakeBreaker | None = None,
        key_present: bool = True,
    ) -> None:
        self.session = _FakeSession()
        self.quota = quota if quota is not None else _FakeQuota()
        self.redis = _FakeRedis()
        self.cache = LLMCache(self.redis, ttl_seconds=60)  # type: ignore[arg-type]
        self.breaker = breaker if breaker is not None else _FakeBreaker()
        self.client = (
            client if client is not None else _FakeLLMClient(chunks=['{"message": ', '"hello"}'])
        )
        self.match = MatchResult(id=uuid7(), user_id=_USER_ID)
        self.orchestrator = LLMOrchestrator(
            session=self.session,  # type: ignore[arg-type]
            quota=self.quota,  # type: ignore[arg-type]
            cache=self.cache,
            breaker=self.breaker,  # type: ignore[arg-type]
            client_factory=lambda: self.client,
            settings=_build_settings(),
            key_present=lambda: key_present,
        )

    async def run(self) -> LLMOutcome[_EchoResult]:
        return await self.orchestrator.run(
            _SPEC, user_id=_USER_ID, match=self.match, feature_input=None
        )


# ---------------------------------------------------------------------------
# Pipeline behavior.
# ---------------------------------------------------------------------------


async def test_happy_path_persists_caches_and_logs() -> None:
    """Valid output: persisted row, cache entry, one success log, deltas relayed."""
    harness = _Harness()
    deltas: list[str] = []

    async def on_delta(delta: str) -> None:
        deltas.append(delta)

    outcome = await harness.orchestrator.run(
        _SPEC, user_id=_USER_ID, match=harness.match, feature_input=None, on_delta=on_delta
    )

    envelope = outcome.envelope
    assert envelope.is_fallback is False
    assert envelope.fallback_reason is None
    assert envelope.result.message == "hello"
    assert envelope.id is not None and UUID(envelope.id)
    assert envelope.prompt_template_version == 1
    assert isinstance(envelope.created_at, datetime)
    assert envelope.created_at.tzinfo is UTC

    # Deltas are relayed verbatim, in order (display-progressive only).
    assert deltas == ['{"message": ', '"hello"}']

    # Exactly one persisted LLM_Result carrying the validated payload.
    results = harness.session.rows(LLMResult)
    assert len(results) == 1
    assert results[0].payload == {"message": "hello"}
    assert results[0].feature == "resume_coach"

    # Exactly one invocation-log row: success, usage recorded, hash shared.
    logs = harness.session.rows(LLMInvocationLog)
    assert len(logs) == 1
    assert logs[0].output == {"message": "hello"}
    assert logs[0].failure_category is None
    assert logs[0].cost_usd == Decimal("0.000123")
    assert logs[0].latency_ms == 42

    # Cache holds exactly one entry, keyed with the same input hash.
    assert len(harness.redis.store) == 1
    (cache_key,) = harness.redis.store
    assert logs[0].input_hash in cache_key
    assert str(_USER_ID) in cache_key

    # One provider call, one atomic reservation; breaker evaluated before
    # the call and again after the log persist.
    assert harness.client.stream_calls == 1
    assert harness.quota.reserve_calls == 1
    assert harness.breaker.evaluate_calls == 2
    assert harness.breaker.persist_failures == 0
    assert outcome.quota_remaining == 4


async def test_provider_error_falls_back_and_logs_failure() -> None:
    """Provider error: fallback envelope, one failure log, nothing persisted/cached."""
    harness = _Harness(client=_FakeLLMClient(error=LLMError(category="provider_error")))

    outcome = await harness.run()

    envelope = outcome.envelope
    assert envelope.is_fallback is True
    assert envelope.fallback_reason is FailureReason.PROVIDER_ERROR
    assert envelope.result.message == "fallback:provider_error"
    assert envelope.id is None and envelope.created_at is None

    assert harness.session.rows(LLMResult) == []
    assert harness.redis.store == {}
    logs = harness.session.rows(LLMInvocationLog)
    assert len(logs) == 1
    assert logs[0].failure_category == "provider_error"
    assert logs[0].output is None
    # The reservation stays counted even though the call failed (Req 13.3).
    assert harness.quota.reserve_calls == 1


async def test_timeout_maps_to_timeout_failure_reason() -> None:
    """The adapter's timeout category maps onto FailureReason.TIMEOUT."""
    harness = _Harness(client=_FakeLLMClient(error=LLMError(category="timeout")))

    outcome = await harness.run()

    assert outcome.envelope.fallback_reason is FailureReason.TIMEOUT
    logs = harness.session.rows(LLMInvocationLog)
    assert len(logs) == 1
    assert logs[0].failure_category == "timeout"


async def test_schema_validation_failure_falls_back() -> None:
    """Off-schema output: schema_validation_failed, never persisted or cached."""
    harness = _Harness(client=_FakeLLMClient(chunks=["not json at all"]))

    outcome = await harness.run()

    assert outcome.envelope.is_fallback is True
    assert outcome.envelope.fallback_reason is FailureReason.SCHEMA_VALIDATION_FAILED
    assert harness.session.rows(LLMResult) == []
    assert harness.redis.store == {}
    logs = harness.session.rows(LLMInvocationLog)
    assert len(logs) == 1
    assert logs[0].failure_category == "schema_validation_failed"


async def test_quota_gate_exhausted_raises_before_any_work() -> None:
    """Gate rejection: 429 exception, no provider call, no reservation."""
    harness = _Harness(quota=_FakeQuota(gate_allowed=False, remaining=0))

    with pytest.raises(DailyQuotaExceededError) as exc_info:
        await harness.run()

    assert exc_info.value.remaining == 0
    assert exc_info.value.resets_at.tzinfo is UTC
    assert harness.client.stream_calls == 0
    assert harness.quota.reserve_calls == 0
    assert harness.session.added == []


async def test_lost_reserve_race_raises_the_same_429() -> None:
    """A reserve that loses the concurrency race gets the same 429 (Req 13.7)."""
    harness = _Harness(quota=_FakeQuota(gate_allowed=True, reserve_allowed=False))

    with pytest.raises(DailyQuotaExceededError):
        await harness.run()

    assert harness.client.stream_calls == 0


async def test_breaker_open_raises_spend_limit_before_call() -> None:
    """Open breaker: 503 exception, no provider call, no reservation."""
    harness = _Harness(breaker=_FakeBreaker(is_open=True))

    with pytest.raises(SpendLimitExceededError):
        await harness.run()

    assert harness.client.stream_calls == 0
    assert harness.quota.reserve_calls == 0


async def test_key_absent_falls_back_llm_unavailable() -> None:
    """Key absent: llm_unavailable fallback, no call, no log row (no call made)."""
    harness = _Harness(key_present=False)

    outcome = await harness.run()

    assert outcome.envelope.is_fallback is True
    assert outcome.envelope.fallback_reason is FailureReason.LLM_UNAVAILABLE
    assert harness.client.stream_calls == 0
    assert harness.session.rows(LLMInvocationLog) == []
    assert harness.quota.gate_calls == 1  # gate still ran first (Req 13.2)
    assert harness.quota.reserve_calls == 0


async def test_quota_accounting_unavailable_falls_back_without_call() -> None:
    """Redis down at the gate: fallback, no provider call (Req 13.8)."""
    harness = _Harness(quota=_FakeQuota(gate_error=True))

    outcome = await harness.run()

    assert outcome.envelope.fallback_reason is FailureReason.QUOTA_ACCOUNTING_UNAVAILABLE
    assert outcome.quota_remaining is None
    assert harness.client.stream_calls == 0
    assert harness.session.rows(LLMInvocationLog) == []


async def test_cache_hit_skips_provider_call_and_quota_reserve() -> None:
    """A repeat identical request is served from cache: no call, no count."""
    harness = _Harness()
    first = await harness.run()

    # Fresh client for the second run; cache and quota fakes persist.
    harness.client = _FakeLLMClient(chunks=["never used"])
    second = await harness.run()

    assert harness.client.stream_calls == 0
    assert harness.quota.reserve_calls == 1  # only the first run reserved
    assert second.envelope.is_fallback is False
    assert second.envelope.id == first.envelope.id
    assert second.envelope.result == first.envelope.result
    # No second invocation-log row for a call that did not happen (Req 15.2).
    assert len(harness.session.rows(LLMInvocationLog)) == 1


async def test_redaction_applied_before_transmission() -> None:
    """The transmitted user message carries the placeholder, never the raw email."""
    harness = _Harness()

    await harness.run()

    request = harness.client.last_request
    assert request is not None
    user_message = request.messages[1]
    assert user_message.role == "user"
    assert _RAW_EMAIL not in user_message.content
    assert "[EMAIL_1]" in user_message.content
    # The unredacted section passes through verbatim.
    assert "docker, kubernetes" in user_message.content


# ---------------------------------------------------------------------------
# Canonical input hash.
# ---------------------------------------------------------------------------


def _hash(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "feature": LLMFeature.RESUME_COACH,
        "template_version": 1,
        "model": "test/model",
        "values": {"a": "1"},
        "sections": [("resume", "redacted text")],
    }
    kwargs.update(overrides)
    return compute_input_hash(**kwargs)


def test_input_hash_is_deterministic_and_component_sensitive() -> None:
    """Equal tuples hash equal; any component change produces a new digest."""
    base = _hash()
    assert base == _hash()
    assert len(base) == 64 and int(base, 16) >= 0  # sha256 hex

    assert base != _hash(feature=LLMFeature.BULLET_REWRITE)
    assert base != _hash(template_version=2)
    assert base != _hash(model="other/model")
    assert base != _hash(values={"a": "2"})
    assert base != _hash(sections=[("resume", "different text")])
    assert base != _hash(sections=[("job_description", "redacted text")])
