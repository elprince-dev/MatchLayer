"""Invocation-log persistence and the monthly spend query (Requirement 12).

Exactly one ``llm_invocation_logs`` row is written per LLM_Provider call —
streaming or non-streaming, success or failure (Requirement 12.1). The row
records the LLM_Feature, the registry-active Prompt_Template version the
call used, the LLM_Model, the PII_Redactor version, the deterministic hash
of the redacted prompt input (the same digest as the LLM_Cache key,
Requirement 12.6), the validated structured output XOR a
:class:`~matchlayer_api.services.llm.schemas.FailureReason` category,
wall-clock latency, token usage and cost with their basis (``None`` means
*unavailable*, deliberately distinct from zero — Requirement 12.2), the
owning user id, the Match_Result id, and a UTC timestamp. Raw Resume text,
Job_Description text, or any other Restricted PII never appears here
(Requirement 12.3) — the prompt input is represented only by its hash.

**Best-effort write** (Requirement 12.5): :func:`record_invocation` stages
the row inside a SAVEPOINT (``session.begin_nested()``) so a failed write
rolls back only the savepoint, leaving the request-scoped session usable
and the user-facing request unaffected — never a 5xx from a log-write
failure. The failure is reported two ways: a structured
``invocation_log_write_failed`` event (no PII) and a ``False`` return
value, which the orchestrator feeds to the Spend_Circuit_Breaker so it
force-opens when a call's cost could not enter the tracked spend
(Requirement 14.8). Complete storage unavailability is outside this
guarantee — it surfaces through normal storage error handling as
Requirement 12.5 permits.

**Monthly spend query** (Requirement 14.1): :func:`current_month_spend`
sums ``cost_usd`` over rows timestamped within the current UTC calendar
month via SQLAlchemy Core — no raw SQL (`conventions.md`). It is the data
source for the Spend_Circuit_Breaker's ``evaluate()`` (design
§"SpendCircuitBreaker"); read failures propagate to the caller, which
treats them as a tracking failure and fails safe (Requirement 14.7). The
clock is injectable so tests can exercise UTC month-rollover behavior
(design Testing Strategy), mirroring the pattern in
``services/llm/quota.py``.

Rows are append-only operational records: nothing in this module (or the
API at large) deletes, expires, or overwrites them in Phase 3
(Requirement 12.4).

Design reference: phase-3-llm-layer §"Invocation logging
(services/llm/invocation_log.py)".
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.db.models import LLMInvocationLog
from matchlayer_api.ml.llm.client import LLMUsage
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.schemas import FailureReason

__all__ = ["current_month_spend", "record_invocation"]

_log = structlog.get_logger(__name__)


def _utc_now() -> datetime:
    """Default injected clock: timezone-aware current UTC time."""
    return datetime.now(UTC)


def _month_start_utc(now: datetime) -> datetime:
    """First instant (00:00:00 UTC) of ``now``'s calendar month.

    The Spend_Circuit_Breaker's tracked spend covers the current calendar
    month in UTC (Requirement 14.1), so the boundary is derived after
    normalizing ``now`` to UTC — a clock in another zone can never shift
    the month window.
    """
    utc_now = now.astimezone(UTC)
    return utc_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def record_invocation(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_result_id: UUID,
    feature: LLMFeature,
    prompt_template_version: int,
    llm_model: str,
    redactor_version: str,
    input_hash: str,
    latency_ms: int,
    usage: LLMUsage,
    # dict[str, Any]: the schema-validated JSONB payload — JSON is
    # inherently heterogeneous (nested objects, arrays, strings), so Any
    # is the honest value type, matching the model column.
    output: dict[str, Any] | None = None,
    failure_category: FailureReason | None = None,
) -> bool:
    """Persist exactly one invocation-log row for a completed provider call.

    Called by the orchestrator after every LLM_Provider call terminates,
    whether it succeeded or failed (Requirement 12.1). Exactly one of
    ``output`` (the schema-validated structured output) and
    ``failure_category`` (a closed :class:`FailureReason` value) must be
    provided — the row records one XOR the other, never both, never
    neither.

    ``prompt_template_version`` is the registry-active version the call
    was assembled under (the orchestrator resolves it from
    ``ACTIVE_PROMPT_VERSIONS``); ``input_hash`` is the canonical digest
    over the redacted prompt input shared with the LLM_Cache key
    (Requirement 12.6); ``usage`` carries nullable token counts and cost
    plus the ``cost_basis`` recording how the cost was obtained
    (Requirement 12.2). ``created_at`` is the database's UTC ``now()``
    via the column's server default.

    The write is **best-effort** (Requirement 12.5): it is staged inside
    a SAVEPOINT so a failure rolls back only this row, and the exception
    is swallowed after emitting a structured
    ``invocation_log_write_failed`` event containing no Restricted PII.

    Args:
        session: The request-scoped :class:`AsyncSession`. The row is
            staged and flushed under a nested transaction; the router
            still owns the outer commit.
        user_id: The owning User_Account id.
        match_result_id: The Match_Result the feature ran against.
        feature: Which LLM_Feature made the call.
        prompt_template_version: The registry-active Prompt_Template
            version used to assemble the prompt.
        llm_model: The LLM_Model identifier the call was made with.
        redactor_version: The PII_Redactor version applied to the prompt
            input (Requirement 3.4).
        input_hash: sha256 digest of the redacted prompt input — never
            the raw input (Requirement 12.3).
        latency_ms: Wall-clock latency from transmission start to the
            final token or termination.
        usage: Token counts and cost with their basis; ``None`` fields
            mean *unavailable*, distinct from zero (Requirement 12.2).
        output: The validated structured output, or ``None`` on failure.
        failure_category: The failure category, or ``None`` on success.

    Returns:
        ``True`` when the row was flushed successfully; ``False`` when
        the write failed — the caller must force the
        Spend_Circuit_Breaker open (Requirement 14.8) because the call's
        cost did not enter the tracked spend.

    Raises:
        ValueError: If both or neither of ``output`` and
            ``failure_category`` are provided — a caller contract bug,
            not a storage failure, so it is never swallowed.
    """
    if (output is None) == (failure_category is None):
        raise ValueError(
            "exactly one of 'output' and 'failure_category' must be provided: "
            "an invocation log row records the validated output XOR a "
            "failure category (Requirement 12.1)"
        )

    row = LLMInvocationLog(
        user_id=user_id,
        match_result_id=match_result_id,
        feature=feature.value,
        prompt_template_version=prompt_template_version,
        llm_model=llm_model,
        redactor_version=redactor_version,
        input_hash=input_hash,
        output=output,
        failure_category=failure_category.value if failure_category is not None else None,
        latency_ms=latency_ms,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        cost_basis=usage.cost_basis,
    )

    try:
        # SAVEPOINT: the row is flushed when the nested block exits; a
        # flush failure rolls back only the savepoint, so the outer
        # request transaction stays usable and the user-facing request
        # completes normally (Requirement 12.5).
        async with session.begin_nested():
            session.add(row)
    except Exception:
        # The event carries identifiers only — never prompt content,
        # output payloads, or any Restricted PII (Requirement 12.5).
        _log.warning(
            "invocation_log_write_failed",
            feature=feature.value,
            user_id=str(user_id),
            match_result_id=str(match_result_id),
            llm_model=llm_model,
        )
        return False
    return True


async def current_month_spend(
    session: AsyncSession,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> Decimal:
    """Sum recorded LLM cost over the current UTC calendar month.

    ``SUM(cost_usd)`` over every ``llm_invocation_logs`` row whose
    ``created_at`` falls at or after the current month's first instant
    (00:00:00 UTC on day 1), built with SQLAlchemy Core — no raw SQL.
    Rows with a ``NULL`` cost (cost unavailable, Requirement 12.2)
    contribute nothing, matching SQL ``SUM`` semantics; failed calls
    that did record a cost are included (Requirement 14.1).

    Read failures (storage errors) propagate: the Spend_Circuit_Breaker
    treats an unreadable tracked spend as a tracking failure and fails
    safe to the open state (Requirement 14.7) — swallowing the error
    here would hide exactly the condition the breaker must react to.

    Args:
        session: The request-scoped :class:`AsyncSession`.
        clock: Injectable timezone-aware clock; production uses real UTC
            time, tests inject fixed clocks to exercise month rollover
            (design Testing Strategy).

    Returns:
        The tracked spend as a :class:`~decimal.Decimal`; ``Decimal("0")``
        when no priced rows exist this month.
    """
    month_start = _month_start_utc(clock())
    stmt = select(func.sum(LLMInvocationLog.cost_usd)).where(
        LLMInvocationLog.created_at >= month_start
    )
    total: Decimal | None = (await session.execute(stmt)).scalar_one()
    return total if total is not None else Decimal("0")
