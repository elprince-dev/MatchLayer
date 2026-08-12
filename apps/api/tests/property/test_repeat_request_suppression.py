"""Feature: phase-3-llm-layer — Property 12.

# Feature: phase-3-llm-layer, Property 12: Repeat requests never re-call the provider

Property 12: Repeat requests never re-call the provider.

    *For any* validated LLM_Result that has been persisted (coach reuse
    under unchanged version+model) or cached (any feature, within TTL),
    a subsequent identical request from the same user returns the same
    payload with zero provider calls, zero new invocation-log rows for a
    call that did not occur, and zero Daily_Quota consumption.

**Validates: Requirements 5.4, 15.2**

Two tests cover the two suppression mechanisms, each driving the real
:class:`LLMOrchestrator` pipeline end-to-end against scripted fakes (the
``tests/unit/test_llm_orchestrator.py`` harness convention):

1. **LLM_Cache suppression (Req 15.2)** — a feature *without*
   persisted-result reuse runs once (one provider call, one quota
   reservation, one invocation-log row, one persisted row), then the
   byte-identical request runs again against the same live cache. The
   repeat must be served from the cache: the same envelope payload comes
   back, the scripted client's stream counter never moves, no new
   invocation-log row appears, and the quota ``reserve`` counter — the
   only consumption point — never moves.

2. **Persisted-result reuse (Req 5.4)** — the coach-shaped spec
   (``reuse_persisted=True``, design D7) runs once, then the identical
   request runs through a second orchestrator sharing the session, quota,
   and client but holding a **fresh, empty cache** — so only the
   persisted LLM_Result row can satisfy the repeat. Under the unchanged
   active template version and configured model the stored row is
   served: same payload, same row id, zero provider calls, zero new log
   rows, zero quota consumption.

Generators vary the resume body, the pass-through section, the LLM's
JSON payload content, and the user id, so suppression is asserted across
arbitrary validated results rather than one fixture.
"""

from __future__ import annotations

import asyncio
import json
import string
from collections.abc import AsyncIterator, Callable, Coroutine
from decimal import Decimal
from types import TracebackType
from typing import Any
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMInvocationLog, LLMResult, MatchResult
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOrchestrator,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.quota import QuotaDecision
from matchlayer_api.services.llm.schemas import FailureReason
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
# Fakes — the exact surfaces the orchestrator touches, mirroring
# tests/unit/test_llm_orchestrator.py.
# ---------------------------------------------------------------------------


class _EchoResult(BaseModel):
    """Minimal result schema standing in for a feature payload."""

    model_config = ConfigDict(extra="forbid")

    message: str


class _CountingQuota:
    """Always-allowing DailyQuota counting gate and reserve calls.

    ``reserve`` is the single Daily_Quota consumption point (Req 13.3),
    so its call count is the "zero quota consumption" witness.
    """

    def __init__(self) -> None:
        self.gate_calls = 0
        self.reserve_calls = 0

    async def gate(self, user_id: str) -> QuotaDecision:
        self.gate_calls += 1
        return QuotaDecision(allowed=True, remaining=5)

    async def reserve(self, user_id: str) -> QuotaDecision:
        self.reserve_calls += 1
        return QuotaDecision(allowed=True, remaining=4)


class _FakeRedis:
    """In-memory redis fake covering the get/set surface LLMCache uses."""

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


class _ScalarResult:
    """Minimal stand-in for an ``Result`` supporting ``scalars().first()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """In-memory fake ``AsyncSession`` recording rows and answering selects.

    ``execute`` emulates the ``find_reusable_result`` lookup generically:
    the compiled statement's bind parameters are matched against the
    corresponding :class:`LLMResult` attributes (``user_id_1`` →
    ``user_id`` and so on), and matching rows come back newest-first —
    the same equality filters and ordering the real query applies.
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

    async def execute(self, stmt: Any) -> _ScalarResult:
        params: dict[str, Any] = dict(stmt.compile().params)

        def matches(row: Any) -> bool:
            for name, value in params.items():
                column = name.rsplit("_", 1)[0]
                if hasattr(row, column) and getattr(row, column) != value:
                    return False
            return True

        matching = [row for row in self.rows(LLMResult) if matches(row)]
        matching.sort(key=lambda row: (row.created_at, str(row.id)), reverse=True)
        return _ScalarResult(matching)


class _FakeLLMClient:
    """Scripted ``LLMClient`` replaying one JSON completion, counting calls."""

    def __init__(self, *, text: str) -> None:
        self.text = text
        self.stream_calls = 0
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self.stream_calls += 1
        try:
            yield LLMStreamChunk(delta=self.text)
        finally:
            self._completion = LLMCompletion(
                text=self.text,
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


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Spec wiring (the resume_coach template's two placeholder slots).
# ---------------------------------------------------------------------------


def _build_spec(
    *, raw_resume: str, passthrough: str, reuse_persisted: bool
) -> LLMFeatureSpec[None, _EchoResult]:
    def _build_inputs(match: MatchResult, feature_input: None) -> PromptInputs:
        return PromptInputs(
            values={"min_improvements": "3", "max_improvements": "10"},
            sections=[
                PromptSection(kind="resume", text=raw_resume, redaction="resume"),
                PromptSection(kind="missing_skills", text=passthrough, redaction=None),
            ],
        )

    def _build_fallback(
        match: MatchResult, feature_input: None, reason: FailureReason
    ) -> _EchoResult:
        return _EchoResult(message=f"fallback:{reason.value}")

    return LLMFeatureSpec(
        feature=LLMFeature.RESUME_COACH,
        result_schema=_EchoResult,
        build_inputs=_build_inputs,
        build_fallback=_build_fallback,
        reuse_persisted=reuse_persisted,
    )


def _orchestrator(
    *,
    session: _FakeSession,
    quota: _CountingQuota,
    redis: _FakeRedis,
    client: _FakeLLMClient,
) -> LLMOrchestrator:
    return LLMOrchestrator(
        session=session,  # type: ignore[arg-type]
        quota=quota,  # type: ignore[arg-type]
        cache=LLMCache(redis, ttl_seconds=60),  # type: ignore[arg-type]
        breaker=_FakeBreaker(),  # type: ignore[arg-type]
        client_factory=lambda: client,
        settings=_build_settings(),
        key_present=lambda: True,
    )


# ---------------------------------------------------------------------------
# Strategies.
# ---------------------------------------------------------------------------

# Resume body: lowercase single-line prose, so redaction is deterministic
# and repeat inputs hash identically (redaction correctness is Properties
# 1-3's concern; here the *suppression* of the second call is under test).
_BODY = st.text(alphabet=string.ascii_lowercase + " ", max_size=120)

# A pass-through (already-PII-free derived data) section, read verbatim.
_PASSTHROUGH = st.text(alphabet=string.ascii_lowercase + ", ", max_size=60)

# The validated payload content the scripted client returns.
_MESSAGE = st.text(max_size=80)


# ---------------------------------------------------------------------------
# Property, part 1: a cached result suppresses the repeat call (Req 15.2).
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 12: Repeat requests never re-call the provider
@settings(max_examples=100, deadline=None)
@given(body=_BODY, passthrough=_PASSTHROUGH, message=_MESSAGE, user_id=st.uuids())
def test_cached_result_serves_repeat_with_no_call_no_log_no_quota(
    body: str, passthrough: str, message: str, user_id: UUID
) -> None:
    """Within TTL, an identical repeat is a cache hit: zero provider calls,
    zero new invocation-log rows, zero quota consumption, same payload
    (Requirement 15.2).
    """
    spec = _build_spec(raw_resume=body, passthrough=passthrough, reuse_persisted=False)
    completion_text = json.dumps({"message": message})

    async def _run() -> None:
        session = _FakeSession()
        quota = _CountingQuota()
        redis = _FakeRedis()
        client = _FakeLLMClient(text=completion_text)
        orchestrator = _orchestrator(session=session, quota=quota, redis=redis, client=client)
        match = MatchResult(id=uuid7(), user_id=user_id)

        first = await orchestrator.run(spec, user_id=user_id, match=match, feature_input=None)
        assert first.envelope.is_fallback is False
        assert first.envelope.result.message == message
        assert client.stream_calls == 1
        assert quota.reserve_calls == 1
        assert len(session.rows(LLMInvocationLog)) == 1
        assert len(session.rows(LLMResult)) == 1

        second = await orchestrator.run(spec, user_id=user_id, match=match, feature_input=None)

        # Same payload back, and zero of everything a call would produce.
        assert second.envelope.is_fallback is False
        assert second.envelope.result == first.envelope.result
        assert second.envelope.id == first.envelope.id
        assert client.stream_calls == 1  # zero additional provider calls
        assert quota.reserve_calls == 1  # zero additional quota consumption
        assert len(session.rows(LLMInvocationLog)) == 1  # zero new log rows
        assert len(session.rows(LLMResult)) == 1  # nothing re-persisted

    _run_sync(_run)


# ---------------------------------------------------------------------------
# Property, part 2: coach persisted-result reuse suppresses the repeat call
# even without the cache (Req 5.4).
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 12: Repeat requests never re-call the provider
@settings(max_examples=100, deadline=None)
@given(body=_BODY, passthrough=_PASSTHROUGH, message=_MESSAGE, user_id=st.uuids())
def test_persisted_coach_result_serves_repeat_with_no_call_no_log_no_quota(
    body: str, passthrough: str, message: str, user_id: UUID
) -> None:
    """Under the unchanged active version + model, the coach's repeat is
    served from the persisted LLM_Result — with a fresh, empty cache — so
    the suppression comes from persistence alone: zero provider calls,
    zero new invocation-log rows, zero quota consumption, the stored
    payload returned (Requirement 5.4).
    """
    spec = _build_spec(raw_resume=body, passthrough=passthrough, reuse_persisted=True)
    completion_text = json.dumps({"message": message})

    async def _run() -> None:
        session = _FakeSession()
        quota = _CountingQuota()
        client = _FakeLLMClient(text=completion_text)
        match = MatchResult(id=uuid7(), user_id=user_id)

        first_orchestrator = _orchestrator(
            session=session, quota=quota, redis=_FakeRedis(), client=client
        )
        first = await first_orchestrator.run(spec, user_id=user_id, match=match, feature_input=None)
        assert first.envelope.is_fallback is False
        assert client.stream_calls == 1
        assert quota.reserve_calls == 1
        persisted = session.rows(LLMResult)
        assert len(persisted) == 1
        row = persisted[0]

        # A second orchestrator with an EMPTY cache: only the persisted
        # row can satisfy the repeat (design D7 — reuse precedes the
        # cache lookup and works without it).
        second_orchestrator = _orchestrator(
            session=session, quota=quota, redis=_FakeRedis(), client=client
        )
        second = await second_orchestrator.run(
            spec, user_id=user_id, match=match, feature_input=None
        )

        assert second.envelope.is_fallback is False
        assert second.envelope.id == str(row.id)  # the stored row was served
        assert second.envelope.result == first.envelope.result
        assert second.envelope.result.message == message
        assert second.envelope.prompt_template_version == row.prompt_template_version
        assert client.stream_calls == 1  # zero additional provider calls
        assert quota.reserve_calls == 1  # zero additional quota consumption
        assert len(session.rows(LLMInvocationLog)) == 1  # zero new log rows
        assert len(session.rows(LLMResult)) == 1  # nothing re-persisted

    _run_sync(_run)
