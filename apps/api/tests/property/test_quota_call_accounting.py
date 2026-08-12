"""Feature: phase-3-llm-layer — Property 14.

Property 14: Quota accounting counts exactly the initiated provider calls.

    *For any* generated sequence of feature requests for a set of users —
    mixing cache hits, persisted-result reuse, fresh calls that succeed,
    fresh calls that fail after initiation, gate rejections, and requests
    crossing a UTC-midnight clock boundary — each user's daily counter
    equals exactly the number of provider calls initiated for that user in
    that UTC day; counters are independent across users; requests beyond
    the limit receive 429; under concurrent reserves the number granted
    never exceeds the configured quota; and counted requests remain counted
    when their call fails.

**Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.6, 13.7**

Two properties drive ``services/llm/quota.DailyQuota`` directly, mapping
the orchestrator pipeline onto the quota surface it actually touches:

* **Sequential accounting** — a generated event sequence interleaves
  read-only ``gate()`` calls (the pipeline's cache hits, persisted-result
  reuse, and 429 gate rejections never go past the gate) with ``reserve()``
  calls (the single accounting point at provider-call initiation — one
  variant models the provider call succeeding, the other models it failing
  *after* initiation, and both must count identically, Requirement 13.3).
  An injected stepping clock advances across UTC-midnight boundaries so
  keys roll to the new ``llm:quota:{user_id}:{YYYYMMDD}`` day and the full
  limit returns (Requirements 13.1, 13.6). A shadow model tracks the
  expected per-(user, day) count; every decision's ``allowed`` /
  ``remaining`` must match the model (an at-limit denial is the upstream
  429, Requirement 13.2), counters never cross user boundaries
  (Requirement 13.4), and the final Redis state holds exactly the modelled
  counts with the 48h expiry set exactly once per key.
* **Concurrent reserves** — ``asyncio.gather`` fires more reserves than
  the limit for one user; the atomic Lua INCR-if-below-limit (emulated by
  the fake with real script atomicity: no awaits inside one execution)
  must grant exactly ``limit`` of them and leave the counter at the limit,
  never beyond (Requirement 13.7).

The fake Redis follows the ``tests/unit/test_rate_limit.py`` convention
sanctioned by the design's Testing Strategy: an injected in-memory fake
whose ``register_script`` returns a Python emulation honoring the Lua
script's semantics (read, refuse at limit, INCR, EXPIRE only on first
write, return ``{granted, remaining}``).
"""

# Feature: phase-3-llm-layer, Property 14: Quota accounting counts exactly the initiated provider calls  # noqa: E501

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.core.redis import Redis
from matchlayer_api.services.llm.quota import DailyQuota

# ---------------------------------------------------------------------------
# Fake Redis honoring the reserve script's Lua semantics
# ---------------------------------------------------------------------------

_EXPECTED_EXPIRY_SECONDS = 48 * 60 * 60


class _FakeReserveScript:
    """Python emulation of the atomic INCR-if-below-limit Lua script.

    Executes synchronously end-to-end (no awaits between the read and
    the write), mirroring the atomicity Redis guarantees a Lua script —
    the property the wrapper relies on for Requirement 13.7.
    """

    def __init__(self, store: dict[str, bytes], ttls: dict[str, list[int]]) -> None:
        self._store = store
        self._ttls = ttls

    async def __call__(self, *, keys: list[str], args: list[int]) -> list[int]:
        key = keys[0]
        limit = int(args[0])
        expiry = int(args[1])

        count = int(self._store.get(key, b"0"))
        if count >= limit:
            return [0, 0]
        new_count = count + 1
        self._store[key] = str(new_count).encode()
        if new_count == 1:
            self._ttls.setdefault(key, []).append(expiry)
        return [1, max(limit - new_count, 0)]


class _FakeAsyncRedis:
    """Minimal stand-in for ``redis.asyncio.Redis``.

    Exposes only ``get`` (the gate's read) and ``register_script`` (the
    reserve script) — the two methods :class:`DailyQuota` touches.
    ``ttls`` records every EXPIRE the script issues per key, so the test
    can assert the 48h expiry is set exactly once, on the first write.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, list[int]] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def register_script(self, _source: str) -> _FakeReserveScript:
        return _FakeReserveScript(self.store, self.ttls)


class _SteppingClock:
    """Injected clock: starts at a fixed UTC instant, advances on demand."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance_hours(self, hours: int) -> None:
        self.now += timedelta(hours=hours)


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Pipeline events, projected onto the quota surface:
#   gate            — read-only check; covers cache hits, persisted-result
#                     reuse, and gate rejections (none of which count).
#   reserve_ok      — provider call initiated, call later succeeds.
#   reserve_fails   — provider call initiated, call later fails; the
#                     reservation must remain counted (Requirement 13.3).
_EVENT_KIND = st.sampled_from(["gate", "reserve_ok", "reserve_fails"])

# Clock advance before an event, in hours. Mostly zero (same-instant
# bursts) with occasional jumps so sequences cross UTC midnight; the
# scenario starts at 18:00 UTC, so a handful of jumps reaches new days.
_ADVANCE_HOURS = st.sampled_from([0, 0, 0, 0, 2, 7])

_START = datetime(2025, 6, 1, 18, 0, 0, tzinfo=UTC)


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[int, int, list[tuple[int, str, int]]]:
    """Build (limit, user count, events) with events as (user, kind, advance)."""
    limit = draw(st.integers(min_value=1, max_value=4))
    n_users = draw(st.integers(min_value=1, max_value=3))
    events = draw(
        st.lists(
            st.tuples(st.integers(min_value=0, max_value=n_users - 1), _EVENT_KIND, _ADVANCE_HOURS),
            min_size=1,
            max_size=30,
        )
    )
    return limit, n_users, events


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_daily_counter_equals_initiated_provider_calls(
    scenario: tuple[int, int, list[tuple[int, str, int]]],
) -> None:
    """Counters count exactly the granted reserves — per user, per UTC day.

    Gate-only events (cache hits, reuse, rejections) never move a
    counter (Requirement 13.3); granted reserves each count exactly once
    and stay counted when the modelled provider call fails; denials at
    the limit are the upstream 429 with ``remaining == 0`` (Requirement
    13.2); day rollover restores the full limit under a fresh key
    (Requirements 13.1, 13.6); and no user's events ever touch another
    user's counter (Requirement 13.4).
    """
    limit, n_users, events = scenario
    users = [f"user-{i}" for i in range(n_users)]

    async def _run() -> None:
        fake = _FakeAsyncRedis()
        clock = _SteppingClock(_START)
        quota = DailyQuota(cast(Redis, fake), limit=limit, clock=clock)

        # Shadow model: (user_id, YYYYMMDD) -> initiated provider calls.
        expected: dict[tuple[str, str], int] = defaultdict(int)

        for user_idx, kind, advance in events:
            clock.advance_hours(advance)
            day = clock.now.strftime("%Y%m%d")
            user = users[user_idx]
            count = expected[(user, day)]

            if kind == "gate":
                decision = await quota.gate(user)
                assert decision.allowed == (count < limit)
                assert decision.remaining == max(limit - count, 0)
                # Read-only: the model (and, asserted below, the store)
                # is unchanged by any number of gate checks.
            else:
                decision = await quota.reserve(user)
                if count < limit:
                    # Provider call initiated: counted at this moment —
                    # and it stays counted even when kind == "reserve_fails"
                    # (no decrement path exists on the API at all).
                    assert decision.allowed
                    expected[(user, day)] = count + 1
                    assert decision.remaining == limit - (count + 1)
                else:
                    # Beyond the limit: denied — the upstream 429.
                    assert not decision.allowed
                    assert decision.remaining == 0

        # Final Redis state: exactly the modelled counts, nothing else.
        expected_store = {
            f"llm:quota:{user}:{day}": str(count).encode()
            for (user, day), count in expected.items()
            if count > 0
        }
        assert fake.store == expected_store

        # 48h expiry set exactly once per key — on its first write only.
        assert set(fake.ttls) == set(expected_store)
        for expiries in fake.ttls.values():
            assert expiries == [_EXPECTED_EXPIRY_SECONDS]

    _run_sync(_run)


@settings(max_examples=100, deadline=None)
@given(
    limit=st.integers(min_value=1, max_value=5),
    extra=st.integers(min_value=0, max_value=8),
)
def test_concurrent_reserves_never_exceed_quota(limit: int, extra: int) -> None:
    """Atomicity (Requirement 13.7): concurrent reserves grant at most
    the configured quota — exactly ``limit`` grants out of ``limit +
    extra`` simultaneous attempts, every excess attempt denied with zero
    remaining, and the day's counter resting exactly at the limit."""

    async def _run() -> None:
        fake = _FakeAsyncRedis()
        clock = _SteppingClock(_START)
        quota = DailyQuota(cast(Redis, fake), limit=limit, clock=clock)
        user = "concurrent-user"

        decisions = await asyncio.gather(*(quota.reserve(user) for _ in range(limit + extra)))

        granted = [d for d in decisions if d.allowed]
        denied = [d for d in decisions if not d.allowed]
        assert len(granted) == limit
        assert len(denied) == extra
        assert all(d.remaining == 0 for d in denied)
        # Each grant reports a distinct, correct remaining count.
        assert sorted(d.remaining for d in granted) == list(range(limit))

        key = f"llm:quota:{user}:{_START.strftime('%Y%m%d')}"
        assert fake.store == {key: str(limit).encode()}

    _run_sync(_run)
