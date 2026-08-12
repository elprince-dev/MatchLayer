"""Sliding-window rate limiter backed by Redis SORTED SETs.

The ``redis`` import boundary lives in ``core/redis.py`` (the single
module allowed to import ``redis`` — phase-3-llm-layer design decision
D6, enforced by ``tests/unit/test_import_boundaries.py``). This module
receives an *injected* client and annotates it via the ``core/redis.py``
re-exports.

Design reference: Rate Limiting §10.1-§10.4.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends

from matchlayer_api.core.redis import AsyncScript, Redis, get_redis_client

_log = structlog.get_logger(__name__)

# Lua script: atomic sliding-window check (§10.1).
_LUA_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_after_ms = (tonumber(oldest[2]) + window_ms) - now_ms
  if retry_after_ms < 0 then retry_after_ms = 0 end
  return {0, math.ceil(retry_after_ms / 1000)}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, 0}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of a rate-limit check.

    ``allowed`` and ``retry_after_seconds`` are the public contract from
    design §10.1. ``redis_unavailable`` is a defaulted, internal-only
    signal that lets the dependency layer distinguish a fail-closed
    Redis outage from a legitimate rejection (design §10.4) — both
    return ``allowed=False`` with the same ``retry_after_seconds=60``
    placeholder, so the boolean is the only reliable discriminator
    when the configured window itself is 60 seconds (e.g. the refresh
    endpoint, design §10.3).
    """

    allowed: bool
    retry_after_seconds: int
    redis_unavailable: bool = False


class RateLimiter:
    """Sliding-window rate limiter using Redis SORTED SETs + Lua.

    Constructed with an injected redis client. The public
    :func:`get_rate_limiter` factory below builds the limiter that
    the dependency layer wires through ``Depends`` —
    :class:`RateLimiter` itself does not own the client lifecycle,
    so tests can inject fakes and production can swap clients
    without coupling either path to a specific connection-pool
    strategy.
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._script: AsyncScript | None = None

    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """Check and record a request against the sliding window.

        On any Redis error, returns fail-closed (§10.4).
        """
        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        member = f"{now_ms}:{secrets.token_hex(4)}"

        try:
            if self._script is None:
                self._script = self._redis.register_script(_LUA_SCRIPT)
            result = await self._script(
                keys=[key],
                args=[now_ms, window_ms, limit, member],
            )
            allowed = bool(result[0])
            retry_after = int(result[1])
            return RateLimitDecision(allowed=allowed, retry_after_seconds=retry_after)
        except Exception:
            _log.warning("rate_limiter_redis_error", key=key)
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=60,
                redis_unavailable=True,
            )


# ---------------------------------------------------------------------------
# Per-request limiter factory.
#
# The client lifecycle (per-request construction, teardown, and the
# event-loop-affinity rationale) lives in ``core/redis.py``'s
# ``get_redis_client`` — see the commentary there. This factory only
# composes that dependency: FastAPI resolves ``get_redis_client`` once
# per request, hands the loop-bound client here, and runs the
# generator's teardown when the request finishes.
# ---------------------------------------------------------------------------


async def get_rate_limiter(
    client: Annotated[Redis, Depends(get_redis_client)],
) -> RateLimiter:
    """Build a per-request :class:`RateLimiter` around the injected client.

    The client is created and closed by
    :func:`matchlayer_api.core.redis.get_redis_client`; this factory
    never owns the connection lifecycle (design decision D6). Tests
    that drive :class:`RateLimiter` directly construct fake clients
    and override this dependency in ``app.dependency_overrides`` —
    they skip both factories entirely.
    """
    return RateLimiter(client)
