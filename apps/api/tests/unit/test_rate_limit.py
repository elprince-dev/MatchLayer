"""Unit tests for ``core/rate_limit.py``.

Covers the two contracts task 8.4 of phase-1-auth pins down:

* **Sliding-window correctness.** A sequence of ten hits at
  ``t, t+1ms, ..., t+9ms`` against ``limit=10, window=1s`` all succeed;
  the eleventh rejects with ``retry_after_seconds > 0``; at
  ``t+1100ms`` the next hit succeeds again. Time is controlled with
  :mod:`freezegun` so ``time.time()`` (the wrapper's clock) returns
  deterministic millisecond values across the whole sequence.

* **Fail-closed on Redis outage.** When the injected Redis client
  raises during the script execution, the wrapper returns
  :class:`RateLimitDecision(allowed=False, retry_after_seconds=60,
  redis_unavailable=True)` (Rate Limiting §10.4). The companion
  router-level assertion wires a minimal FastAPI app around the
  ``rate_limit(...)`` dependency and the
  :class:`RateLimiterUnavailableError` exception, then asserts the
  503 ``rate_limiter_unavailable`` envelope shape Requirement 10.9
  prescribes.

The tests do not stand up a real Redis: the Lua script's
``EVAL``-time semantics are exercised by PBT-3 (task 8.18) against
the docker-compose Redis. Here we drive the wrapper directly with a
fake client whose ``register_script`` returns a Python emulation of
the Lua algorithm — this isolates the wrapper's responsibilities
(timestamp generation, decision-dataclass construction, exception
handling) from the Lua-runtime concerns the property test owns.

References:
* Requirements 10.1, 10.7, 10.9 (sliding window, audit emission,
  fail-closed envelope).
* Design §10.1 (algorithm + Lua-script shape), §10.4 (fail-closed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from freezegun import freeze_time
from httpx import ASGITransport, AsyncClient

from matchlayer_api.core.dependencies import (
    RateLimited,
    RateLimiterUnavailableError,
    rate_limit,
)
from matchlayer_api.core.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    get_rate_limiter,
)

# ---------------------------------------------------------------------------
# Fake Redis client that emulates the subset of behaviour the Lua script
# from ``core/rate_limit.py`` performs.
# ---------------------------------------------------------------------------


class _FakeAsyncScript:
    """Stand-in for :class:`redis.commands.core.AsyncScript`.

    The real ``AsyncScript`` is what ``register_script`` returns; the
    rate-limiter wrapper invokes it as ``await self._script(keys=...,
    args=...)``. This fake mirrors that calling shape and runs a
    Python re-implementation of the sliding-window algorithm spelled
    out in Design §10.1, so the wrapper's behaviour (clock, member
    generation, decision shape) is exercised without standing up a
    Redis server or shipping the Lua interpreter into the test.
    """

    def __init__(self, store: dict[str, list[tuple[int, str]]]) -> None:
        self._store = store

    async def __call__(self, keys: list[str], args: list[Any]) -> list[int]:
        key = keys[0]
        now_ms = int(args[0])
        window_ms = int(args[1])
        limit = int(args[2])
        member = str(args[3])

        # ZREMRANGEBYSCORE key '-inf' (now_ms - window_ms) removes
        # every entry whose score is at most ``now_ms - window_ms``,
        # leaving only entries scored *strictly greater* than the
        # cutoff (i.e., within the trailing window).
        cutoff = now_ms - window_ms
        survivors = [(s, m) for (s, m) in self._store.get(key, []) if s > cutoff]

        if len(survivors) >= limit:
            # Compute the retry-after by reading the oldest survivor's
            # score and projecting the moment it falls out of the
            # window (Design §10.1 ``retry_after_ms`` formula).
            oldest_score = min(score for score, _ in survivors)
            retry_after_ms = max(0, (oldest_score + window_ms) - now_ms)
            # ``ceil`` of milliseconds → seconds, matching the Lua
            # ``math.ceil(retry_after_ms / 1000)``.
            retry_after_seconds = -(-retry_after_ms // 1000) if retry_after_ms > 0 else 0
            self._store[key] = survivors
            return [0, retry_after_seconds]

        survivors.append((now_ms, member))
        self._store[key] = survivors
        return [1, 0]


class _RaisingAsyncScript:
    """Fake script whose call always raises a Redis-style exception.

    Drives the fail-closed branch of :meth:`RateLimiter.check` without
    requiring a ``register_script`` failure at registration time —
    the wrapper caches the script after the first call so failure on
    every invocation is a stronger contract.
    """

    async def __call__(self, keys: list[str], args: list[Any]) -> list[int]:
        # ``ConnectionError`` (the redis-py one) inherits from
        # ``Exception``; the wrapper's ``except Exception`` clause
        # in :class:`RateLimiter.check` catches anything that bubbles.
        raise ConnectionError("redis is down")


class _FakeAsyncRedis:
    """Minimal stand-in for :class:`redis.asyncio.Redis`.

    Exposes only :meth:`register_script` because that's the single
    method the rate-limiter touches. ``store`` is a per-instance
    dict so two test cases sharing a fake never see each other's
    state.
    """

    def __init__(self) -> None:
        self.store: dict[str, list[tuple[int, str]]] = {}

    def register_script(self, _source: str) -> _FakeAsyncScript:
        return _FakeAsyncScript(self.store)


class _BrokenAsyncRedis:
    """Fake Redis whose registered scripts always raise on call.

    Used to drive the fail-closed branch of
    :meth:`RateLimiter.check` (Design §10.4 / Requirement 10.9).
    """

    def register_script(self, _source: str) -> _RaisingAsyncScript:
        return _RaisingAsyncScript()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# The rate-limiter wrapper builds its key off whatever the caller
# passes; the value is opaque to the algorithm tested here.
_TEST_KEY = "rl:auth:test:ip:127.0.0.1"

# Frozen origin used by every sliding-window case; chosen so
# ``int(time.time() * 1000)`` lands on a round millisecond and the
# arithmetic in the assertions reads like Design §10.1 verbatim.
_T0_ISO = "2024-01-01T00:00:00Z"


def _t0_dt() -> datetime:
    """Return ``_T0_ISO`` as a timezone-aware :class:`datetime`.

    Rendered separately so the freezegun call site stays a single
    expression and the conversion error (if the constant ever drifts)
    surfaces here rather than mid-test.
    """
    return datetime.fromisoformat(_T0_ISO.replace("Z", "+00:00")).astimezone(UTC)


# ---------------------------------------------------------------------------
# Sliding-window correctness
# ---------------------------------------------------------------------------


async def test_sliding_window_allows_ten_hits_then_rejects_eleventh() -> None:
    """Ten hits in the first 9 ms succeed; the eleventh rejects.

    Walks the sequence the task description pins:

    * ten hits at ``t``, ``t+1ms``, ..., ``t+9ms`` against
      ``limit=10, window=1s`` all succeed (every decision carries
      ``allowed=True`` and ``retry_after_seconds == 0``);
    * an eleventh hit (taken after the ten land) rejects with
      ``allowed=False`` and ``retry_after_seconds > 0`` — the oldest
      surviving entry sits at ``t``, so it falls out of the window
      ``1000 ms`` after ``t``, which rounds up to ``1`` second of
      retry-after at any sample point inside the window;
    * at ``t+1100ms`` the oldest entry has aged out of the window and
      a fresh hit succeeds again.

    Validates: Requirement 10.1 (sliding-window decision shape) and
    Requirement 10.7 (the rejecting ``Retry-After`` value the router
    surfaces is the same integer the wrapper returns).
    """
    fake_redis = _FakeAsyncRedis()
    limiter = RateLimiter(fake_redis)  # type: ignore[arg-type]

    with freeze_time(_t0_dt()) as frozen:
        # Ten back-to-back allowed hits at 1-ms increments.
        for i in range(10):
            decision = await limiter.check(_TEST_KEY, limit=10, window_seconds=1)
            assert decision.allowed is True, f"hit #{i + 1} should be allowed"
            assert decision.retry_after_seconds == 0
            assert decision.redis_unavailable is False
            frozen.tick(delta=timedelta(milliseconds=1))

        # The eleventh hit (now at t+10ms) is over budget. The oldest
        # surviving entry was scored at ``t`` and falls out of the
        # window 1000 ms later — i.e., 990 ms from now — which
        # ``ceil`` rounds up to 1 second of retry-after.
        eleventh = await limiter.check(_TEST_KEY, limit=10, window_seconds=1)
        assert eleventh.allowed is False
        assert eleventh.retry_after_seconds > 0
        assert eleventh.redis_unavailable is False, (
            "rejection driven by the limit (not a Redis outage) must not set redis_unavailable"
        )

        # Advance to t+1100 ms. The oldest entry was at t (score 0);
        # the cutoff at this moment is 1100 - 1000 = 100, so every
        # entry with score in [0, 9] is purged and the window is
        # empty again.
        frozen.move_to(_t0_dt() + timedelta(milliseconds=1100))
        post_window = await limiter.check(_TEST_KEY, limit=10, window_seconds=1)
        assert post_window.allowed is True
        assert post_window.retry_after_seconds == 0
        assert post_window.redis_unavailable is False


async def test_redis_unavailable_field_is_false_for_limit_rejection() -> None:
    """The ``redis_unavailable`` discriminator stays ``False`` on a normal reject.

    Design §10.4 documents ``redis_unavailable`` as the only reliable
    way to distinguish a rate-limit rejection from a Redis-outage
    fail-closed when both surfaces share the ``retry_after_seconds=60``
    placeholder (the refresh endpoint's window happens to be 60 s, so
    the integer alone can't disambiguate). Pin the contract here so
    a future regression that conflates the two surfaces fails loudly.
    """
    fake_redis = _FakeAsyncRedis()
    limiter = RateLimiter(fake_redis)  # type: ignore[arg-type]

    with freeze_time(_t0_dt()):
        # One hit allowed, immediately followed by a hit that exceeds
        # ``limit=1``. The reject is by-policy, not by-outage.
        first = await limiter.check(_TEST_KEY, limit=1, window_seconds=60)
        assert first.allowed is True
        assert first.redis_unavailable is False

        rejected = await limiter.check(_TEST_KEY, limit=1, window_seconds=60)
        assert rejected.allowed is False
        assert rejected.redis_unavailable is False


# ---------------------------------------------------------------------------
# Fail-closed on Redis outage
# ---------------------------------------------------------------------------


async def test_check_returns_fail_closed_decision_when_redis_raises() -> None:
    """Wrapper-level fail-closed contract.

    When the injected Redis client raises during the Lua-script
    invocation, :meth:`RateLimiter.check` MUST return
    :class:`RateLimitDecision` with ``allowed=False``,
    ``retry_after_seconds=60``, and ``redis_unavailable=True`` — the
    decision shape Design §10.4 mandates so the dependency layer can
    map it to a 503 envelope rather than the 429 envelope the
    by-policy reject branch produces.

    Validates: Requirement 10.9 (fail-closed envelope) at the wrapper
    boundary.
    """
    broken = _BrokenAsyncRedis()
    limiter = RateLimiter(broken)  # type: ignore[arg-type]

    decision = await limiter.check(_TEST_KEY, limit=10, window_seconds=900)

    assert isinstance(decision, RateLimitDecision)
    assert decision.allowed is False
    assert decision.retry_after_seconds == 60
    assert decision.redis_unavailable is True


# ---------------------------------------------------------------------------
# Router 503 mapping
# ---------------------------------------------------------------------------


@pytest.fixture
def fail_closed_app() -> Iterator[FastAPI]:
    """Build a minimal FastAPI app that routes through ``rate_limit(...)``.

    Mirrors the wiring task 7.1 will land in ``auth/router.py`` —
    one route, one ``rate_limit(...)`` dependency, one handler for
    :class:`RateLimiterUnavailableError` that produces the
    ``rate_limiter_unavailable`` envelope. By keeping the app
    self-contained the test does not depend on task 7.4 having wired
    the handler into ``main.py`` yet.

    The dependency-override registers a :class:`RateLimiter` wrapping
    the always-raising fake redis so every request takes the
    fail-closed branch. The session dependency is also overridden
    with an inert stub: the fail-closed branch in
    :func:`rate_limit` short-circuits *before* the audit-row insert
    (Design §10.4 forbids the audit write on a Redis-outage path
    because the database may also be impaired), so the session is
    resolved by FastAPI but never used — a no-op stub keeps the
    test from reaching out to a real Postgres.
    """
    from unittest.mock import MagicMock

    from fastapi import Request
    from fastapi.responses import JSONResponse
    from sqlalchemy.ext.asyncio import AsyncSession

    from matchlayer_api.core.db import get_session

    app = FastAPI()

    broken_limiter = RateLimiter(_BrokenAsyncRedis())  # type: ignore[arg-type]

    def _override_rate_limiter() -> RateLimiter:
        return broken_limiter

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        # The fail-closed branch never touches the session, but
        # FastAPI still resolves the dependency. Yielding a MagicMock
        # spec'd to ``AsyncSession`` keeps any future regression that
        # *does* call into the session loud (attribute-error rather
        # than silent network call).
        yield MagicMock(spec=AsyncSession)

    app.dependency_overrides[get_rate_limiter] = _override_rate_limiter
    app.dependency_overrides[get_session] = _override_get_session

    # Reproduce the envelope the foundation ``errors.py`` will emit
    # once task 7.4 lands. The shape (``type`` + ``status`` +
    # ``detail`` + ``Retry-After`` header) is the contract the test
    # asserts against.
    @app.exception_handler(RateLimiterUnavailableError)
    async def _handle_unavailable(_request: Request, exc: Exception) -> JSONResponse:
        # ``isinstance`` narrowing keeps the handler mypy-clean even
        # though Starlette types ``exception_handler`` callbacks with
        # the base ``Exception``.
        if not isinstance(exc, RateLimiterUnavailableError):  # pragma: no cover - defensive
            raise exc
        return JSONResponse(
            status_code=503,
            content={
                "type": "rate_limiter_unavailable",
                "title": "Rate limiter unavailable",
                "detail": "The rate limiter is temporarily unavailable. Try again shortly.",
                "status": 503,
                "request_id": None,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.exception_handler(RateLimited)
    async def _handle_limited(_request: Request, exc: Exception) -> JSONResponse:
        # Defensive — we don't expect this path under the always-fail
        # fake, but registering the handler keeps any false positive
        # (a decision that comes back as a normal reject) from
        # surfacing as a generic 500 that masks the real failure.
        if not isinstance(exc, RateLimited):  # pragma: no cover - defensive
            raise exc
        return JSONResponse(
            status_code=429,
            content={
                "type": "rate_limited",
                "title": "Too Many Requests",
                "detail": "Rate limit exceeded.",
                "status": 429,
                "request_id": None,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.post(
        "/_test/limited",
        dependencies=[Depends(rate_limit(endpoint="login", by=("ip",)))],
    )
    async def _route() -> dict[str, str]:
        return {"status": "ok"}  # pragma: no cover - never reached on fail-closed

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def fail_closed_client(fail_closed_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Drive :func:`fail_closed_app` through :class:`httpx.ASGITransport`."""
    transport = ASGITransport(app=fail_closed_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_router_maps_redis_outage_to_503_rate_limiter_unavailable(
    fail_closed_client: AsyncClient,
) -> None:
    """End-to-end: a Redis outage surfaces as 503 ``rate_limiter_unavailable``.

    Drives a real request through the ``rate_limit(...)`` dependency
    against an always-failing fake redis and asserts:

    * status code 503 (not 429 — that's the by-policy reject envelope);
    * ``type`` field equals ``rate_limiter_unavailable``;
    * the ``Retry-After`` response header carries the wrapper's
      60-second placeholder, matching what the dependency layer
      received from :class:`RateLimitDecision`.

    Validates: Requirement 10.9, the contract that wires
    Design §10.4's fail-closed wrapper output to the ``Auth_Router``'s
    response shape.
    """
    response = await fail_closed_client.post("/_test/limited")

    assert response.status_code == 503
    body = response.json()
    assert body["type"] == "rate_limiter_unavailable"
    assert body["status"] == 503
    # The wrapper returned ``retry_after_seconds=60``; the dependency
    # is responsible for putting that value on the response header
    # (Design §10.5).
    assert response.headers["Retry-After"] == "60"


# ---------------------------------------------------------------------------
# Cross-check: the fake-Redis ``register_script`` is called only once
# (the wrapper memoizes the script object — Design §10.1 references the
# Lua script as registered at first call), so a regression that drops
# the cache and re-registers on every check is caught.
# ---------------------------------------------------------------------------


async def test_register_script_is_called_once_across_checks() -> None:
    """The wrapper memoizes the Lua script (Design §10.1).

    Re-registering on every ``check()`` would (a) defeat the
    ``EVALSHA``/``EVAL`` fast path Redis uses and (b) generate a
    Lua-cache stampede under load. The cache lives in
    :class:`RateLimiter` as ``self._script``; pin the behaviour here
    so a refactor that drops it surfaces in the unit suite rather
    than via a Redis-side metric.
    """
    call_count = 0

    class _CountingFake(_FakeAsyncRedis):
        def register_script(self, source: str) -> _FakeAsyncScript:
            nonlocal call_count
            call_count += 1
            return super().register_script(source)

    counting = _CountingFake()
    limiter = RateLimiter(counting)  # type: ignore[arg-type]

    with freeze_time(_t0_dt()) as frozen:
        for _ in range(3):
            await limiter.check(_TEST_KEY, limit=10, window_seconds=1)
            frozen.tick(delta=timedelta(milliseconds=1))

    assert call_count == 1
