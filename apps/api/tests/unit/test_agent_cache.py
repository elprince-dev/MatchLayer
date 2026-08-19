"""Unit tests for the Agent_Cache (task 8.3, phase-4-agentic).

Covers the Requirement 9.7 / 9.10 behavior of
``services/agent_jobs/cache.py``:

* **Round-trip**: a non-degraded output written under a key reads back
  as the same fully validated model, and the write carries the
  configured TTL (``agent_cache_ttl_seconds``).
* **User isolation**: the user id is a structural key component, so two
  users with otherwise identical key parts never see each other's
  entries (Requirement 9.7, ``security.md``).
* **Degraded exclusion**: ``set`` rejects a ``degraded=True`` output
  with ``ValueError`` before any Redis I/O (Requirement 9.7).
* **Read failure → miss** and **write failure → proceed**, each without
  raising (Requirement 9.10).
* **Corrupt entry → miss**: an entry that no longer validates degrades
  to a fresh computation, never an error.
* **TTL expiry is a miss**: an expired key reads back as ``None``.
* **Input-hash canon**: ``compute_agent_input_hash`` is deterministic
  and sensitive to agent name, prompt version, and input content.

Everything runs against injected in-memory fakes mirroring
``tests/unit/test_llm_cache_and_logging_edges.py`` — no real Redis.
"""

from __future__ import annotations

from typing import cast

import pytest

from matchlayer_api.core.redis import Redis
from matchlayer_api.ml.agents.state import SkillGapEntry, SkillGapReport
from matchlayer_api.services.agent_jobs.cache import (
    AgentCache,
    compute_agent_input_hash,
)

_USER_ID = "11111111-1111-7111-8111-111111111111"
_OTHER_USER_ID = "22222222-2222-7222-8222-222222222222"
_AGENT_NAME = "skill_gap"
_PROMPT_VERSION = "gap_rules.v1"
_INPUT_HASH = "a" * 64
_TTL_SECONDS = 100


def _report(*, degraded: bool = False) -> SkillGapReport:
    """A schema-valid Skill_Gap_Report — the shape a real caller caches."""
    return SkillGapReport(
        gaps=[SkillGapEntry(skill="terraform", classification="missing", rank=1)],
        degraded=degraded,
    )


async def _get(cache: AgentCache, *, user_id: str = _USER_ID) -> SkillGapReport | None:
    return await cache.get(
        user_id=user_id,
        agent_name=_AGENT_NAME,
        prompt_version=_PROMPT_VERSION,
        input_hash=_INPUT_HASH,
        output_type=SkillGapReport,
    )


async def _set(cache: AgentCache, output: SkillGapReport, *, user_id: str = _USER_ID) -> None:
    await cache.set(
        user_id=user_id,
        agent_name=_AGENT_NAME,
        prompt_version=_PROMPT_VERSION,
        input_hash=_INPUT_HASH,
        output=output,
    )


# ---------------------------------------------------------------------------
# Fake Redis clients (injected; ``Redis`` is the core/redis.py re-export
# used purely as a typing cast target — the tests never import redis).
# ---------------------------------------------------------------------------


class _TickingFakeRedis:
    """In-memory fake honoring ``SET ... EX`` expiry against a manual clock."""

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
    """Fake whose reads always fail — the Requirement 9.10 read edge."""

    def __init__(self) -> None:
        self.set_calls = 0

    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis unreachable during lookup")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls += 1


class _BrokenSetRedis:
    """Fake whose writes always fail — the Requirement 9.10 write edge."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise ConnectionError("redis unreachable during write")


def _cache(fake: object, *, ttl: int = _TTL_SECONDS) -> AgentCache:
    return AgentCache(cast(Redis, fake), ttl_seconds=ttl)


# ---------------------------------------------------------------------------
# Round-trip and TTL (Requirement 9.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_returns_equal_output_with_configured_ttl() -> None:
    fake = _TickingFakeRedis()
    cache = _cache(fake)
    original = _report()

    await _set(cache, original)
    hit = await _get(cache)

    assert hit == original
    assert fake.observed_expirations == [_TTL_SECONDS]


@pytest.mark.asyncio
async def test_expired_entry_reads_back_as_miss() -> None:
    fake = _TickingFakeRedis()
    cache = _cache(fake)
    await _set(cache, _report())

    fake.now = float(_TTL_SECONDS)  # TTL elapsed exactly
    assert await _get(cache) is None


@pytest.mark.asyncio
async def test_absent_key_is_a_miss() -> None:
    cache = _cache(_TickingFakeRedis())
    assert await _get(cache) is None


# ---------------------------------------------------------------------------
# User isolation (Requirement 9.7, security.md).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_users_never_see_each_others_entries() -> None:
    fake = _TickingFakeRedis()
    cache = _cache(fake)
    await _set(cache, _report(), user_id=_USER_ID)

    assert await _get(cache, user_id=_OTHER_USER_ID) is None
    assert await _get(cache, user_id=_USER_ID) is not None
    # The user id is a structural component of every stored key.
    assert all(f":{_USER_ID}:" in key for key in fake.store)


# ---------------------------------------------------------------------------
# Degraded exclusion (Requirement 9.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rejects_degraded_output_before_redis_io() -> None:
    fake = _TickingFakeRedis()
    cache = _cache(fake)

    with pytest.raises(ValueError, match="degraded"):
        await _set(cache, _report(degraded=True))
    assert fake.store == {}  # no write reached Redis


# ---------------------------------------------------------------------------
# Failure posture (Requirement 9.10).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_failure_is_a_miss_and_never_raises() -> None:
    cache = _cache(_BrokenGetRedis())
    assert await _get(cache) is None


@pytest.mark.asyncio
async def test_write_failure_is_swallowed() -> None:
    cache = _cache(_BrokenSetRedis())
    await _set(cache, _report())  # must not raise


@pytest.mark.asyncio
async def test_corrupt_entry_is_a_miss() -> None:
    fake = _TickingFakeRedis()
    cache = _cache(fake)
    key = AgentCache._key(
        user_id=_USER_ID,
        agent_name=_AGENT_NAME,
        prompt_version=_PROMPT_VERSION,
        input_hash=_INPUT_HASH,
    )
    fake.store[key] = ("{not valid json", None)

    assert await _get(cache) is None


# ---------------------------------------------------------------------------
# Canonical input hash.
# ---------------------------------------------------------------------------


def test_input_hash_is_deterministic_and_input_sensitive() -> None:
    base = compute_agent_input_hash(
        agent_name=_AGENT_NAME,
        prompt_version=_PROMPT_VERSION,
        redacted_input="redacted resume [EMAIL_1]",
    )
    assert base == compute_agent_input_hash(
        agent_name=_AGENT_NAME,
        prompt_version=_PROMPT_VERSION,
        redacted_input="redacted resume [EMAIL_1]",
    )
    assert base != compute_agent_input_hash(
        agent_name=_AGENT_NAME,
        prompt_version=_PROMPT_VERSION,
        redacted_input="different input",
    )
    assert base != compute_agent_input_hash(
        agent_name="other_agent",
        prompt_version=_PROMPT_VERSION,
        redacted_input="redacted resume [EMAIL_1]",
    )
    assert base != compute_agent_input_hash(
        agent_name=_AGENT_NAME,
        prompt_version="gap_rules.v2",
        redacted_input="redacted resume [EMAIL_1]",
    )
