"""Feature: phase-3-llm-layer — Property 4.

# Feature: phase-3-llm-layer, Property 4: Hash and cache-key consistency over redacted text

Property 4: Hash and cache-key consistency over redacted text.

    *For any* raw feature input, the invocation-log input hash and the
    LLM_Cache key hash component are equal, are computed from the
    redacted text (independently recomputing ``sha256`` over the
    canonical redacted byte string yields the same digest), and contain
    no raw-PII-derived bytes; and *for any* two cache-key component
    tuples (input hash, template version, model, user id), the derived
    cache keys are equal if and only if the tuples are equal.

**Validates: Requirements 3.8, 12.6, 15.1**

Two properties cover the two halves of the statement:

1. **One redacted digest keys both artifacts** — the real
   :class:`LLMOrchestrator` pipeline is driven end-to-end against
   scripted fakes (the ``tests/unit/test_llm_orchestrator.py`` harness
   convention), with a unique PII sentinel (an email address) planted in
   the raw resume section. After a successful run the property asserts:

   * the single Redis cache key embeds exactly the digest recorded on
     the single invocation-log row (Req 12.6, 15.1 — same hash function,
     same redacted prompt input, byte-for-byte);
   * independently recomputing ``sha256`` over the canonical redacted
     byte string — ``feature | template_version | model | <compact
     key-sorted JSON of values + ordered (kind, redacted_text)
     sections>``, rebuilt here from :func:`redact` and :mod:`json` +
     :mod:`hashlib` alone, never via ``compute_input_hash`` — yields
     that same digest (Req 3.8: the hash is over redacted text);
   * the digest computed over the *raw* pre-redaction canonical string
     differs, and the planted PII sentinel appears in neither the cache
     key nor any serialized invocation-log column (Req 3.8: no derived
     artifact encodes raw PII).

2. **Cache-key injectivity** — for generated pairs of component tuples
   (input hash, template version, model, user id; the feature segment
   held fixed), the keys produced by the real :class:`LLMCache` write
   path are equal iff the tuples are equal (Req 15.1: any change to
   prompt input, template version, or model produces a different key,
   and the user id always scopes the entry). Generators constrain each
   component to its production shape — 64-hex digests, UUID-string user
   ids, integer versions, colon-free model names — so equality is
   decided by the components, not by delimiter forgery outside the real
   input space.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from matchlayer_api.db.models import LLMInvocationLog, MatchResult
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
from matchlayer_api.services.llm.redaction import redact
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope
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


class _FakeQuota:
    """Always-allowing DailyQuota: both gates pass, remaining fixed."""

    async def gate(self, user_id: str) -> QuotaDecision:
        return QuotaDecision(allowed=True, remaining=5)

    async def reserve(self, user_id: str) -> QuotaDecision:
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
    """Scripted ``LLMClient`` replaying one valid JSON completion."""

    def __init__(self) -> None:
        self._completion: LLMCompletion | None = None

    async def validate_credentials(self) -> None:
        return None

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMStreamChunk]:
        text = '{"message": "ok"}'
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


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


def _canonical_digest(
    *,
    feature: str,
    template_version: int,
    model: str,
    values: dict[str, str],
    sections: list[tuple[str, str]],
) -> str:
    """Independent sha256 over the canonical byte string — no orchestrator code.

    Rebuilds the documented canonical form (``feature | template_version |
    model | <compact key-sorted JSON of values + ordered (kind, text)
    sections>``) from :mod:`json` and :mod:`hashlib` alone, so agreement
    with the recorded digest proves what the digest was computed over —
    rather than re-running the unit under test.
    """
    serialized = json.dumps(
        {"values": values, "sections": [[kind, text] for kind, text in sections]},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    canonical = f"{feature}|{template_version}|{model}|{serialized}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_row(row: LLMInvocationLog) -> str:
    """Concatenate the repr of every mapped column value on ``row``."""
    parts: list[str] = []
    for column in LLMInvocationLog.__table__.columns:
        parts.append(f"{column.key}={getattr(row, column.key)!r}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Strategies.
# ---------------------------------------------------------------------------

# Resume body text: lowercase single-line prose, so the planted email on the
# first line always sits in the redacted (non-exempt) region and the name
# heuristic finds nothing to vary on. The redactor itself is exercised across
# arbitrary inputs by Properties 1-3; here it is the *hash provenance* under
# test, so the generator guarantees redaction changes the text.
_BODY = st.text(alphabet=string.ascii_lowercase + " ", max_size=120)

# A pass-through (already-PII-free derived data) section, read verbatim.
_PASSTHROUGH = st.text(alphabet=string.ascii_lowercase + ", ", max_size=60)

# Template placeholder values for the resume_coach template's two slots.
_BOUND = st.integers(min_value=1, max_value=9).map(str)

# Unique PII sentinel material for the planted email address.
_TOKEN = st.uuids(version=4).map(lambda u: u.hex)


# ---------------------------------------------------------------------------
# Property, part 1: one redacted digest keys the log row and the cache entry.
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 4: Hash and cache-key consistency over redacted text
@settings(max_examples=100, deadline=None)
@given(
    body=_BODY,
    passthrough=_PASSTHROUGH,
    min_improvements=_BOUND,
    max_improvements=_BOUND,
    user_id=st.uuids(),
    token=_TOKEN,
)
def test_log_hash_and_cache_key_share_one_redacted_digest(
    body: str,
    passthrough: str,
    min_improvements: str,
    max_improvements: str,
    user_id: UUID,
    token: str,
) -> None:
    """The invocation-log hash and cache-key hash are one redacted digest.

    A raw feature input with a planted PII sentinel is run through the
    real pipeline; the recorded digest must key the cache entry exactly,
    must equal an independent sha256 over the canonical *redacted* byte
    string, must differ from the raw-text digest, and no derived artifact
    may contain the sentinel (Requirements 3.8, 12.6, 15.1).
    """
    email = f"user.{token}@example.com"
    raw_resume = f"reach me at {email}\n{body}"
    values = {"min_improvements": min_improvements, "max_improvements": max_improvements}

    def _build_inputs(match: MatchResult, feature_input: None) -> PromptInputs:
        return PromptInputs(
            values=values,
            sections=[
                PromptSection(kind="resume", text=raw_resume, redaction="resume"),
                PromptSection(kind="missing_skills", text=passthrough, redaction=None),
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

    async def _run() -> None:
        session = _FakeSession()
        redis = _RecordingRedis()
        orchestrator = LLMOrchestrator(
            session=session,  # type: ignore[arg-type]
            quota=_FakeQuota(),  # type: ignore[arg-type]
            cache=LLMCache(redis, ttl_seconds=60),  # type: ignore[arg-type]
            breaker=_FakeBreaker(),  # type: ignore[arg-type]
            client_factory=_FakeLLMClient,
            settings=_build_settings(),
            key_present=lambda: True,
        )
        match = MatchResult(id=uuid7(), user_id=user_id)

        outcome = await orchestrator.run(spec, user_id=user_id, match=match, feature_input=None)
        assert outcome.envelope.is_fallback is False

        # Exactly one invocation-log row and one cache write happened.
        logs = session.rows(LLMInvocationLog)
        assert len(logs) == 1
        row = logs[0]
        assert redis.set_keys == [
            f"llm:cache:{user_id}:resume_coach:{row.input_hash}"
            f":v{row.prompt_template_version}:{row.llm_model}"
        ]
        cache_key = redis.set_keys[0]

        # Independent recomputation over the canonical *redacted* byte
        # string yields the recorded digest (Req 3.8, 12.6, 15.1): same
        # hash function, same redacted prompt input, for both artifacts.
        redacted_resume = redact(raw_resume, kind="resume").text
        assert email not in redacted_resume  # the sentinel was redacted
        assert redacted_resume != raw_resume
        redacted_sections = [("resume", redacted_resume), ("missing_skills", passthrough)]
        assert row.input_hash == _canonical_digest(
            feature="resume_coach",
            template_version=row.prompt_template_version,
            model=row.llm_model,
            values=values,
            sections=redacted_sections,
        )

        # No raw-PII-derived bytes: the digest over the raw pre-redaction
        # canonical string is a *different* digest, and the sentinel
        # appears in neither derived artifact (Req 3.8).
        raw_sections = [("resume", raw_resume), ("missing_skills", passthrough)]
        assert row.input_hash != _canonical_digest(
            feature="resume_coach",
            template_version=row.prompt_template_version,
            model=row.llm_model,
            values=values,
            sections=raw_sections,
        )
        assert email not in cache_key
        assert email not in _serialize_row(row)

    _run_sync(_run)


# ---------------------------------------------------------------------------
# Property, part 2: cache keys are equal iff the component tuples are equal.
# ---------------------------------------------------------------------------

# Cache-key components in their production shapes: the digest is a 64-hex
# sha256 (Req 15.1), user ids are UUID strings (conventions.md), template
# versions are small positive integers, and model identifiers are the
# colon-free provider ids the settings accept (e.g. "gpt-4o-mini").
_INPUT_HASH = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_TEMPLATE_VERSION = st.integers(min_value=1, max_value=9)
_MODEL = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-./",
    min_size=1,
    max_size=24,
)
_USER_ID_STR = st.uuids(version=4).map(str)

_KEY_TUPLE = st.tuples(_INPUT_HASH, _TEMPLATE_VERSION, _MODEL, _USER_ID_STR)


@st.composite
def _key_tuple_pairs(
    draw: st.DrawFn,
) -> tuple[tuple[str, int, str, str], tuple[str, int, str, str]]:
    """Two component tuples; forced equal half the time so both sides bite."""
    first = draw(_KEY_TUPLE)
    if draw(st.booleans()):
        return first, first
    return first, draw(_KEY_TUPLE)


# Feature: phase-3-llm-layer, Property 4: Hash and cache-key consistency over redacted text
@settings(max_examples=100, deadline=None)
@given(pair=_key_tuple_pairs())
def test_cache_keys_equal_iff_component_tuples_equal(
    pair: tuple[tuple[str, int, str, str], tuple[str, int, str, str]],
) -> None:
    """Derived cache keys are equal if and only if the tuples are equal.

    Two writes through the real :class:`LLMCache` produce equal keys
    exactly when their (input hash, template version, model, user id)
    tuples are equal — so any change to the redacted prompt input, the
    template version, or the model can never serve a stale entry, and
    the user id always scopes the key (Requirement 15.1).
    """
    first, second = pair

    envelope = LLMResultEnvelope[_EchoResult](
        id=str(uuid7()),
        is_fallback=False,
        prompt_template_version=1,
        result=_EchoResult(message="ok"),
    )

    async def _run() -> None:
        redis = _RecordingRedis()
        cache = LLMCache(redis, ttl_seconds=60)  # type: ignore[arg-type]
        for input_hash, template_version, model, user_id in (first, second):
            await cache.set(
                user_id=user_id,
                feature="resume_coach",
                input_hash=input_hash,
                template_version=template_version,
                model=model,
                envelope=envelope,
            )

        key_first, key_second = redis.set_keys
        assert (key_first == key_second) == (first == second)

    _run_sync(_run)
