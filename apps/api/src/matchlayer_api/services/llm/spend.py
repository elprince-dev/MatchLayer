"""SpendCircuitBreaker — the global monthly LLM spend cap (Requirement 14).

The breaker's tracked spend is **derived, not stored** (design decision
D5): every evaluation recomputes ``SUM(cost_usd)`` over the current UTC
calendar month's ``llm_invocation_logs`` rows via
:func:`~matchlayer_api.services.llm.invocation_log.current_month_spend`
(Requirement 14.1). The breaker is *open* — LLM_Features app-wide in the
LLM_Unavailable state — iff that sum reaches or exceeds
``llm_monthly_spend_limit_usd`` (Requirement 14.2).

The orchestrator calls :meth:`SpendCircuitBreaker.evaluate` before
initiating each LLM_Provider call and again after persisting each
invocation-log row (Requirement 14.2); when
:func:`~matchlayer_api.services.llm.invocation_log.record_invocation`
reports a failed persist it calls
:meth:`SpendCircuitBreaker.record_persist_failure` instead (Requirement
14.8). The most recent result is memoized in process-local state so the
``/healthz`` probe and the 503 rejection path read it without a query per
probe (:attr:`SpendCircuitBreaker.state`).

**Fail-safe open** (Requirements 14.7, 14.8): a tracked-spend read failure
or an invocation-log persist failure forces the state open with cause
``tracking_failure`` — an unaccounted cost can never allow unbounded
spend. The open state clears itself: the next evaluation that successfully
reads a below-limit sum closes the breaker, so recovery from a tracking
failure, a UTC month rollover (Requirement 14.4), or a raised limit
(Requirement 14.5) requires no restart, no code change, and no manual
intervention.

**Transition events** (Requirement 14.6): exactly one structured
``llm_spend_breaker_transition`` event is emitted per open↔closed change,
recording the direction, the tracked spend at the transition, the
configured limit, and the trigger cause — never Restricted PII, never the
API key. Evaluations that confirm the current state emit nothing. Read
failures additionally emit one ``llm_spend_read_failed`` event naming the
failure category (Requirement 14.7).

The clock and the limit provider are injectable so tests can exercise UTC
month rollover and limit raises with fixed clocks and stepping providers
(design Testing Strategy), mirroring ``services/llm/quota.py``.

Design reference: phase-3-llm-layer §"SpendCircuitBreaker
(services/llm/spend.py)".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.config import get_settings
from matchlayer_api.services.llm.invocation_log import current_month_spend

__all__ = [
    "BreakerCause",
    "BreakerState",
    "SpendCircuitBreaker",
    "get_spend_circuit_breaker",
    "reset_spend_circuit_breaker",
]

_log = structlog.get_logger(__name__)


class BreakerCause(StrEnum):
    """Trigger causes for breaker transitions (Requirement 14.6).

    ``LIMIT_REACHED`` opens the breaker when the tracked spend reaches
    the configured limit; ``TRACKING_FAILURE`` opens it when the tracked
    spend cannot be read or a call's cost could not be persisted.
    ``MONTH_ROLLOVER`` and ``LIMIT_RAISED`` close it when a fresh
    evaluation finds the (new month's / newly raised limit's) tracked
    spend below the limit; ``TRACKING_FAILURE`` also labels the close
    that recovers from a transient tracking failure.
    """

    LIMIT_REACHED = "limit_reached"
    MONTH_ROLLOVER = "month_rollover"
    LIMIT_RAISED = "limit_raised"
    TRACKING_FAILURE = "tracking_failure"


@dataclass(frozen=True, slots=True)
class BreakerState:
    """Memoized snapshot of the breaker after its most recent update.

    ``is_open`` gates new provider calls (open → 503 upstream, Requirement
    14.3) and feeds the ``/healthz`` ``llm`` field (Requirement 10.1).
    ``tracked_spend`` is the last successfully read monthly sum (``None``
    when unknown — before the first evaluation or after a tracking
    failure); ``limit`` is the configured limit at the last update;
    ``cause`` is why the breaker is currently open (``None`` while
    closed).
    """

    is_open: bool
    tracked_spend: Decimal | None
    limit: Decimal | None
    cause: BreakerCause | None


def _utc_now() -> datetime:
    """Default injected clock: timezone-aware current UTC time."""
    return datetime.now(UTC)


def _default_limit() -> Decimal:
    """Default injected limit provider: the configured monthly cap."""
    return get_settings().llm_monthly_spend_limit_usd


def _month_key(now: datetime) -> tuple[int, int]:
    """The UTC ``(year, month)`` the instant ``now`` falls in.

    Comparing month keys across evaluations is how a close transition is
    attributed to a UTC month rollover (Requirement 14.4); normalizing to
    UTC first keeps a clock in another zone from shifting the boundary.
    """
    utc_now = now.astimezone(UTC)
    return (utc_now.year, utc_now.month)


class SpendCircuitBreaker:
    """App-wide monthly spend circuit breaker over the invocation log.

    One instance per process (see :func:`get_spend_circuit_breaker`); the
    memoized :attr:`state` starts *closed* with an unknown tracked spend —
    the pipeline evaluates before every provider call (Requirement 14.2),
    so no call is ever gated on a stale never-evaluated state.

    State mutation happens only in synchronous sections (after any
    ``await``), so concurrent evaluations on one event loop can never
    interleave a transition check — each open↔closed change emits its
    event exactly once (Requirement 14.6).
    """

    def __init__(
        self,
        *,
        limit_provider: Callable[[], Decimal] = _default_limit,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._limit_provider = limit_provider
        self._clock = clock
        self._state = BreakerState(is_open=False, tracked_spend=None, limit=None, cause=None)
        # The UTC month of the most recent open-confirming update, used
        # to attribute a later close to a month rollover (Req 14.6).
        self._open_month: tuple[int, int] | None = None

    @property
    def state(self) -> BreakerState:
        """The memoized snapshot from the most recent update.

        Read by the ``/healthz`` probe and the 503 rejection path without
        touching storage (design decision D5).
        """
        return self._state

    async def evaluate(self, session: AsyncSession) -> BreakerState:
        """Recompute the breaker state from the current month's spend.

        Called before each provider call and after each invocation-log
        persist (Requirement 14.2). Open iff the current UTC month's
        ``SUM(cost_usd)`` reaches or exceeds the configured limit; a
        below-limit read closes an open breaker regardless of why it
        opened, which is exactly how month rollover (Requirement 14.4),
        a raised limit (Requirement 14.5), and tracking-failure recovery
        (Requirements 14.7, 14.8) all resolve without intervention.

        A read failure never propagates: it forces the state open with
        cause ``tracking_failure`` and emits one ``llm_spend_read_failed``
        event naming the failure category (Requirement 14.7).

        Args:
            session: The request-scoped :class:`AsyncSession` the spend
                query runs on.

        Returns:
            The updated memoized :class:`BreakerState`.
        """
        limit = self._limit_provider()
        now = self._clock()
        try:
            spend = await current_month_spend(session, clock=self._clock)
        except Exception as exc:
            # Requirement 14.7: the event names the failure category —
            # the exception class, never its message (which could carry
            # driver/DSN details), never PII, never the key.
            _log.warning("llm_spend_read_failed", category=type(exc).__name__)
            return self._force_open(tracked_spend=None, limit=limit, now=now)

        if spend >= limit:
            return self._transition_open(
                tracked_spend=spend, limit=limit, now=now, cause=BreakerCause.LIMIT_REACHED
            )
        return self._transition_closed(tracked_spend=spend, limit=limit, now=now)

    def record_persist_failure(self) -> BreakerState:
        """Force the breaker open after a failed invocation-log persist.

        Called when
        :func:`~matchlayer_api.services.llm.invocation_log.record_invocation`
        returns ``False``: the completed call's cost never entered the
        tracked spend, so the breaker fails safe to open with cause
        ``tracking_failure`` until a subsequent evaluation successfully
        reads a below-limit sum (Requirement 14.8). The persist failure
        itself is already reported by the invocation log's
        ``invocation_log_write_failed`` event; this method emits only the
        transition event, and only if the state actually flips
        (Requirement 14.6).

        Returns:
            The updated memoized :class:`BreakerState`.
        """
        # The completed call's cost is missing from the log, so the true
        # tracked spend is unknown — the transition event reports it as
        # such rather than echoing a stale last-read figure.
        return self._force_open(
            tracked_spend=None,
            limit=self._limit_provider(),
            now=self._clock(),
        )

    def _force_open(
        self, *, tracked_spend: Decimal | None, limit: Decimal, now: datetime
    ) -> BreakerState:
        """Open (or keep open) the breaker for a tracking failure."""
        return self._transition_open(
            tracked_spend=tracked_spend, limit=limit, now=now, cause=BreakerCause.TRACKING_FAILURE
        )

    def _transition_open(
        self,
        *,
        tracked_spend: Decimal | None,
        limit: Decimal,
        now: datetime,
        cause: BreakerCause,
    ) -> BreakerState:
        """Move to (or refresh) the open state, logging one event per flip."""
        was_open = self._state.is_open
        self._state = BreakerState(
            is_open=True, tracked_spend=tracked_spend, limit=limit, cause=cause
        )
        # Refresh the open context on every open-confirming update so a
        # later close is attributed against the most recent evidence.
        self._open_month = _month_key(now)
        if not was_open:
            self._log_transition(direction="opened", state=self._state)
        return self._state

    def _transition_closed(
        self, *, tracked_spend: Decimal, limit: Decimal, now: datetime
    ) -> BreakerState:
        """Move to (or confirm) the closed state, logging one event per flip."""
        was_open = self._state.is_open
        close_cause = self._close_cause(now=now) if was_open else None
        self._state = BreakerState(
            is_open=False, tracked_spend=tracked_spend, limit=limit, cause=None
        )
        self._open_month = None
        if was_open:
            self._log_transition(direction="closed", state=self._state, cause=close_cause)
        return self._state

    def _close_cause(self, *, now: datetime) -> BreakerCause:
        """Attribute an open→closed transition to its trigger (Req 14.6).

        A new UTC month means the recomputed sum covers a fresh window
        (``month_rollover``, Requirement 14.4); recovery from a
        tracking-failure open keeps that cause; otherwise a below-limit
        read within the same month can only mean the limit was raised
        above the tracked spend (``limit_raised``, Requirement 14.5) —
        invocation-log rows are append-only, so a month's sum never
        decreases.
        """
        if self._open_month is not None and _month_key(now) != self._open_month:
            return BreakerCause.MONTH_ROLLOVER
        if self._state.cause is BreakerCause.TRACKING_FAILURE:
            return BreakerCause.TRACKING_FAILURE
        # Invocation-log rows are append-only, so within one month the
        # tracked sum never decreases: a same-month below-limit read
        # after a limit-reached open can only mean the limit was raised
        # above the tracked spend (Requirement 14.5).
        return BreakerCause.LIMIT_RAISED

    def _log_transition(
        self, *, direction: str, state: BreakerState, cause: BreakerCause | None = None
    ) -> None:
        """Emit the single structured transition event (Requirement 14.6).

        Carries the direction, the tracked spend at the transition
        (rendered as a string; ``"unknown"`` when unreadable), the
        configured limit, and the trigger cause. No Restricted PII and
        no API key can appear here — every field is derived from
        aggregate cost figures and configuration.
        """
        effective_cause = cause if cause is not None else state.cause
        _log.info(
            "llm_spend_breaker_transition",
            direction=direction,
            tracked_spend=(
                str(state.tracked_spend) if state.tracked_spend is not None else "unknown"
            ),
            limit=str(state.limit),
            cause=effective_cause.value if effective_cause is not None else None,
        )


# ---------------------------------------------------------------------------
# Process-local singleton.
#
# The breaker's memoized state must outlive individual requests (design
# decision D5: /healthz and the 503 path read it without a query), so one
# instance is shared per process. Tests construct their own instances with
# injected clocks/limits, or call reset_spend_circuit_breaker() to isolate
# app-level state between cases.
# ---------------------------------------------------------------------------

_breaker: SpendCircuitBreaker | None = None


def get_spend_circuit_breaker() -> SpendCircuitBreaker:
    """Return the process-wide :class:`SpendCircuitBreaker` instance."""
    global _breaker
    if _breaker is None:
        _breaker = SpendCircuitBreaker()
    return _breaker


def reset_spend_circuit_breaker() -> None:
    """Drop the process-wide instance (test isolation only)."""
    global _breaker
    _breaker = None
