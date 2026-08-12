"""Unit tests for cache and invocation-logging edge behavior (task 7.9).

Covers the failure-posture edges of the LLM request pipeline's storage
adjuncts — the pieces whose failures must degrade, never 5xx:

* **TTL expiry is a miss** (Requirement 15.6): an entry written with the
  configured TTL reads back before expiry and reads back as ``None`` — a
  miss — once the TTL has elapsed.
* **Lookup failure → normal path** (Requirement 15.7): a Redis error (or
  a corrupt entry) during ``LLMCache.get`` returns ``None`` — a miss —
  and never raises, so the caller proceeds down the normal provider-call
  path and the cache failure alone can never produce a 5xx.
* **Write failure → result still returned** (Requirement 15.8): a Redis
  error during ``LLMCache.set`` is swallowed (structured event only), so
  the validated result the caller already holds is still returned.
* **Invocation-log write failure → request completes and breaker opens**
  (Requirements 12.5, 14.8): a failed ``record_invocation`` flush rolls
  back only its SAVEPOINT, returns ``False`` without raising, emits a
  PII-free ``invocation_log_write_failed`` event — and feeding that
  ``False`` to ``SpendCircuitBreaker.record_persist_failure`` forces the
  breaker open (cause ``tracking_failure``) until a later evaluation
  successfully reads a below-limit tracked spend.

Per the design's Testing Strategy and this suite's conventions
(``tests/property/test_quota_call_accounting.py``,
``tests/property/test_invocation_log_exactly_once.py``), everything runs
against injected in-memory fakes: a clock-stepped fake Redis honoring
``SET ... EX`` expiry semantics, error-injecting fake Redis clients, and
fake ``AsyncSession`` objects modeling exactly the SAVEPOINT / spend-query
surface the units touch. No database, no real Redis.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import cast

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.core.redis import Redis
from matchlayer_api.db.models import LLMInvocationLog
from matchlayer_api.ml.llm.client import LLMUsage
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.invocation_log import record_invocation
from matchlayer_api.services.llm.redaction import REDACTOR_VERSION
from matchlayer_api.services.llm.schemas import LLMResultEnvelope
from matchlayer_api.services.llm.spend import BreakerCause, SpendCircuitBreaker

# ---------------------------------------------------------------------------
# Envelope payload stub — the cache is generic over any validated envelope,
# so a minimal payload model keeps the tests focused on cache mechanics.
# ---------------------------------------------------------------------------


class _StubPayload(BaseModel):
    text: str


_Envelope = LLMResultEnvelope[_StubPayload]

_USER_ID = "11111111-1111-7111-8111-111111111111"
_FEATURE = "resume_coach"
_INPUT_HASH = "a" * 64
_TEMPLATE_VERSION = 1
_MODEL = "test-provider/test-model"

_TTL_SECONDS = 100


def _validated_envelope(text: str = "validated LLM output") -> LLMResultEnvelope[_StubPayload]:
    """A persisted, non-fallback envelope — the only shape the cache stores."""
    return _Envelope(
        id=str(uuid.uuid4()),
        is_fallback=False,
        fallback_reason=None,
        prompt_template_version=1,
        created_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        result=_StubPayload(text=text),
    )


async def _cache_get(cache: LLMCache) -> LLMResultEnvelope[_StubPayload] | None:
    """Look up the canonical test key."""
    return await cache.get(
        user_id=_USER_ID,
        feature=_FEATURE,
        input_hash=_INPUT_HASH,
        template_version=_TEMPLATE_VERSION,
        model=_MODEL,
        envelope_type=_Envelope,
    )


async def _cache_set(cache: LLMCache, envelope: LLMResultEnvelope[_StubPayload]) -> None:
    """Write ``envelope`` under the canonical test key."""
    await cache.set(
        user_id=_USER_ID,
        feature=_FEATURE,
        input_hash=_INPUT_HASH,
        template_version=_TEMPLATE_VERSION,
        model=_MODEL,
        envelope=envelope,
    )


def _canonical_key() -> str:
    """The full Redis key the helpers above resolve to."""
    return LLMCache._key(
        user_id=_USER_ID,
        feature=_FEATURE,
        input_hash=_INPUT_HASH,
        template_version=_TEMPLATE_VERSION,
        model=_MODEL,
    )


# ---------------------------------------------------------------------------
# Fake Redis clients (injected per design decision D6; the tests never
# import redis themselves — ``Redis`` is the core/redis.py re-export used
# purely as a typing cast target).
# ---------------------------------------------------------------------------


class _TickingFakeRedis:
    """In-memory fake honoring ``SET ... EX`` expiry against a manual clock.

    ``now`` is advanced by the test; a key whose expiry instant has been
    reached reads back as ``None`` exactly as an expired Redis key does
    (Requirement 15.6 — expiry is Redis-side, so the fake models it at
    the ``get`` boundary).
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.store: dict[str, tuple[str, float | None]] = {}
        self.observed_expirations: list[int | None] = []

    async def get(self, key: str) -> str | None:
        entry = self.store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self.now >= expires_at:
            del self.store[key]
            return None
        return value

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.observed_expirations.append(ex)
        expires_at = self.now + ex if ex is not None else None
        self.store[key] = (value, expires_at)


class _BrokenGetRedis:
    """Fake whose reads always fail — the Requirement 15.7 lookup edge."""

    def __init__(self) -> None:
        self.set_calls = 0

    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis unreachable during lookup")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls += 1


class _BrokenSetRedis:
    """Fake whose writes always fail — the Requirement 15.8 write edge."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise ConnectionError("redis unreachable during write")


# ---------------------------------------------------------------------------
# Fake AsyncSession surfaces.
#
# ``record_invocation`` touches ``begin_nested()`` + ``add(row)`` (the
# SAVEPOINT pattern of Requirement 12.5); ``SpendCircuitBreaker.evaluate``
# reaches storage only through ``session.execute(stmt).scalar_one()``.
# ---------------------------------------------------------------------------


class _FakeSavepoint:
    """Async context manager standing in for ``AsyncSessionTransaction``."""

    def __init__(self, session: _FakeLogSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if self._session.fail_flush:
            # The real session flushes on savepoint exit and raises when
            # that flush fails; the pending row rolls back with the
            # savepoint (Requirement 12.5).
            self._session._pending.clear()
            raise RuntimeError("simulated flush failure inside SAVEPOINT")
        if exc_type is None:
            self._session.persisted.extend(self._session._pending)
        self._session._pending.clear()
        return False


class _FakeLogSession:
    """In-memory fake ``AsyncSession`` for the invocation-log write path."""

    def __init__(self, *, fail_flush: bool = False) -> None:
        self.fail_flush = fail_flush
        self.persisted: list[LLMInvocationLog] = []
        self._pending: list[LLMInvocationLog] = []

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint(self)

    def add(self, row: LLMInvocationLog) -> None:
        self._pending.append(row)


class _ScalarResult:
    def __init__(self, value: Decimal | None) -> None:
        self._value = value

    def scalar_one(self) -> Decimal | None:
        return self._value


class _FakeSpendSession:
    """Fake session yielding scripted monthly-spend sums to ``evaluate``."""

    def __init__(self, totals: list[Decimal | None]) -> None:
        self._totals: Iterator[Decimal | None] = iter(totals)

    async def execute(self, stmt: object) -> _ScalarResult:
        return _ScalarResult(next(self._totals))


# ---------------------------------------------------------------------------
# TTL expiry is a miss (Requirement 15.6).
# ---------------------------------------------------------------------------


async def test_expired_entry_reads_back_as_miss() -> None:
    """An entry written with the configured TTL is a hit before expiry and
    a miss (``None``) once the TTL has elapsed (Requirement 15.6)."""
    fake = _TickingFakeRedis()
    cache = LLMCache(cast(Redis, fake), ttl_seconds=_TTL_SECONDS)
    envelope = _validated_envelope()

    await _cache_set(cache, envelope)

    # The write carried the configured TTL to Redis (SET ... EX).
    assert fake.observed_expirations == [_TTL_SECONDS]

    # Before expiry: a hit, round-tripping the exact validated envelope.
    fake.now = float(_TTL_SECONDS - 1)
    hit = await _cache_get(cache)
    assert hit == envelope

    # At/after expiry: the entry is gone — a miss, not an error.
    fake.now = float(_TTL_SECONDS)
    assert await _cache_get(cache) is None


# ---------------------------------------------------------------------------
# Lookup failure → normal path (Requirement 15.7).
# ---------------------------------------------------------------------------


async def test_lookup_failure_is_a_miss_and_never_raises() -> None:
    """A Redis error during lookup returns ``None`` — a miss — so the
    caller proceeds down the normal provider-call path; the failure is
    reported only as a structured event (Requirement 15.7)."""
    cache = LLMCache(cast(Redis, _BrokenGetRedis()), ttl_seconds=_TTL_SECONDS)

    with structlog.testing.capture_logs() as captured:
        result = await _cache_get(cache)

    assert result is None
    events = [e for e in captured if e["event"] == "llm_cache_lookup_failed"]
    assert len(events) == 1


async def test_corrupt_entry_is_a_miss_and_never_raises() -> None:
    """An entry that no longer validates against the envelope schema is a
    miss, never an exception or a malformed response (Requirement 15.7)."""
    fake = _TickingFakeRedis()
    cache = LLMCache(cast(Redis, fake), ttl_seconds=_TTL_SECONDS)
    fake.store[_canonical_key()] = ("{not valid json at all", None)

    with structlog.testing.capture_logs() as captured:
        result = await _cache_get(cache)

    assert result is None
    events = [e for e in captured if e["event"] == "llm_cache_entry_invalid"]
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Write failure → result still returned (Requirement 15.8).
# ---------------------------------------------------------------------------


async def test_write_failure_is_swallowed_so_result_is_still_returned() -> None:
    """A Redis error during the cache write never propagates: ``set``
    returns normally, so the validated result the caller already holds is
    returned to the requester; the failure is a structured event carrying
    no cached content (Requirement 15.8)."""
    fake = _BrokenSetRedis()
    cache = LLMCache(cast(Redis, fake), ttl_seconds=_TTL_SECONDS)
    sentinel = f"CACHED-CONTENT-{uuid.uuid4().hex}"
    envelope = _validated_envelope(text=sentinel)

    with structlog.testing.capture_logs() as captured:
        # Must not raise — that is the whole guarantee.
        await _cache_set(cache, envelope)

    assert fake.store == {}
    events = [e for e in captured if e["event"] == "llm_cache_write_failed"]
    assert len(events) == 1
    # The event carries identifiers only — never the envelope content.
    assert sentinel not in repr(events[0])


# ---------------------------------------------------------------------------
# Invocation-log write failure → request completes and breaker opens
# (Requirements 12.5, 14.8).
# ---------------------------------------------------------------------------


def _fixed_clock() -> datetime:
    """Injected mid-month UTC instant, per the design's Testing Strategy."""
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


async def _record(session: _FakeLogSession) -> bool:
    """Drive ``record_invocation`` with a representative successful call."""
    return await record_invocation(
        cast(AsyncSession, session),
        user_id=uuid.uuid4(),
        match_result_id=uuid.uuid4(),
        feature=LLMFeature.RESUME_COACH,
        prompt_template_version=1,
        llm_model=_MODEL,
        redactor_version=REDACTOR_VERSION,
        input_hash="b" * 64,
        latency_ms=1234,
        usage=LLMUsage(
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal("0.001"),
            cost_basis="provider_reported",
        ),
        output={"summary": "ok"},
        failure_category=None,
    )


async def test_invocation_log_write_failure_returns_false_without_raising() -> None:
    """A failed invocation-log flush is swallowed: the function returns
    ``False`` (never raises, so the user-facing request completes and no
    5xx can originate here), persists nothing, and emits one PII-free
    ``invocation_log_write_failed`` event (Requirement 12.5)."""
    session = _FakeLogSession(fail_flush=True)

    with structlog.testing.capture_logs() as captured:
        ok = await _record(session)

    assert ok is False
    assert session.persisted == []
    events = [e for e in captured if e["event"] == "invocation_log_write_failed"]
    assert len(events) == 1
    # Identifiers only: no output payload, no hash-source text, no PII.
    assert set(events[0]) == {
        "event",
        "log_level",
        "feature",
        "user_id",
        "match_result_id",
        "llm_model",
    }


async def test_invocation_log_success_path_still_returns_true() -> None:
    """Contrast case: a clean flush persists exactly one row and returns
    ``True`` — the breaker force-open of Requirement 14.8 is keyed off the
    ``False`` return alone."""
    session = _FakeLogSession(fail_flush=False)

    ok = await _record(session)

    assert ok is True
    assert len(session.persisted) == 1


async def test_persist_failure_forces_breaker_open_until_below_limit_read() -> None:
    """Feeding a failed persist to the breaker forces it open with cause
    ``tracking_failure`` and an unknown tracked spend; the next evaluation
    that successfully reads a below-limit sum closes it again — no restart,
    no manual intervention (Requirement 14.8)."""
    breaker = SpendCircuitBreaker(limit_provider=lambda: Decimal("10"), clock=_fixed_clock)
    assert breaker.state.is_open is False

    # The completed call's cost never entered the tracked spend →
    # fail-safe open, tracked spend reported as unknown.
    with structlog.testing.capture_logs() as captured:
        state = breaker.record_persist_failure()

    assert state.is_open is True
    assert state.cause is BreakerCause.TRACKING_FAILURE
    assert state.tracked_spend is None
    transitions = [e for e in captured if e["event"] == "llm_spend_breaker_transition"]
    assert len(transitions) == 1
    assert transitions[0]["direction"] == "opened"
    assert transitions[0]["tracked_spend"] == "unknown"

    # A repeated persist failure keeps it open without a second event
    # (exactly one structured event per open↔closed change, Req 14.6).
    with structlog.testing.capture_logs() as captured:
        state = breaker.record_persist_failure()
    assert state.is_open is True
    assert [e for e in captured if e["event"] == "llm_spend_breaker_transition"] == []

    # A subsequent evaluation that successfully reads a below-limit sum
    # closes the breaker — the recovery half of Requirement 14.8.
    session = _FakeSpendSession(totals=[Decimal("2.50")])
    state = await breaker.evaluate(cast(AsyncSession, session))

    assert state.is_open is False
    assert state.cause is None
    assert state.tracked_spend == Decimal("2.50")


async def test_write_failure_then_breaker_open_end_to_end() -> None:
    """The full Requirement 12.5 + 14.8 wiring at the unit seam: the failed
    write returns ``False`` (request completes), and the caller's mandated
    reaction — ``record_persist_failure`` — leaves the breaker open."""
    session = _FakeLogSession(fail_flush=True)
    breaker = SpendCircuitBreaker(limit_provider=lambda: Decimal("10"), clock=_fixed_clock)

    ok = await _record(session)
    assert ok is False

    state = breaker.record_persist_failure()
    assert state.is_open is True
    assert state.cause is BreakerCause.TRACKING_FAILURE


# ---------------------------------------------------------------------------
# Guard: the fakes stay honest.
# ---------------------------------------------------------------------------


async def test_fake_redis_never_expires_keys_written_without_ex() -> None:
    """Fake self-check: only ``EX``-carrying writes expire, mirroring Redis."""
    fake = _TickingFakeRedis()
    fake.store["k"] = ("v", None)
    fake.now = 1e9
    assert await fake.get("k") == "v"
