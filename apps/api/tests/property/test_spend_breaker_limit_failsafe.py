"""Feature: phase-3-llm-layer — Property 15.

Property 15: Spend circuit breaker opens exactly at the limit and fails safe.

    *For any* generated set of invocation-log rows (costs spread across
    months, including failed-call costs and unavailable costs) and any
    positive limit, the breaker is open after evaluation if and only if the
    sum of in-current-UTC-month recorded costs reaches or exceeds the
    limit; a spend-read failure or an invocation-log persist failure forces
    the state open until a subsequent successful below-limit evaluation
    closes it; month rollover and a raised limit close it at the next
    evaluation; and every open↔closed transition emits exactly one
    structured transition event with direction, tracked spend, limit, and
    cause.

**Validates: Requirements 14.1, 14.2, 14.4, 14.5, 14.6, 14.7, 14.8**

What is driven, and how (no database)
-------------------------------------
The unit under test is the real
:class:`matchlayer_api.services.llm.spend.SpendCircuitBreaker` with its
injectable clock and limit provider (design Testing Strategy), composed
with the real
:func:`matchlayer_api.services.llm.invocation_log.current_month_spend`
query builder. Per this suite's conventions the storage layer is an
**in-memory fake ``AsyncSession``** holding ``(created_at, cost)`` rows;
its ``execute`` extracts the month-start bind parameter from the *actual
statement the production query built* and applies SQL ``SUM`` semantics
over the fake rows (``NULL`` costs contribute nothing; no priced rows →
``NULL``). The UTC month window is therefore computed by production code
from the injected clock — rows deposited in earlier months genuinely
leave the window when the clock crosses a month boundary
(Requirements 14.1, 14.4).

Three properties cover the clauses:

* **Scenario walk** — a generated op sequence interleaves row deposits
  (priced and cost-unavailable), successful evaluations, read-failure
  evaluations, persist-failure reports, limit changes (including pinning
  the limit exactly to the current spend, the boundary case), and clock
  advances that cross UTC month boundaries. A shadow model tracks the
  expected open/closed state and the full expected transition-event
  sequence; after the walk the captured ``llm_spend_breaker_transition``
  events must equal the model's exactly — one per flip, none for
  state-confirming updates (Requirement 14.6).
* **Exact boundary** — with the limit strictly above the month's spend
  the breaker stays closed; pinned exactly equal it opens
  (``spend >= limit`` with equality, Requirement 14.2); raised strictly
  above again it closes at the next evaluation with cause
  ``limit_raised`` (Requirement 14.5).
* **Fail-safe** — a spend-read failure and a persist failure each force
  the state open with cause ``tracking_failure`` and an *unknown*
  tracked spend (Requirements 14.7, 14.8); a subsequent successful
  below-limit evaluation closes it, and a month rollover close is
  attributed ``month_rollover`` (Requirement 14.4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import structlog
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from matchlayer_api.services.llm.spend import BreakerCause, SpendCircuitBreaker

# ---------------------------------------------------------------------------
# In-memory fake session — evaluates the real current_month_spend statement
# against fake (created_at, cost) rows with SQL SUM semantics.
# ---------------------------------------------------------------------------


def _extract_month_start(stmt: Select[Any]) -> datetime:
    """Pull the month-start bind value out of the production statement.

    ``current_month_spend`` builds
    ``select(sum(cost_usd)).where(created_at >= month_start)``; the fake
    honors the *actual* boundary the production code computed from the
    injected clock, so the month-window logic (Requirement 14.1) is
    genuinely exercised rather than re-derived in the test.
    """
    where = stmt.whereclause
    assert isinstance(where, BinaryExpression), "expected a single >= comparison"
    param = where.right
    assert isinstance(param, BindParameter), "expected a bound month-start value"
    value = param.value
    assert isinstance(value, datetime)
    return value


class _FakeResult:
    """Stand-in for the ``Result`` surface ``current_month_spend`` uses."""

    def __init__(self, value: Decimal | None) -> None:
        self._value = value

    def scalar_one(self) -> Decimal | None:
        return self._value


class _FakeSession:
    """In-memory fake ``AsyncSession`` over ``(created_at, cost)`` rows.

    ``cost`` may be ``None`` — a recorded row whose cost was unavailable
    (Requirement 12.2); SQL ``SUM`` skips NULLs, and returns NULL when no
    priced row matches, both of which the fake reproduces. Setting
    ``fail_reads`` makes the next reads raise — the spend-read failure of
    Requirement 14.7.
    """

    def __init__(self) -> None:
        self.rows: list[tuple[datetime, Decimal | None]] = []
        self.fail_reads = False

    async def execute(self, stmt: Select[Any]) -> _FakeResult:
        if self.fail_reads:
            raise RuntimeError("simulated spend read failure")
        month_start = _extract_month_start(stmt)
        costs = [
            cost for created_at, cost in self.rows if created_at >= month_start and cost is not None
        ]
        return _FakeResult(sum(costs, Decimal("0")) if costs else None)


class _SteppingClock:
    """Injected clock: starts at a fixed UTC instant, advances on demand."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance_hours(self, hours: int) -> None:
        self.now += timedelta(hours=hours)


class _LimitHolder:
    """Injected limit provider whose value the scenario can change."""

    def __init__(self, value: Decimal) -> None:
        self.value = value

    def __call__(self) -> Decimal:
        return self.value


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Drive an async body from a sync Hypothesis example via ``Runner``."""
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


def _month_key(now: datetime) -> tuple[int, int]:
    """UTC ``(year, month)`` — the shadow model's month identity."""
    utc_now = now.astimezone(UTC)
    return (utc_now.year, utc_now.month)


def _month_sum(rows: list[tuple[datetime, Decimal | None]], now: datetime) -> Decimal:
    """The shadow model's current-UTC-month spend over the fake rows."""
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return sum(
        (cost for created_at, cost in rows if created_at >= start and cost is not None),
        Decimal("0"),
    )


def _transition_events(captured: list[dict[str, Any]]) -> list[tuple[str, str, str, str | None]]:
    """Project captured breaker transition events to comparable tuples."""
    return [
        (
            event["direction"],
            event["tracked_spend"],
            event["limit"],
            event["cause"],
        )
        for event in captured
        if event.get("event") == "llm_spend_breaker_transition"
    ]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Row costs within the Numeric(10, 6) bounds; None models a recorded call
# whose cost was unavailable (contributes nothing to the tracked spend).
_COST = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("5"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
_ROW_COST = st.none() | _COST

_LIMIT = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("15"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Clock advance before each op, in hours. Mostly zero (bursts at one
# instant), some intra-month jumps, and 800h (~33 days) to guarantee UTC
# month rollovers. The scenario starts mid-month.
_ADVANCE_HOURS = st.sampled_from([0, 0, 0, 6, 24, 800])

_START = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

# Scenario ops (payload meaning depends on the kind):
#   add_row           — deposit an invocation-log row at the current clock
#                       time (payload: cost, possibly None/unavailable).
#   evaluate          — a successful breaker evaluation.
#   evaluate_read_failure — evaluation whose spend read raises (Req 14.7).
#   persist_failure   — record_persist_failure() after a failed log write
#                       (Requirement 14.8).
#   set_limit         — reconfigure the limit (payload: new limit; raising
#                       and lowering both occur, Requirement 14.5).
#   set_limit_to_spend — pin the limit exactly to the current month spend
#                       (the >= boundary case, Requirement 14.2).
_OP_KIND = st.sampled_from(
    [
        "add_row",
        "add_row",
        "evaluate",
        "evaluate",
        "evaluate_read_failure",
        "persist_failure",
        "set_limit",
        "set_limit_to_spend",
    ]
)

_Op = tuple[int, str, Decimal | None]


@st.composite
def _scenarios(draw: st.DrawFn) -> tuple[Decimal, list[_Op]]:
    """Build (initial limit, ops) with ops as (advance_hours, kind, payload)."""
    initial_limit = draw(_LIMIT)
    n_ops = draw(st.integers(min_value=1, max_value=25))
    ops: list[_Op] = []
    for _ in range(n_ops):
        advance = draw(_ADVANCE_HOURS)
        kind = draw(_OP_KIND)
        payload: Decimal | None = None
        if kind == "add_row":
            payload = draw(_ROW_COST)
        elif kind == "set_limit":
            payload = draw(_LIMIT)
        ops.append((advance, kind, payload))
    return initial_limit, ops


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 15: Spend circuit breaker opens exactly at the limit and fails safe  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_breaker_state_tracks_monthly_spend_and_fails_safe(
    scenario: tuple[Decimal, list[_Op]],
) -> None:
    """Open iff month spend ≥ limit; tracking failures fail safe; one event per flip.

    A shadow model walks the generated op sequence alongside the real
    breaker: after every successful evaluation the breaker must be open
    exactly when the current-UTC-month sum reaches the limit (Requirements
    14.1, 14.2); read/persist failures force it open with cause
    ``tracking_failure`` until a below-limit success closes it
    (Requirements 14.7, 14.8); closes after a month boundary are
    attributed ``month_rollover`` (Requirement 14.4) and same-month closes
    after a limit-reached open are ``limit_raised`` (Requirement 14.5);
    and the captured transition-event sequence equals the model's exactly
    — one event per open↔closed flip, none otherwise (Requirement 14.6).
    """
    initial_limit, ops = scenario

    async def _run() -> None:
        session = _FakeSession()
        clock = _SteppingClock(_START)
        limit = _LimitHolder(initial_limit)
        breaker = SpendCircuitBreaker(limit_provider=limit, clock=clock)

        # Shadow model.
        model_open = False
        model_open_cause: str | None = None
        model_open_month: tuple[int, int] | None = None
        expected_events: list[tuple[str, str, str, str | None]] = []
        expected_read_failures = 0

        def model_force_open(now: datetime) -> None:
            nonlocal model_open, model_open_cause, model_open_month
            if not model_open:
                expected_events.append(
                    ("opened", "unknown", str(limit.value), BreakerCause.TRACKING_FAILURE.value)
                )
            model_open = True
            model_open_cause = BreakerCause.TRACKING_FAILURE.value
            model_open_month = _month_key(now)

        with structlog.testing.capture_logs() as captured:
            for advance, kind, payload in ops:
                clock.advance_hours(advance)
                now = clock.now

                if kind == "add_row":
                    session.rows.append((now, payload))
                    continue
                if kind == "set_limit":
                    assert payload is not None
                    limit.value = payload
                    continue
                if kind == "set_limit_to_spend":
                    spend = _month_sum(session.rows, now)
                    if spend > 0:
                        # Pin the limit exactly at the tracked spend: the
                        # next evaluation must open (>= with equality).
                        limit.value = spend
                    continue

                if kind == "persist_failure":
                    state = breaker.record_persist_failure()
                    model_force_open(now)
                    assert state.is_open
                    assert state.tracked_spend is None
                    assert state.cause is BreakerCause.TRACKING_FAILURE
                    continue

                if kind == "evaluate_read_failure":
                    session.fail_reads = True
                    state = await breaker.evaluate(cast(AsyncSession, session))
                    session.fail_reads = False
                    expected_read_failures += 1
                    model_force_open(now)
                    assert state.is_open, "a spend-read failure must force the state open"
                    assert state.tracked_spend is None
                    assert state.cause is BreakerCause.TRACKING_FAILURE
                    continue

                assert kind == "evaluate"
                spend = _month_sum(session.rows, now)
                state = await breaker.evaluate(cast(AsyncSession, session))

                # Open iff the current-UTC-month sum reaches the limit
                # (Requirements 14.1, 14.2) — including exactly at it.
                assert state.is_open == (spend >= limit.value), (
                    f"spend={spend} limit={limit.value}: breaker must be open "
                    f"iff spend >= limit, got is_open={state.is_open}"
                )
                assert state.tracked_spend == spend
                assert state.limit == limit.value

                if spend >= limit.value:
                    if not model_open:
                        expected_events.append(
                            (
                                "opened",
                                str(spend),
                                str(limit.value),
                                BreakerCause.LIMIT_REACHED.value,
                            )
                        )
                    model_open = True
                    model_open_cause = BreakerCause.LIMIT_REACHED.value
                    model_open_month = _month_key(now)
                    assert state.cause is BreakerCause.LIMIT_REACHED
                else:
                    if model_open:
                        # Attribute the close: a new UTC month means the sum
                        # covers a fresh window (Requirement 14.4); recovery
                        # from a tracking failure keeps that cause
                        # (Requirements 14.7, 14.8); otherwise the limit was
                        # raised above the spend (Requirement 14.5).
                        if _month_key(now) != model_open_month:
                            close_cause = BreakerCause.MONTH_ROLLOVER.value
                        elif model_open_cause == BreakerCause.TRACKING_FAILURE.value:
                            close_cause = BreakerCause.TRACKING_FAILURE.value
                        else:
                            close_cause = BreakerCause.LIMIT_RAISED.value
                        expected_events.append(
                            ("closed", str(spend), str(limit.value), close_cause)
                        )
                    model_open = False
                    model_open_cause = None
                    model_open_month = None
                    assert state.cause is None

        # Exactly one structured transition event per open↔closed flip,
        # each carrying direction, tracked spend, limit, and cause
        # (Requirement 14.6) — and none for state-confirming updates.
        assert _transition_events(captured) == expected_events

        # Every read failure emitted exactly one llm_spend_read_failed
        # event naming the failure category (Requirement 14.7).
        read_failed = [e for e in captured if e.get("event") == "llm_spend_read_failed"]
        assert len(read_failed) == expected_read_failures
        assert all(e["category"] == "RuntimeError" for e in read_failed)

    _run_sync(_run)


# Feature: phase-3-llm-layer, Property 15: Spend circuit breaker opens exactly at the limit and fails safe  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(
    costs=st.lists(_COST, min_size=1, max_size=10),
    epsilon=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_breaker_opens_exactly_at_the_limit(costs: list[Decimal], epsilon: Decimal) -> None:
    """The >= boundary: below-limit closed, exactly-at-limit open, raised closes.

    With the limit strictly above the month's spend the breaker stays
    closed; pinned exactly equal it opens (Requirement 14.2, the equality
    edge of ``spend >= limit``); raised strictly above again it closes at
    the next evaluation with cause ``limit_raised`` (Requirement 14.5),
    emitting exactly one event per flip (Requirement 14.6).
    """

    async def _run() -> None:
        session = _FakeSession()
        clock = _SteppingClock(_START)
        total = sum(costs, Decimal("0"))
        limit = _LimitHolder(total + epsilon)
        breaker = SpendCircuitBreaker(limit_provider=limit, clock=clock)
        session.rows = [(clock.now, cost) for cost in costs]

        with structlog.testing.capture_logs() as captured:
            # Strictly above the spend: closed.
            state = await breaker.evaluate(cast(AsyncSession, session))
            assert not state.is_open
            assert state.tracked_spend == total

            # Exactly at the spend: open — the boundary is inclusive.
            limit.value = total
            state = await breaker.evaluate(cast(AsyncSession, session))
            assert state.is_open, "spend == limit must open the breaker (>= boundary)"
            assert state.cause is BreakerCause.LIMIT_REACHED

            # Raised strictly above again: closed at the next evaluation.
            limit.value = total + epsilon
            state = await breaker.evaluate(cast(AsyncSession, session))
            assert not state.is_open, "a raised limit must close the breaker (Req 14.5)"

        assert _transition_events(captured) == [
            ("opened", str(total), str(total), BreakerCause.LIMIT_REACHED.value),
            ("closed", str(total), str(total + epsilon), BreakerCause.LIMIT_RAISED.value),
        ]

    _run_sync(_run)


# Feature: phase-3-llm-layer, Property 15: Spend circuit breaker opens exactly at the limit and fails safe  # noqa: E501
@settings(max_examples=100, deadline=None)
@given(
    costs=st.lists(_ROW_COST, min_size=0, max_size=6),
    headroom=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("5"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_breaker_fails_safe_and_recovers(costs: list[Decimal | None], headroom: Decimal) -> None:
    """Read/persist failures force open; success and rollover close again.

    A spend-read failure forces the state open with cause
    ``tracking_failure`` and an unknown tracked spend (Requirement 14.7);
    the next successful below-limit evaluation closes it without
    intervention. A persist failure likewise forces it open (Requirement
    14.8); a close after the clock crosses into a new UTC month is
    attributed ``month_rollover`` (Requirement 14.4). Each flip emits
    exactly one transition event (Requirement 14.6).
    """

    async def _run() -> None:
        session = _FakeSession()
        clock = _SteppingClock(_START)
        total = sum((c for c in costs if c is not None), Decimal("0"))
        limit = _LimitHolder(total + headroom)  # always strictly above the spend
        breaker = SpendCircuitBreaker(limit_provider=limit, clock=clock)
        session.rows = [(clock.now, cost) for cost in costs]

        with structlog.testing.capture_logs() as captured:
            # Read failure → forced open, tracked spend unknown (Req 14.7).
            session.fail_reads = True
            state = await breaker.evaluate(cast(AsyncSession, session))
            session.fail_reads = False
            assert state.is_open
            assert state.tracked_spend is None
            assert state.cause is BreakerCause.TRACKING_FAILURE

            # Below-limit success → closed, recovery cause tracking_failure.
            state = await breaker.evaluate(cast(AsyncSession, session))
            assert not state.is_open
            assert state.tracked_spend == total

            # Persist failure → forced open again (Req 14.8).
            state = breaker.record_persist_failure()
            assert state.is_open
            assert state.cause is BreakerCause.TRACKING_FAILURE

            # New UTC month → the close is a month rollover (Req 14.4).
            clock.advance_hours(31 * 24)
            state = await breaker.evaluate(cast(AsyncSession, session))
            assert not state.is_open
            # Last month's rows left the window with the rollover (Req 14.1).
            assert state.tracked_spend == Decimal("0")

        limit_str = str(total + headroom)
        assert _transition_events(captured) == [
            ("opened", "unknown", limit_str, BreakerCause.TRACKING_FAILURE.value),
            ("closed", str(total), limit_str, BreakerCause.TRACKING_FAILURE.value),
            ("opened", "unknown", limit_str, BreakerCause.TRACKING_FAILURE.value),
            ("closed", "0", limit_str, BreakerCause.MONTH_ROLLOVER.value),
        ]

    _run_sync(_run)
