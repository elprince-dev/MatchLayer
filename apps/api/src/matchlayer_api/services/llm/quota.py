"""DailyQuota — per-user, per-UTC-day LLM call quota on Redis.

Fixed-window counter keyed ``llm:quota:{user_id}:{YYYYMMDD}`` (UTC
date, Requirement 13.1). The key's expiry is set to 48 hours after the
first write, so keys are self-cleaning and day rollover is automatic —
a new UTC day means a new key with a fresh count (Requirement 13.6).

Two operations, both returning a :class:`QuotaDecision` whose
``remaining`` feeds the ``X-LLM-Quota-Remaining`` response header
included on every LLM feature response, 429s included (Requirement
13.5):

- :meth:`DailyQuota.gate` — read-only ``GET``; count at or above the
  limit → rejection (429 upstream, Requirement 13.2). Never counts.
- :meth:`DailyQuota.reserve` — atomic Lua INCR-if-below-limit
  (Requirement 13.7), called only at the moment a provider call is
  initiated (Requirement 13.3). A reserved count stays counted even if
  the provider call subsequently fails.

Any Redis failure raises :exc:`QuotaAccountingError` so the caller
takes the fallback path without initiating a provider call — an
accounting failure can never cause unbounded spend (Requirement 13.8).

The ``redis`` import boundary lives in ``core/redis.py`` (the single
module allowed to import ``redis`` — design decision D6, enforced by
``tests/unit/test_import_boundaries.py``). This module receives an
*injected* client and annotates it via the ``core/redis.py``
re-exports. The clock is injected too, so the UTC-day derivation is
testable across day-rollover boundaries (design Testing Strategy).

Design reference: phase-3-llm-layer §"DailyQuota (services/llm/quota.py)".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends

from matchlayer_api.config import get_settings
from matchlayer_api.core.redis import AsyncScript, Redis, get_redis_client

_log = structlog.get_logger(__name__)

# 48 hours: long enough that a key created any time during its UTC day
# outlives that day's final read, short enough that stale keys clean
# themselves up without a sweeper (Requirement 13.6).
_KEY_EXPIRY_SECONDS = 48 * 60 * 60

# Lua script: atomic INCR-if-below-limit (Requirement 13.7). Reads the
# counter, refuses when it has reached the limit, otherwise increments
# and — only on the first write for the key — sets the 48h expiry.
# Returns {granted, remaining}.
_RESERVE_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local expiry = tonumber(ARGV[2])

local count = tonumber(redis.call('GET', key) or '0')
if count >= limit then
  return {0, 0}
end
local new_count = redis.call('INCR', key)
if new_count == 1 then
  redis.call('EXPIRE', key, expiry)
end
local remaining = limit - new_count
if remaining < 0 then remaining = 0 end
return {1, remaining}
"""


class QuotaAccountingError(Exception):
    """Raised when the quota counter cannot be read or updated.

    Signals the caller to serve the request via the Fallback_Response
    path with no provider call (Requirement 13.8). Carries no user
    content and no Redis payload — the message is a fixed, safe string.
    """


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """Result of a quota gate or reserve.

    ``allowed`` is whether the request may proceed toward a provider
    call; ``remaining`` is the requester's remaining quota for the
    current UTC day, surfaced as the ``X-LLM-Quota-Remaining`` header
    on every LLM feature response including 429s (Requirement 13.5).
    """

    allowed: bool
    remaining: int


def _utc_now() -> datetime:
    """Default injected clock: timezone-aware current UTC time."""
    return datetime.now(UTC)


class DailyQuota:
    """Per-user fixed-window daily quota on Redis.

    Constructed with an injected redis client (design decision D6), the
    configured daily limit (``llm_daily_quota``), and an injectable
    clock returning a timezone-aware ``datetime`` — production uses
    real UTC time, tests inject a fixed or stepping clock to exercise
    UTC day-rollover behavior. The public :func:`get_daily_quota`
    factory below wires the production instance through ``Depends``.
    """

    def __init__(
        self,
        redis_client: Redis,
        *,
        limit: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._redis = redis_client
        self._limit = limit
        self._clock = clock
        self._reserve_script: AsyncScript | None = None

    def _key(self, user_id: str) -> str:
        """Quota key for ``user_id`` on the current UTC day."""
        today = self._clock().astimezone(UTC).strftime("%Y%m%d")
        return f"llm:quota:{user_id}:{today}"

    async def gate(self, user_id: str) -> QuotaDecision:
        """Read-only quota check — never increments the counter.

        Run as the first enforcement step after authentication and
        ownership verification (Requirement 13.2). A count at or above
        the limit yields ``allowed=False`` (429 upstream); the gate
        itself never counts the request (Requirement 13.3).

        Raises :exc:`QuotaAccountingError` on any Redis failure
        (Requirement 13.8).
        """
        key = self._key(user_id)
        try:
            raw = await self._redis.get(key)
        except Exception as exc:
            _log.warning("llm_quota_redis_error", operation="gate", user_id=user_id)
            raise QuotaAccountingError("quota counter unavailable") from exc

        count = int(raw) if raw is not None else 0
        remaining = max(self._limit - count, 0)
        return QuotaDecision(allowed=count < self._limit, remaining=remaining)

    async def reserve(self, user_id: str) -> QuotaDecision:
        """Atomically count one provider call if quota remains.

        Called only at provider-call initiation (Requirement 13.3): a
        cache hit, a fallback without a provider call, and a 429
        rejection never reach this method. The Lua script makes the
        check-and-increment atomic, so concurrent requests from one
        user can never push the day's count past the limit (Requirement
        13.7). A granted reservation stays counted even if the provider
        call fails (Requirement 13.3).

        Raises :exc:`QuotaAccountingError` on any Redis failure — the
        caller must not initiate the provider call (Requirement 13.8).
        """
        key = self._key(user_id)
        try:
            if self._reserve_script is None:
                self._reserve_script = self._redis.register_script(_RESERVE_LUA)
            result = await self._reserve_script(
                keys=[key],
                args=[self._limit, _KEY_EXPIRY_SECONDS],
            )
        except Exception as exc:
            _log.warning("llm_quota_redis_error", operation="reserve", user_id=user_id)
            raise QuotaAccountingError("quota counter unavailable") from exc

        return QuotaDecision(allowed=bool(result[0]), remaining=int(result[1]))


# ---------------------------------------------------------------------------
# Per-request quota factory.
#
# Mirrors ``core/rate_limit.py``'s ``get_rate_limiter``: the client
# lifecycle (per-request construction, teardown, event-loop affinity)
# lives in ``core/redis.py``'s ``get_redis_client``. This factory only
# composes that dependency with the configured limit.
# ---------------------------------------------------------------------------


async def get_daily_quota(
    client: Annotated[Redis, Depends(get_redis_client)],
) -> DailyQuota:
    """Build a per-request :class:`DailyQuota` around the injected client.

    The limit comes from settings (``llm_daily_quota``, Requirement
    13.1); the client is created and closed by
    :func:`matchlayer_api.core.redis.get_redis_client`. Tests that
    drive :class:`DailyQuota` directly construct fake clients and
    clocks and skip this factory entirely.
    """
    settings = get_settings()
    return DailyQuota(client, limit=settings.llm_daily_quota)
