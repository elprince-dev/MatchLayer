"""PBT-3: Sliding-window rate limiter accounts monotonically.

Validates: Requirements 10.1, 10.7, 10.8.

Adapter note (task 16.5 / follow-up to 16.1)
--------------------------------------------
After task 16.1 reshaped ``get_rate_limiter()`` from a synchronous
factory returning a process-wide :class:`RateLimiter` into an
``async def`` generator that yields a per-request limiter and closes
its underlying client on teardown, this property test could no longer
call the factory directly. The previous shape was

    rl = get_rate_limiter()                       # ← synchronous
    decision = await rl.check(...)

which under the new shape returns a coroutine-iterator and never
constructs a :class:`RateLimiter`.

Adapter chosen here: construct the :class:`RateLimiter` directly
inside the same async helper that drives ``check()``, against a
fresh ``redis.asyncio.Redis`` client the test allocates per scenario.
This keeps the property assertion identical to the design contract
(the limiter writes its own Redis keys, no fakes) while staying
clear of FastAPI's request-scoped lifecycle management — which is
not under test here.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import Awaitable, Callable

import pytest
import redis.asyncio as aioredis
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.config import get_settings
from matchlayer_api.core.rate_limit import RateLimiter


def _redis_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=1):
            pass
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available (docker-compose not running)",
)


_REDIS_URL = str(get_settings().redis_url)


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Run an async test body via :class:`asyncio.Runner`.

    Replaces ``asyncio.run(_run())`` with the
    ``with asyncio.Runner() as r: r.run(...)`` shape recommended for
    repeated invocations: ``Runner`` closes the event loop and its
    selectors deterministically on ``__exit__`` so the
    ``ResourceWarning("unclosed event loop")`` that bare ``asyncio.run``
    can leak in teardown is impossible. Hypothesis drives the
    sliding-window property across many examples per run, so the
    deterministic close is also measurably faster than re-creating
    the loop scaffolding via ``asyncio.run`` per example.
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


async def _build_limiter() -> tuple[RateLimiter, aioredis.Redis]:
    """Construct a :class:`RateLimiter` against a fresh Redis client.

    Returns the (limiter, client) pair so the caller can ``aclose()``
    the client deterministically — letting it fall through to GC
    triggers a ``ResourceWarning`` that pytest's ``filterwarnings =
    ["error"]`` config promotes to a teardown failure.
    """
    client = aioredis.from_url(_REDIS_URL, decode_responses=False)  # type: ignore[no-untyped-call]
    return RateLimiter(client), client


@settings(deadline=None, max_examples=10)
@given(
    limit=st.integers(min_value=3, max_value=10),
    window_seconds=st.integers(min_value=1, max_value=5),
)
def test_sliding_window_respects_limit(limit: int, window_seconds: int) -> None:
    """At every step, allowed count within the window is at most limit."""
    # Use a unique key per test run to avoid interference.
    key = f"pbt3:{uuid.uuid4()}"

    async def _run() -> None:
        rl, client = await _build_limiter()
        try:
            allowed_count = 0
            for _ in range(limit + 5):
                decision = await rl.check(key, limit=limit, window_seconds=window_seconds)
                if decision.allowed:
                    allowed_count += 1
                    assert decision.retry_after_seconds == 0
                else:
                    assert decision.retry_after_seconds > 0

            # The number of allowed requests should be exactly `limit`.
            assert allowed_count == limit
        finally:
            await client.aclose(close_connection_pool=True)
            await client.connection_pool.disconnect()

    _run_sync(_run)


def test_window_expiry_allows_new_requests() -> None:
    """After the window expires, new requests are allowed again."""
    key = f"pbt3_expiry:{uuid.uuid4()}"

    async def _run() -> None:
        rl, client = await _build_limiter()
        try:
            # Fill the window (limit=3, window=1s).
            for _ in range(3):
                decision = await rl.check(key, limit=3, window_seconds=1)
                assert decision.allowed

            # Next should be rejected.
            decision = await rl.check(key, limit=3, window_seconds=1)
            assert not decision.allowed

            # Wait for window to expire.
            await asyncio.sleep(1.1)

            # Should be allowed again.
            decision = await rl.check(key, limit=3, window_seconds=1)
            assert decision.allowed
        finally:
            await client.aclose(close_connection_pool=True)
            await client.connection_pool.disconnect()

    _run_sync(_run)
