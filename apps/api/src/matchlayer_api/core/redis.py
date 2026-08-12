"""Redis client ownership and the per-request async client factory.

This is the ONLY module in the API that imports ``redis``.
Import-boundary enforced by ``tests/unit/test_import_boundaries.py``.

Every consumer (:class:`~matchlayer_api.core.rate_limit.RateLimiter`,
the idempotency store, and the Phase 3 DailyQuota / LLMCache) receives
an *injected* client — either through the :func:`get_redis_client`
FastAPI dependency below or via direct construction in tests — and
annotates it with the :data:`Redis` / :data:`AsyncScript` re-exports so
no other module ever imports ``redis`` itself.

Design reference: phase-3-llm-layer design decision D6.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
import structlog
from redis.commands.core import AsyncScript

from matchlayer_api.config import get_settings

_log = structlog.get_logger(__name__)

# Re-exports so injected-client consumers can annotate their parameters
# without importing ``redis`` (which would breach the import boundary).
Redis = aioredis.Redis

__all__ = ["AsyncScript", "Redis", "get_redis_client"]


# ---------------------------------------------------------------------------
# Per-request client factory.
#
# ``redis.asyncio.Redis`` builds a connection pool whose ``Future``
# objects bind to the running event loop on first ``await``. A
# module-scope singleton client therefore "captures" the first loop it
# sees, and any later use from a different loop raises
# ``RuntimeError: ... attached to a different loop`` — which consumers'
# defensive ``except Exception`` clauses convert into their fail-closed
# / fail-soft paths.
#
# pytest-asyncio's default function-scoped event loop turns this into
# a silent failure mode: every test after the first sees the degraded
# path (e.g. 503 ``rate_limiter_unavailable``) instead of the real
# route response.
#
# The fix is to scope the client to the *request*. FastAPI's
# dependency-injection system supports async-generator dependencies —
# yielding the client, then closing it (and draining its connection
# pool) when the request finishes. Each request gets a client bound to
# the loop that served the request, never reused on another. Production
# opens at most one TCP connection per request (redis-py pools lazily —
# no command means no connection); consumers sharing this dependency
# within one request share one client via FastAPI's per-request
# dependency cache.
# ---------------------------------------------------------------------------


async def get_redis_client() -> AsyncIterator[aioredis.Redis]:
    """Yield a per-request async Redis client; close it on teardown.

    Built per request so the underlying ``redis.asyncio.Redis``
    client's asyncio resources never outlive the event loop that
    allocates them. The FastAPI dependency layer resolves this once
    per request, drains the pool's connections back through redis-py
    on teardown, and never shares the client across requests (or
    across the function-scoped event loops pytest-asyncio creates per
    test).

    Tests that drive the consumers directly construct fake clients and
    override the consumer dependencies in ``app.dependency_overrides``
    — they skip this factory entirely and so do not need to engage
    with the close path.
    """
    settings = get_settings()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=False)  # type: ignore[no-untyped-call]
    try:
        yield client
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
            _log.warning("redis_client_close_failed")
