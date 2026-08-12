"""Feature: phase-3-llm-layer — Property 9.

# Feature: phase-3-llm-layer, Property 9: Universal fallback outcome for any LLM failure

Property 9: Universal fallback outcome for any LLM failure.

    *For any* injected failure mode from the closed set (provider error,
    timeout, runtime key rejection, schema-validation failure, redaction
    failure/timeout, missing prompt template, quota-accounting
    unavailability), the request produces the feature's
    Fallback_Response — HTTP 200 for non-streaming, degraded terminal
    event for streaming — never a 5xx caused by the failure; at most one
    provider call attempt is made; exactly one structured failure log
    event is emitted carrying the matching failure-reason enum value,
    request id, user id, feature, and prompt version and containing no
    planted PII sentinel and no API key; and no LLM_Result row or cache
    entry is created.

**Validates: Requirements 1.11, 1.12, 2.5, 3.6, 9.1, 9.2, 9.4, 9.5, 9.6, 13.8**

The real :class:`LLMOrchestrator` pipeline is driven end-to-end against
the scripted fakes of the ``tests/unit/test_llm_orchestrator.py`` harness
convention, with each failure mode injected at its genuine pipeline stage:

* **provider error / timeout / runtime key rejection** — the scripted
  ``LLMClient`` raises :class:`LLMError` with the matching category
  mid-stream (a runtime 401 surfaces as the adapter's ``invalid_key``
  category, which the taxonomy maps onto ``provider_error`` — Req 1.11);
* **schema-validation failure** — the client replays a complete raw
  response that cannot validate against the feature schema (malformed
  JSON, off-schema keys, truncation, wrong types);
* **redaction failure** — :class:`RedactionError` raised on the redaction
  stage (Req 3.6: unredacted text never proceeds);
* **missing prompt template** — an unfillable placeholder map, so prompt
  assembly fails and nothing is transmitted (Req 2.5);
* **quota-accounting unavailability** — :class:`QuotaAccountingError`
  raised at the read-only gate and, separately, at the atomic reserve
  (Req 13.8).

Sentinels: a unique planted email address travels in the raw resume
section, and a unique API-key-shaped token is planted in every injected
exception's message — the one place a careless handler would leak it from.
The captured event stream must contain neither. Log capture uses a manual
``structlog.configure`` with ``merge_contextvars`` + ``LogCapture``
(the ``tests/unit/test_matching_fallback_ladder.py`` pattern) so the
request id bound by the request-id middleware convention is visible on
the asserted event, which plain ``capture_logs`` would drop.
"""

from __future__ import annotations

import asyncio
import string
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from types import TracebackType
from typing import Any
from uuid import UUID

import structlog
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings
from matchlayer_api.db.models import LLMResult, MatchResult
from matchlayer_api.ml.llm.client import (
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMStreamChunk,
    LLMUsage,
)
from matchlayer_api.ml.prompts.registry import ACTIVE_PROMPT_VERSIONS, LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    LLMOrchestrator,
    LLMOutcome,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.quota import QuotaAccountingError, QuotaDecision
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


_REQUEST_ID = "req-prop9-fixed"


# ---------------------------------------------------------------------------
# Fakes — the exact surfaces the orchestrator touches, mirroring
# tests/unit/test_llm_orchestrator.py.
# ---------------------------------------------------------------------------


class _EchoResult(BaseModel):
    """Minimal result schema standing in for a feature payload."""

    model_config = ConfigDict(extra="forbid")

    message: str


class _FakeQuota:
    """Scripted DailyQuota: optional gate/reserve QuotaAccountingError."""

    def __init__(
        self,
        *,
        gate_error_message: str | None = None,
        reserve_error_message: str | None = None,
    ) -> None:
        self.gate_error_message = gate_error_message
        self.reserve_error_message = reserve_error_message
        self.reserve_calls = 0

    async def gate(self, user_id: str) -> QuotaDecision:
        if self.gate_error_message is not None:
            raise QuotaAccountingError(self.gate_error_message)
        return QuotaDecision(allowed=True, remaining=5)

    async def reserve(self, user_id: str) -> QuotaDecision:
        self.reserve_calls += 1
        if self.reserve_error_message is not None:
            raise QuotaAccountingError(self.reserve_error_message)
        return QuotaDecision(allowed=True, remaining=4)


class _RecordingRedis:
    """In-memory redis fake recording every key written via ``set``."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_keys: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_keys.append(key)
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
    """Scripted ``LLMClient``: replays chunks, optionally fails, counts calls."""

    def __init__(self, *, chunks: list[str] | None = None, error: LLMError | None = None) -> None:
        self.chunks = chunks if chunks is not None else []
        self.error = error
        self.stream_calls = 0
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        self.stream_calls += 1
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


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


@contextmanager
def _capture_with_request_id() -> Iterator[list[dict[str, Any]]]:
    """Capture events *with* contextvars merged, unlike ``capture_logs``.

    Production binds ``request_id`` into structlog contextvars in the
    request-id middleware; the failure event must carry it (Req 9.4).
    ``structlog.testing.capture_logs`` replaces the processor chain
    wholesale (dropping ``merge_contextvars``), so this configures the
    chain manually and binds the fixed test request id — the
    ``tests/unit/test_matching_fallback_ladder.py`` pattern.
    """
    capture = structlog.testing.LogCapture()
    previous = structlog.get_config()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, capture])
    structlog.contextvars.bind_contextvars(request_id=_REQUEST_ID)
    try:
        yield capture.entries
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.configure(**previous)


# ---------------------------------------------------------------------------
# The closed failure-mode set (design Property 9) and its injection points.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FailureMode:
    """One injectable failure mode with its expected pipeline outcome.

    ``expected_provider_calls`` distinguishes pre-call failures (0: the
    pipeline never reaches the provider) from call-phase failures (1:
    exactly the single permitted attempt — never a retry, Req 1.12/9.6).
    ``key_in_exception`` marks modes whose injected exception message can
    carry the planted API-key sentinel (the leak vector under test).
    """

    name: str
    expected_reason: FailureReason
    expected_provider_calls: int
    client_error_category: str | None = None
    client_chunks: tuple[str, ...] = ()
    quota_gate_error: bool = False
    quota_reserve_error: bool = False
    bad_redaction_kind: bool = False
    missing_placeholder: bool = False
    key_in_exception: bool = False


# Raw responses that must fail terminal validation against ``_EchoResult``
# (malformed JSON, truncation, off-schema key, wrong type, bare scalar).
# Exhaustive raw-response-space coverage is Property 8's job; here one
# failing response per shape suffices to drive the fallback path.
_INVALID_RESPONSES: tuple[tuple[str, ...], ...] = (
    ("not json at all",),
    ('{"message": "hel',),
    ('{"message": "ok", ', '"extra": true}'),
    ('{"message": 123}',),
    ('"just a string"',),
)

_MODES: tuple[_FailureMode, ...] = (
    _FailureMode(
        name="provider_error",
        expected_reason=FailureReason.PROVIDER_ERROR,
        expected_provider_calls=1,
        client_error_category="provider_error",
        client_chunks=('{"message": ',),  # fails mid-stream
        key_in_exception=True,
    ),
    _FailureMode(
        name="timeout",
        expected_reason=FailureReason.TIMEOUT,
        expected_provider_calls=1,
        client_error_category="timeout",
        key_in_exception=True,
    ),
    # A runtime 401 rejection of the configured key surfaces from the
    # adapter as a non-timeout LLMError category; the taxonomy maps it
    # onto provider_error and the request degrades, never 5xxs (Req 1.11).
    _FailureMode(
        name="runtime_key_rejection",
        expected_reason=FailureReason.PROVIDER_ERROR,
        expected_provider_calls=1,
        client_error_category="invalid_key",
        key_in_exception=True,
    ),
    _FailureMode(
        name="redaction_failure",
        expected_reason=FailureReason.REDACTION_FAILED,
        expected_provider_calls=0,
        bad_redaction_kind=True,
    ),
    _FailureMode(
        name="missing_prompt_template",
        expected_reason=FailureReason.PROMPT_TEMPLATE_MISSING,
        expected_provider_calls=0,
        missing_placeholder=True,
    ),
    _FailureMode(
        name="quota_accounting_unavailable_at_gate",
        expected_reason=FailureReason.QUOTA_ACCOUNTING_UNAVAILABLE,
        expected_provider_calls=0,
        quota_gate_error=True,
        key_in_exception=True,
    ),
    _FailureMode(
        name="quota_accounting_unavailable_at_reserve",
        expected_reason=FailureReason.QUOTA_ACCOUNTING_UNAVAILABLE,
        expected_provider_calls=0,
        quota_reserve_error=True,
        key_in_exception=True,
    ),
)

_SCHEMA_MODES: tuple[_FailureMode, ...] = tuple(
    _FailureMode(
        name=f"schema_validation_failure_{i}",
        expected_reason=FailureReason.SCHEMA_VALIDATION_FAILED,
        expected_provider_calls=1,
        client_chunks=chunks,
    )
    for i, chunks in enumerate(_INVALID_RESPONSES)
)

_ALL_MODES: tuple[_FailureMode, ...] = _MODES + _SCHEMA_MODES


def _serialize_events(entries: list[dict[str, Any]]) -> str:
    """Flatten every captured event dict for sentinel-absence checks."""
    return " ".join(f"{key}={value!r}" for entry in entries for key, value in entry.items())


# ---------------------------------------------------------------------------
# Strategies.
# ---------------------------------------------------------------------------

# Resume body text: lowercase single-line prose so the planted email on the
# first line always sits in a redacted (non-exempt) region. Redactor
# behavior across arbitrary inputs is Properties 1-3; here the pipeline's
# failure handling is under test.
_BODY = st.text(alphabet=string.ascii_lowercase + " ", max_size=120)

# Unique sentinel material (PII email + API-key-shaped token).
_TOKEN = st.uuids(version=4).map(lambda u: u.hex)

_MODE = st.sampled_from(_ALL_MODES)


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 9: Universal fallback outcome for any LLM failure
@settings(max_examples=100, deadline=None)
@given(mode=_MODE, body=_BODY, user_id=st.uuids(), token=_TOKEN)
def test_any_llm_failure_produces_fallback_one_event_no_persistence(
    mode: _FailureMode,
    body: str,
    user_id: UUID,
    token: str,
) -> None:
    """Any injected failure lands on the universal fallback outcome.

    The request completes with the feature's Fallback_Response (never an
    exception → never a 5xx, Req 9.1); at most one provider attempt was
    made (Req 1.12, 9.6); exactly one ``llm_feature_failed`` event carries
    the matching reason, request id, user id, feature, and prompt version
    with no planted PII or API-key sentinel (Req 2.5, 3.6, 9.4); and no
    LLM_Result row or cache entry exists afterwards (Req 9.5).
    """
    email = f"user.{token}@example.com"
    key_sentinel = f"sk-or-v1-{token}"  # gitleaks:allow — synthetic sentinel, never a real key
    raw_resume = f"reach me at {email}\n{body}"

    # Missing-template mode: an unfillable placeholder map means prompt
    # assembly fails and nothing is ever transmitted (Req 2.5).
    values = {} if mode.missing_placeholder else {"min_improvements": "3", "max_improvements": "10"}
    # Redaction-failure mode: the redaction stage raises RedactionError,
    # so unredacted text never proceeds past it (Req 3.6).
    resume_redaction = "unknown-kind" if mode.bad_redaction_kind else "resume"

    def _build_inputs(match: MatchResult, feature_input: None) -> PromptInputs:
        return PromptInputs(
            values=values,
            sections=[
                PromptSection(kind="resume", text=raw_resume, redaction=resume_redaction),
                PromptSection(kind="missing_skills", text="docker, kubernetes", redaction=None),
            ],
        )

    def _build_fallback(
        match: MatchResult, feature_input: None, reason: FailureReason
    ) -> _EchoResult:
        return _EchoResult(message=f"fallback:{reason.value}")

    spec = LLMFeatureSpec(
        feature=LLMFeature.RESUME_COACH,
        result_schema=_EchoResult,
        build_inputs=_build_inputs,
        build_fallback=_build_fallback,
    )

    exception_detail = f"upstream said no; header was Bearer {key_sentinel}"
    client_error = (
        LLMError(mode.client_error_category, exception_detail)
        if mode.client_error_category is not None
        else None
    )
    client = _FakeLLMClient(chunks=list(mode.client_chunks), error=client_error)
    quota = _FakeQuota(
        gate_error_message=exception_detail if mode.quota_gate_error else None,
        reserve_error_message=exception_detail if mode.quota_reserve_error else None,
    )

    async def _run() -> None:
        session = _FakeSession()
        redis = _RecordingRedis()
        orchestrator = LLMOrchestrator(
            session=session,  # type: ignore[arg-type]
            quota=quota,  # type: ignore[arg-type]
            cache=LLMCache(redis, ttl_seconds=60),  # type: ignore[arg-type]
            breaker=_FakeBreaker(),  # type: ignore[arg-type]
            client_factory=lambda: client,
            settings=_build_settings(),
            key_present=lambda: True,
        )
        match = MatchResult(id=uuid7(), user_id=user_id)

        # Never an exception from an LLM failure (Req 9.1): ``run``
        # returning at all is the "never a 5xx" half of the property.
        outcome: LLMOutcome[_EchoResult] = await orchestrator.run(
            spec, user_id=user_id, match=match, feature_input=None
        )

        # The feature's Fallback_Response with the matching reason,
        # unmistakably marked and carrying no persisted-row fields
        # (Req 9.2, 9.5).
        envelope = outcome.envelope
        assert envelope.is_fallback is True
        assert envelope.fallback_reason is mode.expected_reason
        assert envelope.result.message == f"fallback:{mode.expected_reason.value}"
        assert envelope.id is None
        assert envelope.prompt_template_version is None
        assert envelope.created_at is None

        # At most one provider call attempt — exactly the expected count
        # for the injected stage, never a retry (Req 1.12, 9.6, 13.8).
        assert client.stream_calls <= 1
        assert client.stream_calls == mode.expected_provider_calls

        # No LLM_Result row and no cache entry (Req 9.5).
        assert session.rows(LLMResult) == []
        assert redis.store == {}
        assert redis.set_keys == []

    with _capture_with_request_id() as entries:
        _run_sync(_run)

    # Exactly one structured failure event (Req 9.4) carrying the
    # matching failure-reason enum value, request id, user id, feature,
    # and the registry-active prompt version.
    failure_events = [entry for entry in entries if entry.get("event") == "llm_feature_failed"]
    assert len(failure_events) == 1
    event = failure_events[0]
    assert event["reason"] == mode.expected_reason.value
    assert event["feature"] == "resume_coach"
    assert event["prompt_template_version"] == ACTIVE_PROMPT_VERSIONS[LLMFeature.RESUME_COACH]
    assert event["user_id"] == str(user_id)
    assert event["request_id"] == _REQUEST_ID

    # No planted PII sentinel and no API key anywhere in the captured
    # event stream — including the modes whose injected exception message
    # deliberately embeds the key sentinel (Req 2.5, 3.6, 9.4).
    serialized = _serialize_events(entries)
    assert email not in serialized
    assert key_sentinel not in serialized
