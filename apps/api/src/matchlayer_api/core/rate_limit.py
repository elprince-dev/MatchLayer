"""Sliding-window rate limiter backed by Redis SORTED SETs.

This is the ONLY module in the API that imports ``redis``.
Import-boundary enforced by ``tests/unit/test_import_boundaries.py``.

Design reference: Rate Limiting §10.1-§10.4.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import redis.asyncio as aioredis
import structlog
from redis.commands.core import AsyncScript

from matchlayer_api.config import get_settings

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

    def __init__(self, redis_client: aioredis.Redis) -> None:
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
# ``redis.asyncio.Redis`` builds a connection pool whose ``Future``
# objects bind to the running event loop on first ``await``. A
# module-scope singleton client therefore "captures" the first loop it
# sees, and any later use from a different loop raises
# ``RuntimeError: ... attached to a different loop`` — which the
# wrapper's ``except Exception`` clause converts into the fail-closed
# ``redis_unavailable=True`` decision.
#
# pytest-asyncio's default function-scoped event loop turns this into
# a silent failure mode: every test after the first sees 503
# ``rate_limiter_unavailable`` instead of the real route response.
#
# The fix is to scope the client to the *request*. FastAPI's
# dependency-injection system supports async-generator dependencies —
# yielding the limiter, then closing the underlying client (and
# draining its connection pool) when the request finishes. Each
# request gets a client bound to the loop that served the request,
# never reused on another. Production opens at most one TCP
# connection per request (redis-py pools lazily — no command means no
# connection); the EVALSHA fast path still hits Redis's server-side
# script cache because the SHA is computed client-side from
# ``_LUA_SCRIPT`` and is identical across :class:`RateLimiter`
# instances.
# ---------------------------------------------------------------------------


async def get_rate_limiter() -> AsyncIterator[RateLimiter]:
    """Yield a per-request :class:`RateLimiter`; close it on teardown.

    Built per request so the underlying ``redis.asyncio.Redis``
    client's asyncio resources never outlive the event loop that
    allocates them. The FastAPI dependency layer resolves this once
    per request, drains the pool's connections back through redis-py
    on teardown, and never shares the client across requests (or
    across the function-scoped event loops pytest-asyncio creates per
    test).

    Tests that drive :class:`RateLimiter` directly construct fake
    clients and override this dependency in
    ``app.dependency_overrides`` — they skip this factory entirely
    and so do not need to engage with the close path.
    """
    settings = get_settings()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=False)  # type: ignore[no-untyped-call]
    try:
        yield RateLimiter(client)
    finally:
        # ``aclose()`` releases the client's state; an explicit
        # ``connection_pool.disconnect()`` then drains any idle
        # ``Connection`` objects whose ``StreamReader``/``StreamWriter``
        # are bound to the loop. Without the disconnect, redis-py
        # delays the close to ``__del__``, which on a closed loop
        # raises a ``ResourceWarning`` — and the API test suite's
        # ``filterwarnings = ["error"]`` config escalates that into
        # a teardown failure.
        try:
            await client.aclose(close_connection_pool=True)
            await client.connection_pool.disconnect()
        except Exception:  # pragma: no cover - defensive
            _log.warning("rate_limiter_client_close_failed")
