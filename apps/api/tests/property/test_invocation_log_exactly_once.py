"""Feature: phase-3-llm-layer — Property 20.

# Feature: phase-3-llm-layer, Property 20: Invocation log records every call exactly once

Property 20: Invocation log records every call exactly once.

    *For any* provider call outcome (success or each failure category,
    streaming or non-streaming) with any generated usage payload
    (provider-reported cost present, token counts only, or usage absent),
    exactly one invocation-log row is written recording the feature, the
    registry-active prompt version, model, redactor version, the input hash
    of Property 4, the validated output or the failure category (never
    both), latency, token usage and cost with the correct basis
    (``provider_reported`` when reported, ``computed`` from configured
    pricing otherwise, ``unavailable`` — distinct from zero — when usage is
    missing), user id, match id, and a UTC timestamp; and the serialized
    row contains no planted PII sentinel.

**Validates: Requirements 2.4, 12.1, 12.2, 12.3**

What is driven, and how (no database)
-------------------------------------
The unit under test is the real
:func:`matchlayer_api.services.llm.invocation_log.record_invocation` — the
single write path every provider call (streaming or not; the orchestrator
funnels both through this one function, which is why "streaming or
non-streaming" collapses to one code path here) terminates in. Per the
design's Testing Strategy and the conventions of this suite (see
``test_soft_delete_idempotency.py``), it is driven against an **in-memory
fake ``AsyncSession``** that models exactly the surface the function
touches: ``begin_nested()`` returning an async SAVEPOINT context manager,
and ``add(row)``. Rows added inside a savepoint that exits cleanly count as
*persisted*; a savepoint that exits with an error persists nothing — the
faithful no-DB analogue of the flush-on-exit semantics the real session
provides.

Per generated scenario the property asserts:

* **Exactly once** (Req 12.1): one ``record_invocation`` call persists
  exactly one row and returns ``True`` — for every outcome (validated
  output, or each :class:`FailureReason` category) and every usage shape.
* **Registry-active prompt version** (Req 2.4): the row records the
  version resolved from ``ACTIVE_PROMPT_VERSIONS`` for the feature — the
  single designated active-version source.
* **Output XOR failure category** (Req 12.1): the persisted row carries
  the validated output or the failure category, never both; and calling
  with both or neither raises ``ValueError`` and persists **zero** rows.
* **Usage fidelity with the correct basis** (Req 12.2): token counts and
  cost land on the row exactly as reported; ``None`` (unavailable) is
  preserved as ``None`` — never coerced to zero — and
  ``cost_basis="unavailable"`` always rides with a ``None`` cost, while
  ``provider_reported`` / ``computed`` bases always carry a real cost.
* **No PII** (Req 12.3): a unique PII sentinel is planted in the synthetic
  redacted-prompt-input text; only its sha256 digest is handed to the
  writer, and the fully serialized row (every column value) contains
  neither the sentinel nor the raw input text.
* **UTC timestamp** (Req 12.1): ``created_at`` is server-defaulted; with a
  fake session that default cannot fire, so the property pins the schema
  fact instead — the column is timezone-aware and carries a server
  default, which the migration materializes as UTC ``now()``.

Identifiers (user id, match id, model, latency) are asserted to round-trip
verbatim onto the row.
"""

from __future__ import annotations

import asyncio
import hashlib
import string
import uuid
from collections.abc import Callable, Coroutine
from decimal import Decimal
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.db.models import LLMInvocationLog
from matchlayer_api.ml.llm.client import LLMUsage
from matchlayer_api.ml.prompts.registry import ACTIVE_PROMPT_VERSIONS, LLMFeature
from matchlayer_api.services.llm.invocation_log import record_invocation
from matchlayer_api.services.llm.redaction import REDACTOR_VERSION
from matchlayer_api.services.llm.schemas import FailureReason

# ---------------------------------------------------------------------------
# In-memory fake session — models the exact surface record_invocation uses:
#     async with session.begin_nested():
#         session.add(row)
# Rows added inside a cleanly-exited savepoint are "persisted"; an erroring
# savepoint persists nothing (the SAVEPOINT rollback of Requirement 12.5).
# ---------------------------------------------------------------------------


class _FakeSavepoint:
    """Async context manager standing in for ``AsyncSessionTransaction``."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSavepoint:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            # Clean exit == flush succeeded: pending rows become persisted.
            self._session.persisted.extend(self._session._pending)
        # Error exit == savepoint rollback: pending rows are discarded.
        self._session._pending.clear()
        return False


class _FakeSession:
    """In-memory fake ``AsyncSession`` recording persisted rows."""

    def __init__(self) -> None:
        self.persisted: list[LLMInvocationLog] = []
        self._pending: list[LLMInvocationLog] = []

    def begin_nested(self) -> _FakeSavepoint:
        return _FakeSavepoint(self)

    def add(self, row: LLMInvocationLog) -> None:
        self._pending.append(row)


def _run_sync(coro_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Run an async test body via :class:`asyncio.Runner`.

    Mirrors the other async property tests in this suite: ``Runner`` closes
    its event loop deterministically on ``__exit__`` so no
    ``ResourceWarning("unclosed event loop")`` can leak into teardown, where
    this suite's ``filterwarnings = ["error"]`` would promote it to a
    failure.
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_token_count = st.integers(min_value=0, max_value=10_000_000)

# Costs within the Numeric(10, 6) column bounds; zero is a legitimate
# recorded cost, deliberately distinct from None/unavailable (Req 12.2).
_cost = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("9999.999999"),
    places=6,
    allow_nan=False,
    allow_infinity=False,
)

# The three usage shapes of the property: provider-reported cost (token
# counts individually present or absent), cost computed locally from token
# counts and configured pricing, and usage entirely absent (all None —
# never zeros).
_usage = st.one_of(
    st.builds(
        LLMUsage,
        input_tokens=st.none() | _token_count,
        output_tokens=st.none() | _token_count,
        cost_usd=_cost,
        cost_basis=st.just("provider_reported"),
    ),
    st.builds(
        LLMUsage,
        input_tokens=_token_count,
        output_tokens=_token_count,
        cost_usd=_cost,
        cost_basis=st.just("computed"),
    ),
    st.builds(
        LLMUsage,
        input_tokens=st.none(),
        output_tokens=st.none(),
        cost_usd=st.none(),
        cost_basis=st.just("unavailable"),
    ),
)

# A JSON-shaped validated-output payload. Content is arbitrary — the writer
# persists whatever the schema gate validated; structure fidelity is what
# matters here.
_json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1_000, max_value=1_000),
    st.text(max_size=20),
)
_output_payload = st.dictionaries(
    keys=st.text(alphabet=string.ascii_lowercase + "_", min_size=1, max_size=12),
    values=st.one_of(_json_scalar, st.lists(_json_scalar, max_size=3)),
    min_size=1,
    max_size=5,
)

# Outcome: a validated output XOR a failure category — every FailureReason
# value is generated so each failure category is exercised (Req 12.1).
_outcome: st.SearchStrategy[tuple[dict[str, Any] | None, FailureReason | None]] = st.one_of(
    _output_payload.map(lambda payload: (payload, None)),
    st.sampled_from(list(FailureReason)).map(lambda reason: (None, reason)),
)

_model_name = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-./:",
    min_size=1,
    max_size=30,
)

# Synthetic redacted-prompt-input body text the PII sentinel is planted in.
_prompt_body = st.text(max_size=200)


def _serialize_row(row: LLMInvocationLog) -> str:
    """Concatenate the repr of every column value on ``row``.

    The property's PII assertion is over the *serialized row* — every value
    that would be written to storage — so all mapped columns are included.
    """
    parts: list[str] = []
    for column in LLMInvocationLog.__table__.columns:
        parts.append(f"{column.key}={getattr(row, column.key)!r}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# The property.
# ---------------------------------------------------------------------------


# Feature: phase-3-llm-layer, Property 20: Invocation log records every call exactly once
@settings(max_examples=100, deadline=None)
@given(
    feature=st.sampled_from(list(LLMFeature)),
    outcome=_outcome,
    usage=_usage,
    user_id=st.uuids(),
    match_result_id=st.uuids(),
    latency_ms=st.integers(min_value=0, max_value=10_000_000),
    llm_model=_model_name,
    prompt_body=_prompt_body,
)
def test_invocation_log_records_every_call_exactly_once(
    feature: LLMFeature,
    outcome: tuple[dict[str, Any] | None, FailureReason | None],
    usage: LLMUsage,
    user_id: UUID,
    match_result_id: UUID,
    latency_ms: int,
    llm_model: str,
    prompt_body: str,
) -> None:
    """One call → exactly one faithful, PII-free row; bad XOR → no row.

    Property 20 (Requirements 2.4, 12.1, 12.2, 12.3). The real
    ``record_invocation`` is driven once per generated provider-call
    outcome and usage shape against the in-memory fake session, then the
    single persisted row is checked field-by-field; finally the two
    contract-violating shapes (both output and failure category; neither)
    are driven and must raise without persisting anything.
    """
    output, failure_category = outcome

    # A unique PII sentinel planted in the synthetic redacted prompt input.
    # Only the sha256 digest crosses into the writer (Req 12.3).
    pii_sentinel = f"PII-SENTINEL-{uuid.uuid4().hex}"
    prompt_input = f"{prompt_body} {pii_sentinel}"
    input_hash = hashlib.sha256(prompt_input.encode("utf-8")).hexdigest()

    # The registry-active version — the single designated source (Req 2.4).
    active_version = ACTIVE_PROMPT_VERSIONS[feature]

    async def _run() -> None:
        session = _FakeSession()

        ok = await record_invocation(
            session,  # type: ignore[arg-type]
            user_id=user_id,
            match_result_id=match_result_id,
            feature=feature,
            prompt_template_version=active_version,
            llm_model=llm_model,
            redactor_version=REDACTOR_VERSION,
            input_hash=input_hash,
            latency_ms=latency_ms,
            usage=usage,
            output=output,
            failure_category=failure_category,
        )

        # --- Exactly one row per call (Req 12.1). ---
        assert ok is True
        assert len(session.persisted) == 1, (
            f"expected exactly one persisted row, got {len(session.persisted)}"
        )
        row = session.persisted[0]

        # --- Field fidelity: feature, registry-active prompt version
        # (Req 2.4), model, redactor version, input hash, latency, user id,
        # match id. ---
        assert row.feature == feature.value
        assert row.prompt_template_version == active_version
        assert row.llm_model == llm_model
        assert row.redactor_version == REDACTOR_VERSION
        assert row.input_hash == input_hash
        assert row.latency_ms == latency_ms
        assert row.user_id == user_id
        assert row.match_result_id == match_result_id

        # --- Validated output XOR failure category (Req 12.1). ---
        assert (row.output is None) != (row.failure_category is None), (
            "the row must carry the validated output or the failure category, never both"
        )
        if output is not None:
            assert row.output == output
            assert row.failure_category is None
        else:
            assert failure_category is not None
            assert row.failure_category == failure_category.value
            assert row.output is None

        # --- Usage and cost with the correct basis; None is preserved as
        # unavailable, never coerced to zero (Req 12.2). ---
        assert row.input_tokens == usage.input_tokens
        assert row.output_tokens == usage.output_tokens
        assert row.cost_usd == usage.cost_usd
        assert row.cost_basis == usage.cost_basis
        if usage.cost_basis == "unavailable":
            assert row.cost_usd is None, "unavailable cost must be None, distinct from zero"
        else:
            assert row.cost_usd is not None, f"{usage.cost_basis} basis must carry a real cost"
        if usage.input_tokens is None:
            assert row.input_tokens is None, "unavailable token count must stay None, not 0"
        if usage.output_tokens is None:
            assert row.output_tokens is None, "unavailable token count must stay None, not 0"

        # --- UTC timestamp: created_at is a timezone-aware column with a
        # server default (materialized as UTC now() by the migration); the
        # fake session cannot fire it, so the schema fact is pinned. ---
        created_at_col = LLMInvocationLog.__table__.columns["created_at"]
        assert created_at_col.server_default is not None
        assert getattr(created_at_col.type, "timezone", False) is True

        # --- No planted PII sentinel anywhere in the serialized row
        # (Req 12.3): the prompt input is represented only by its hash. ---
        serialized = _serialize_row(row)
        assert pii_sentinel not in serialized
        assert prompt_input not in serialized

        # --- Caller-contract violations persist nothing (Req 12.1: the row
        # records one XOR the other — never both, never neither). ---
        some_output = output if output is not None else {"k": "v"}
        some_failure = failure_category if failure_category is not None else FailureReason.TIMEOUT
        for bad_output, bad_failure in (
            (some_output, some_failure),  # both
            (None, None),  # neither
        ):
            with pytest.raises(ValueError, match="exactly one"):
                await record_invocation(
                    session,  # type: ignore[arg-type]
                    user_id=user_id,
                    match_result_id=match_result_id,
                    feature=feature,
                    prompt_template_version=active_version,
                    llm_model=llm_model,
                    redactor_version=REDACTOR_VERSION,
                    input_hash=input_hash,
                    latency_ms=latency_ms,
                    usage=usage,
                    output=bad_output,
                    failure_category=bad_failure,
                )
        assert len(session.persisted) == 1, (
            "contract-violating calls must not persist additional rows"
        )

    _run_sync(_run)
