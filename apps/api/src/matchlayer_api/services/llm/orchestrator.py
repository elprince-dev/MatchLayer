"""The shared LLM request pipeline (design decision D1).

Every LLM_Feature request — Resume_Coach, Bullet_Rewriter,
Interview_Question_Generator, streaming or not — flows through the one
:class:`LLMOrchestrator` here, parameterized by a small per-feature
:class:`LLMFeatureSpec` (template identity, result schema, input builder,
fallback builder). The ~15 cross-cutting requirements (quota, redaction,
caching, spend control, validation, invocation logging, failure taxonomy)
are therefore implemented once and testable once.

The pipeline order is **normative** (design §"Request pipeline"; several
requirements pin specific orderings):

1.  Daily_Quota **gate** (read-only) — exhausted → 429 (Req 13.2).
2.  **Persisted-result reuse** (coach only, design D7) — same active prompt
    version + model → serve the stored row, no call, no quota (Req 5.4).
3.  Key-present check — key absent → ``llm_unavailable`` fallback
    (Req 1.8, 10.3).
4.  **Redaction** of every PII-bearing input (Req 3).
5.  **Prompt assembly** — load active template, render, build messages
    (Req 2, 4).
6.  Canonical **prompt-input hash** over the redacted values (Req 3.8,
    12.6, 15.1).
7.  **Cache lookup** — hit → serve, no count, no call (Req 15.2).
8.  Spend_Circuit_Breaker **evaluation** — open → 503 (Req 14.2, 14.3).
9.  Atomic Daily_Quota **reserve** — executed only at provider-call
    initiation; a lost race → the same 429 (Req 13.3, 13.7).
10. **Single provider call** — exactly one attempt, no retries (Req 1.12,
    9.6).
11. **Terminal validation** against the feature's Pydantic schema — the
    streamed text is display-progressive only; the authoritative result is
    assembled and validated at stream end (Req 8.2, 8.3).
12. **Persist** the validated LLM_Result → best-effort **cache write** →
    **invocation log** (one row per provider call, success or failure) →
    breaker re-evaluation (Req 12.1, 14.2, 14.8, 15.4).

**Failure taxonomy** (Req 9): every exception on the pipeline maps to
exactly one :class:`~matchlayer_api.services.llm.schemas.FailureReason`,
produces exactly one structured ``llm_feature_failed`` event (reason,
request id via structlog contextvars, user id, feature, prompt version —
never PII, never the API key), and lands on the feature's
Fallback_Response. Fallbacks are never persisted and never cached
(Req 9.5, 15.4). An LLM failure never surfaces as a 5xx (Req 9.1): the
only exceptions :meth:`LLMOrchestrator.prepare` raises are the deliberate
gate rejections — :class:`DailyQuotaExceededError` (429) and
:class:`SpendLimitExceededError` (503) — which the router maps onto their
RFC 7807 envelopes before any stream opens (Req 11.4).

**Two-phase API for SSE** (Req 11.4): :meth:`LLMOrchestrator.prepare` runs
every gate and returns either an immediate :class:`LLMOutcome` (reuse hit,
cache hit, pre-call fallback) or a :class:`ProviderCallPlan`;
:meth:`LLMOrchestrator.execute` performs the provider call for a plan and
never raises for an LLM failure. The SSE layer opens the stream only after
``prepare`` returned, so gate rejections are plain JSON responses;
:meth:`LLMOrchestrator.run` composes both phases for the non-streaming
path.

Design reference: phase-3-llm-layer §"Orchestrator
(services/llm/orchestrator.py)". Requirements: 1.11, 8.2, 8.3, 9.1-9.6,
13.2, 13.3, 13.8.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.llm.availability import llm_key_present
from matchlayer_api.ml.llm.client import (
    LLMClient,
    LLMCompletion,
    LLMError,
    LLMRequest,
    LLMUsage,
)
from matchlayer_api.ml.prompts.registry import ACTIVE_PROMPT_VERSIONS, LLMFeature
from matchlayer_api.services.llm.cache import LLMCache
from matchlayer_api.services.llm.invocation_log import record_invocation
from matchlayer_api.services.llm.prompting import (
    PromptRenderError,
    PromptTemplateError,
    UserContentKind,
    UserContentSection,
    build_messages,
    load_template,
    render,
)
from matchlayer_api.services.llm.quota import DailyQuota, QuotaAccountingError
from matchlayer_api.services.llm.redaction import (
    REDACTOR_VERSION,
    RedactionError,
    redact,
)
from matchlayer_api.services.llm.results import find_reusable_result, persist_result
from matchlayer_api.services.llm.schemas import FailureReason, LLMResultEnvelope
from matchlayer_api.services.llm.spend import SpendCircuitBreaker

__all__ = [
    "DailyQuotaExceededError",
    "DeltaCallback",
    "LLMFeatureSpec",
    "LLMOrchestrator",
    "LLMOutcome",
    "PromptInputs",
    "PromptSection",
    "ProviderCallPlan",
    "RedactionKind",
    "SpendLimitExceededError",
    "compute_input_hash",
]

_log = structlog.get_logger(__name__)

type RedactionKind = str
"""One of ``"resume" | "job_description" | "bullet"`` — the PII_Redactor's
``kind`` argument. Kept as ``str`` at the dataclass boundary; the literal
narrowing happens at the single :func:`redact` call site."""

type DeltaCallback = Callable[[str], Awaitable[None]]
"""Async callback receiving each raw output fragment as it streams in.

Deltas are **display-progressive only** (design D2): the authoritative
result is always the terminal validated object, never accumulated deltas.
The SSE layer relays these as ``delta`` events; the non-streaming path
passes no callback."""

_REDACTION_KINDS: frozenset[str] = frozenset({"resume", "job_description", "bullet"})


def _utc_now() -> datetime:
    """Default injected clock: timezone-aware current UTC time."""
    return datetime.now(UTC)


def _next_utc_midnight(moment: datetime) -> datetime:
    """The next 00:00:00 UTC strictly after *moment*'s calendar day.

    The instant the Daily_Quota resets, surfaced in the 429 ``detail`` so
    the caller knows when they may retry (Requirement 13.5's reset time).
    """
    next_day: date = moment.astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Gate-rejection exceptions (the only exceptions ``prepare`` raises).
# The router maps them onto their RFC 7807 envelopes — 429 with the
# X-LLM-Quota-Remaining header, 503 with the spend-limit ``type`` — before
# any SSE stream opens (Req 11.4, 13.5, 16.5).
# ---------------------------------------------------------------------------


class DailyQuotaExceededError(Exception):
    """The requester's Daily_Quota is exhausted (gate or lost reserve race).

    Maps to a 429 RFC 7807 response whose ``detail`` states the configured
    daily limit and the UTC reset instant, with ``X-LLM-Quota-Remaining``
    carrying ``remaining`` (Requirements 13.2, 13.4, 13.5). The message is
    a fixed, PII-free string built from the limit and reset time only.
    """

    def __init__(self, *, limit: int, remaining: int, resets_at: datetime) -> None:
        self.limit = limit
        self.remaining = remaining
        self.resets_at = resets_at
        super().__init__(
            f"Daily LLM quota of {limit} requests reached. Quota resets at {resets_at.isoformat()}."
        )


class SpendLimitExceededError(Exception):
    """The Spend_Circuit_Breaker is open — new provider calls are blocked.

    Maps to a 503 RFC 7807 response whose ``type`` identifies the spend
    limit; the body carries no spend figures (Requirement 14.3, design
    error table). Deliberately carries no state: everything the response
    needs is fixed copy.
    """


# ---------------------------------------------------------------------------
# Feature-spec plumbing (design D1).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSection:
    """One raw user-content region destined for the user-role message.

    ``redaction`` names the PII_Redactor ``kind`` to apply before the text
    enters the prompt, the hash, or the cache key; ``None`` marks stored,
    already-PII-free derived data (e.g. the Match_Result's skill lists,
    read verbatim per Req 5.3/7.6) that passes through unredacted.
    """

    kind: UserContentKind
    text: str
    redaction: RedactionKind | None = None


@dataclass(frozen=True)
class PromptInputs:
    """Everything a feature contributes to prompt assembly.

    ``values`` fills the system template's named ``{placeholder}`` slots
    (Req 2.6) — feature specs must never place raw PII here; PII-bearing
    text travels in ``sections``, where the orchestrator redacts it.
    ``sections`` become the delimited regions of the single user-role
    message (Req 4.1), in order.
    """

    values: Mapping[str, str]
    sections: Sequence[PromptSection]


@dataclass(frozen=True)
class LLMFeatureSpec[TInput, TResult: BaseModel]:
    """The per-feature parameterization of the shared pipeline (design D1).

    ``build_inputs`` maps the Match_Result plus the feature-specific
    request input onto :class:`PromptInputs`; ``build_fallback`` builds the
    locally-derived Fallback_Response conforming to the same ``result``
    schema (Req 9.2, 9.3). ``validate_extra`` is the optional post-schema
    check a feature adds on top of Pydantic validation (the
    Bullet_Rewriter's alignment check, Req 6.7) — it raises
    :class:`ValueError` on any deviation, which the orchestrator treats as
    a schema-validation failure. ``reuse_persisted`` opts the feature into
    the persisted-result reuse step (the Resume_Coach, design D7).
    """

    feature: LLMFeature
    result_schema: type[TResult]
    build_inputs: Callable[[MatchResult, TInput], PromptInputs]
    build_fallback: Callable[[MatchResult, TInput, FailureReason], TResult]
    validate_extra: Callable[[MatchResult, TInput, TResult], None] | None = None
    reuse_persisted: bool = False


@dataclass(frozen=True)
class LLMOutcome[TResult: BaseModel]:
    """The pipeline's terminal product for one feature request.

    ``envelope`` is what the router serializes — a validated LLM_Result or
    a Fallback_Response, distinguished solely by ``is_fallback`` (Req 9.2).
    ``quota_remaining`` feeds the ``X-LLM-Quota-Remaining`` header on every
    LLM feature response (Req 13.5); ``None`` means the counter could not
    be read (quota accounting unavailable).
    """

    envelope: LLMResultEnvelope[TResult]
    quota_remaining: int | None


@dataclass(frozen=True)
class ProviderCallPlan[TInput, TResult: BaseModel]:
    """Everything ``prepare`` resolved for an imminent provider call.

    Produced only after **all** gates passed — including the atomic quota
    reserve, so the reservation is already counted (Req 13.3) and the SSE
    layer may open the stream before calling ``execute`` (Req 11.4).
    """

    spec: LLMFeatureSpec[TInput, TResult]
    envelope_cls: type[LLMResultEnvelope[TResult]]
    user_id: UUID
    match: MatchResult
    feature_input: TInput
    request: LLMRequest
    template_version: int
    input_hash: str
    quota_remaining: int


# ---------------------------------------------------------------------------
# Canonical prompt-input hash (Req 3.8, 12.6, 15.1).
# ---------------------------------------------------------------------------


def compute_input_hash(
    *,
    feature: LLMFeature,
    template_version: int,
    model: str,
    values: Mapping[str, str],
    sections: Sequence[tuple[str, str]],
) -> str:
    """The canonical sha256 digest over the redacted prompt input.

    One digest shared by the invocation-log ``input_hash`` and the
    LLM_Cache key (Req 12.6, 15.1), computed **only** over redacted text
    (Req 3.8): the canonical byte string is
    ``feature | template_version | model | <redacted values + sections>``
    where the trailing component is compact, key-sorted JSON of the
    template placeholder values and the ordered ``(kind, redacted_text)``
    user-content sections — unambiguous (JSON escaping prevents any
    component from forging a separator) and deterministic (sorted keys,
    fixed separators, no ASCII escaping drift).
    """
    serialized = json.dumps(
        {"values": dict(values), "sections": [[kind, text] for kind, text in sections]},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    canonical = f"{feature.value}|{template_version}|{model}|{serialized}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The orchestrator.
# ---------------------------------------------------------------------------


class LLMOrchestrator:
    """The shared pipeline, constructed per request with injected deps.

    Dependencies mirror the module factories (``get_daily_quota``,
    ``get_llm_cache``, ``get_spend_circuit_breaker``): the router composes
    them per request; tests inject fakes plus a fixed clock. The
    ``client_factory`` constructs one :class:`LLMClient` per provider call
    (the OpenRouter adapter in production, a scripted fake in tests) — the
    orchestrator itself never touches provider-specific code (Req 1.1).
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        quota: DailyQuota,
        cache: LLMCache,
        breaker: SpendCircuitBreaker,
        client_factory: Callable[[], LLMClient],
        settings: Settings | None = None,
        key_present: Callable[[], bool] = llm_key_present,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        resolved = settings if settings is not None else get_settings()
        self._session = session
        self._quota = quota
        self._cache = cache
        self._breaker = breaker
        self._client_factory = client_factory
        self._model = resolved.llm_model
        self._max_output_tokens = resolved.llm_max_output_tokens
        self._daily_quota_limit = resolved.llm_daily_quota
        self._key_present = key_present
        self._clock = clock

    # ---- public API ------------------------------------------------------

    async def run[TInput, TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[TInput, TResult],
        *,
        user_id: UUID,
        match: MatchResult,
        feature_input: TInput,
        on_delta: DeltaCallback | None = None,
    ) -> LLMOutcome[TResult]:
        """Execute the full pipeline: gates, provider call, terminal event.

        The non-streaming entry point (and the composition the SSE layer
        splits into :meth:`prepare` + :meth:`execute`). Returns exactly one
        of the two success shapes — a validated LLM_Result envelope or a
        Fallback_Response envelope — and raises only the deliberate gate
        rejections (:class:`DailyQuotaExceededError`,
        :class:`SpendLimitExceededError`); an LLM failure never escapes as
        an exception (Req 9.1).

        Args:
            spec: The feature's pipeline parameterization.
            user_id: The authenticated owner (auth and ownership were
                verified by the router before this call).
            match: The owned Match_Result the feature runs against.
            feature_input: Feature-specific request input (submitted
                bullets for the rewriter; ``None`` for coach/questions).
            on_delta: Optional async relay for raw display-progressive
                output fragments (the SSE ``delta`` events).

        Returns:
            The terminal :class:`LLMOutcome`.

        Raises:
            DailyQuotaExceededError: Quota gate or reserve rejected (429).
            SpendLimitExceededError: Spend breaker open (503).
        """
        prepared = await self.prepare(
            spec, user_id=user_id, match=match, feature_input=feature_input
        )
        if isinstance(prepared, LLMOutcome):
            return prepared
        return await self.execute(prepared, on_delta=on_delta)

    async def prepare[TInput, TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[TInput, TResult],
        *,
        user_id: UUID,
        match: MatchResult,
        feature_input: TInput,
    ) -> LLMOutcome[TResult] | ProviderCallPlan[TInput, TResult]:
        """Run every pre-call pipeline stage (steps 1-9 of the module doc).

        Returns an immediate :class:`LLMOutcome` when the request resolves
        without a provider call — persisted-result reuse, cache hit, or a
        pre-call fallback (key absent, redaction failure, template
        missing, quota accounting unavailable) — or a
        :class:`ProviderCallPlan` when the call may proceed (the quota
        reservation is already counted, Req 13.3). Raises only the two
        gate rejections; the SSE layer therefore opens its stream only
        after this method returns (Req 11.4).
        """
        active_version = ACTIVE_PROMPT_VERSIONS[spec.feature]
        envelope_cls = _envelope_class(spec.result_schema)

        # 1. Daily_Quota gate — the first enforcement step after auth and
        #    ownership (Req 13.2). Read-only: never counts the request.
        try:
            gate = await self._quota.gate(str(user_id))
        except QuotaAccountingError:
            return self._fallback_outcome(
                spec,
                envelope_cls,
                user_id=user_id,
                match=match,
                feature_input=feature_input,
                reason=FailureReason.QUOTA_ACCOUNTING_UNAVAILABLE,
                quota_remaining=None,
            )
        if not gate.allowed:
            raise DailyQuotaExceededError(
                limit=self._daily_quota_limit,
                remaining=gate.remaining,
                resets_at=_next_utc_midnight(self._clock()),
            )
        remaining = gate.remaining

        # 2. Persisted-result reuse (coach only, design D7, Req 5.4): the
        #    newest stored row under the same active version + model is
        #    served with no call and no quota consumption; a version/model
        #    change simply misses (Req 5.7).
        if spec.reuse_persisted:
            reusable = await find_reusable_result(
                self._session,
                user_id=user_id,
                match_result_id=match.id,
                feature=spec.feature,
                prompt_template_version=active_version,
                llm_model=self._model,
            )
            if reusable is not None:
                envelope = envelope_cls(
                    id=str(reusable.id),
                    is_fallback=False,
                    fallback_reason=None,
                    prompt_template_version=reusable.prompt_template_version,
                    created_at=reusable.created_at,
                    result=spec.result_schema.model_validate(reusable.payload),
                )
                return LLMOutcome(envelope=envelope, quota_remaining=remaining)

        # 3. Key-present check: absent → LLM_Unavailable fallback, never a
        #    provider call and never an error (Req 1.8, 10.3).
        if not self._key_present():
            return self._fallback_outcome(
                spec,
                envelope_cls,
                user_id=user_id,
                match=match,
                feature_input=feature_input,
                reason=FailureReason.LLM_UNAVAILABLE,
                quota_remaining=remaining,
            )

        # 4. Build inputs and redact (Req 3). Unredacted text never
        #    proceeds past this point (Req 3.6).
        inputs = spec.build_inputs(match, feature_input)
        try:
            redacted_sections = [self._redact_section(section) for section in inputs.sections]
        except RedactionError:
            return self._fallback_outcome(
                spec,
                envelope_cls,
                user_id=user_id,
                match=match,
                feature_input=feature_input,
                reason=FailureReason.REDACTION_FAILED,
                quota_remaining=remaining,
            )

        # 5. Assemble the prompt (Req 2.5-2.7, 4.1): template load, exact
        #    placeholder substitution, delimited message construction. A
        #    missing template or unfilled placeholder means nothing is
        #    transmitted — fallback (design error table maps both onto
        #    prompt_template_missing).
        try:
            template = load_template(spec.feature)
            rendered = render(template, inputs.values)
        except (PromptTemplateError, PromptRenderError):
            return self._fallback_outcome(
                spec,
                envelope_cls,
                user_id=user_id,
                match=match,
                feature_input=feature_input,
                reason=FailureReason.PROMPT_TEMPLATE_MISSING,
                quota_remaining=remaining,
            )
        messages = build_messages(rendered, redacted_sections)

        # 6. Canonical prompt-input hash over the redacted values (Req 3.8):
        #    the same digest keys the cache and lands in the invocation log
        #    (Req 12.6, 15.1).
        input_hash = compute_input_hash(
            feature=spec.feature,
            template_version=template.version,
            model=self._model,
            values=inputs.values,
            sections=[(section.kind, section.text) for section in redacted_sections],
        )

        # 7. Cache lookup — a hit is served with no provider call, no
        #    invocation-log row, and no quota consumption (Req 15.2); any
        #    lookup failure was already degraded to a miss by the cache.
        cached = await self._cache.get(
            user_id=str(user_id),
            feature=spec.feature.value,
            input_hash=input_hash,
            template_version=template.version,
            model=self._model,
            envelope_type=envelope_cls,
        )
        if cached is not None:
            return LLMOutcome(envelope=cached, quota_remaining=remaining)

        # 8. Spend_Circuit_Breaker evaluation before the call (Req 14.2):
        #    open → 503, no call (Req 14.3). A read failure inside evaluate
        #    already forced the state open (fail-safe, Req 14.7).
        breaker_state = await self._breaker.evaluate(self._session)
        if breaker_state.is_open:
            raise SpendLimitExceededError(
                "AI features are temporarily disabled. Please try again later."
            )

        # 9. Atomic quota reserve at provider-call initiation (Req 13.3,
        #    13.7). A lost concurrency race gets the same 429; a Redis
        #    failure takes the fallback path with no call (Req 13.8).
        try:
            reservation = await self._quota.reserve(str(user_id))
        except QuotaAccountingError:
            return self._fallback_outcome(
                spec,
                envelope_cls,
                user_id=user_id,
                match=match,
                feature_input=feature_input,
                reason=FailureReason.QUOTA_ACCOUNTING_UNAVAILABLE,
                quota_remaining=remaining,
            )
        if not reservation.allowed:
            raise DailyQuotaExceededError(
                limit=self._daily_quota_limit,
                remaining=reservation.remaining,
                resets_at=_next_utc_midnight(self._clock()),
            )

        request = LLMRequest(
            messages=messages,
            output_schema=spec.result_schema.model_json_schema(),
            max_output_tokens=self._max_output_tokens,
        )
        return ProviderCallPlan(
            spec=spec,
            envelope_cls=envelope_cls,
            user_id=user_id,
            match=match,
            feature_input=feature_input,
            request=request,
            template_version=template.version,
            input_hash=input_hash,
            quota_remaining=reservation.remaining,
        )

    async def execute[TInput, TResult: BaseModel](
        self,
        plan: ProviderCallPlan[TInput, TResult],
        *,
        on_delta: DeltaCallback | None = None,
    ) -> LLMOutcome[TResult]:
        """Perform the provider call for *plan* (steps 10-12).

        Exactly one attempt (Req 1.12, 9.6). On success: persist the
        validated LLM_Result, best-effort cache write, one invocation-log
        row, breaker re-evaluation. On any LLM failure (provider error,
        timeout, schema validation): one invocation-log row with the
        failure category, then the fallback — never an exception, never a
        5xx (Req 9.1). The already-counted quota reservation stays counted
        either way (Req 13.3).
        """
        spec = plan.spec
        client = self._client_factory()

        failure: FailureReason | None = None
        try:
            async for chunk in client.stream(plan.request):
                if on_delta is not None:
                    await on_delta(chunk.delta)
        except LLMError as exc:
            failure = (
                FailureReason.TIMEOUT if exc.category == "timeout" else FailureReason.PROVIDER_ERROR
            )
        completion = await self._completion_of(client)

        # Terminal validation (Req 8.2): the accumulated raw text is parsed
        # and validated against the feature's schema, plus the feature's
        # optional extra check (bullet alignment, Req 6.7). Any violation —
        # malformed JSON, truncation, bound violation, misalignment — is a
        # schema-validation failure; content is never repaired or truncated
        # (Req 8.3, 7.7). pydantic.ValidationError subclasses ValueError.
        result_obj: TResult | None = None
        if failure is None:
            try:
                result_obj = spec.result_schema.model_validate_json(completion.text)
                if spec.validate_extra is not None:
                    spec.validate_extra(plan.match, plan.feature_input, result_obj)
            except ValueError:
                result_obj = None
                failure = FailureReason.SCHEMA_VALIDATION_FAILED

        if result_obj is not None:
            return await self._complete_success(plan, result_obj, completion)

        # One invocation-log row for the failed call (Req 12.1), then the
        # fallback (Req 9.1). The reserved quota count stays (Req 13.3).
        assert failure is not None  # exactly one of result_obj/failure holds
        await self._record_call(plan, completion, output=None, failure_category=failure)
        return self._fallback_outcome(
            spec,
            plan.envelope_cls,
            user_id=plan.user_id,
            match=plan.match,
            feature_input=plan.feature_input,
            reason=failure,
            quota_remaining=plan.quota_remaining,
        )

    # ---- internals -------------------------------------------------------

    async def _complete_success[TInput, TResult: BaseModel](
        self,
        plan: ProviderCallPlan[TInput, TResult],
        result_obj: TResult,
        completion: LLMCompletion,
    ) -> LLMOutcome[TResult]:
        """Persist → cache write → invocation log → breaker re-evaluation."""
        payload = result_obj.model_dump(mode="json")
        row = await persist_result(
            self._session,
            user_id=plan.user_id,
            match_result_id=plan.match.id,
            feature=plan.spec.feature,
            prompt_template_version=plan.template_version,
            llm_model=self._model,
            payload=payload,
        )
        envelope = plan.envelope_cls(
            id=str(row.id),
            is_fallback=False,
            fallback_reason=None,
            prompt_template_version=row.prompt_template_version,
            created_at=row.created_at,
            result=result_obj,
        )
        # Best-effort cache write: a failure is logged by the cache and the
        # result is still returned (Req 15.8). Only validated, non-fallback
        # envelopes ever reach this call (Req 15.4).
        await self._cache.set(
            user_id=str(plan.user_id),
            feature=plan.spec.feature.value,
            input_hash=plan.input_hash,
            template_version=plan.template_version,
            model=self._model,
            envelope=envelope,
        )
        await self._record_call(plan, completion, output=payload, failure_category=None)
        return LLMOutcome(envelope=envelope, quota_remaining=plan.quota_remaining)

    async def _record_call[TInput, TResult: BaseModel](
        self,
        plan: ProviderCallPlan[TInput, TResult],
        completion: LLMCompletion,
        *,
        output: dict[str, Any] | None,
        failure_category: FailureReason | None,
    ) -> None:
        """Write the one invocation-log row, then update the breaker.

        Exactly one row per provider call, success or failure (Req 12.1).
        The write is best-effort (Req 12.5): a failed persist never fails
        the request, but forces the breaker open because the call's cost
        never entered the tracked spend (Req 14.8); a successful persist is
        followed by the post-persist breaker evaluation (Req 14.2).
        """
        logged = await record_invocation(
            self._session,
            user_id=plan.user_id,
            match_result_id=plan.match.id,
            feature=plan.spec.feature,
            prompt_template_version=plan.template_version,
            llm_model=self._model,
            redactor_version=REDACTOR_VERSION,
            input_hash=plan.input_hash,
            latency_ms=completion.latency_ms,
            usage=completion.usage,
            output=output,
            failure_category=failure_category,
        )
        if logged:
            await self._breaker.evaluate(self._session)
        else:
            self._breaker.record_persist_failure()

    def _fallback_outcome[TInput, TResult: BaseModel](
        self,
        spec: LLMFeatureSpec[TInput, TResult],
        envelope_cls: type[LLMResultEnvelope[TResult]],
        *,
        user_id: UUID,
        match: MatchResult,
        feature_input: TInput,
        reason: FailureReason,
        quota_remaining: int | None,
    ) -> LLMOutcome[TResult]:
        """Map one failure onto one event and one Fallback_Response (Req 9).

        The single place a failure event is emitted, so no path produces
        zero or two events (Req 9.4). The event carries the failure-reason
        enum value, the feature, the registry-active prompt version, and
        the user id; the request id arrives via the structlog contextvars
        bound by the request-id middleware. Never PII, never prompt
        content, never the API key. The fallback is built entirely from
        data already inside MatchLayer, conforms to the same ``result``
        schema, and is never persisted and never cached (Req 9.2, 9.3,
        9.5, 15.4).
        """
        _log.warning(
            "llm_feature_failed",
            reason=reason.value,
            feature=spec.feature.value,
            prompt_template_version=ACTIVE_PROMPT_VERSIONS[spec.feature],
            user_id=str(user_id),
        )
        fallback = spec.build_fallback(match, feature_input, reason)
        envelope = envelope_cls(
            id=None,
            is_fallback=True,
            fallback_reason=reason,
            prompt_template_version=None,
            created_at=None,
            result=fallback,
        )
        return LLMOutcome(envelope=envelope, quota_remaining=quota_remaining)

    @staticmethod
    def _redact_section(section: PromptSection) -> UserContentSection:
        """Redact one section per its declared kind (Req 3), or pass through.

        Sections carrying ``redaction=None`` hold stored, already-PII-free
        derived data (skill lists, suggestions) read verbatim (Req 5.3).
        An unknown redaction kind is a programming error in the feature
        spec, surfaced as :class:`RedactionError` so the request degrades
        to the fallback rather than transmitting unredacted text.
        """
        if section.redaction is None:
            return UserContentSection(kind=section.kind, text=section.text)
        if section.redaction not in _REDACTION_KINDS:
            raise RedactionError("unknown redaction kind on prompt section")
        kind = cast('Literal["resume", "job_description", "bullet"]', section.redaction)
        redacted = redact(section.text, kind=kind)
        return UserContentSection(kind=section.kind, text=redacted.text)

    @staticmethod
    async def _completion_of(client: LLMClient) -> LLMCompletion:
        """Fetch the accumulated completion, degrading to an empty one.

        The adapter records its completion in a ``finally`` block, so
        ``result()`` is normally available even after a failure or abort.
        A client that cannot produce one (a misbehaving fake, a failure
        before the stream started) must still yield an invocation-log row
        for the initiated call (Req 12.1), so an empty completion with
        unavailable usage stands in (``None`` cost — distinct from zero,
        Req 12.2).
        """
        try:
            return await client.result()
        except Exception:
            return LLMCompletion(
                text="",
                usage=LLMUsage(
                    input_tokens=None,
                    output_tokens=None,
                    cost_usd=None,
                    cost_basis="unavailable",
                ),
                latency_ms=0,
            )


def _envelope_class[TResult: BaseModel](
    result_schema: type[TResult],
) -> type[LLMResultEnvelope[TResult]]:
    """The concretely parameterized envelope model for a result schema.

    Parameterizing at runtime (``LLMResultEnvelope[CoachingReport]``) makes
    Pydantic serialize ``result`` with the concrete schema's fields — the
    unparameterized generic would serialize against the ``BaseModel`` bound
    and drop everything. The cast bridges the runtime parameterization to
    the static type, which mypy cannot express for a runtime class object.
    """
    return cast(
        "type[LLMResultEnvelope[TResult]]",
        LLMResultEnvelope[result_schema],  # type: ignore[valid-type]
    )
