"""LLM feature sub-resource routers: ``/api/v1/matches/{match_id}/...``.

Pure HTTP-shape concerns only (Components and Interfaces import-boundary
rule) — every pipeline stage (quota, redaction, caching, spend control,
schema validation, invocation logging) lives in
:mod:`matchlayer_api.services.llm.orchestrator`, and every query goes
through :mod:`matchlayer_api.services.llm.results`.

The three sub-resources (Requirement 16.1; design "Routers and SSE"):

====== ================================================== ==================
Method Path                                               Purpose
====== ================================================== ==================
POST   ``.../coaching-reports``                           Generate (or reuse) a Coaching_Report
GET    ``.../coaching-reports?limit=&cursor=``            List, newest-first
GET    ``.../coaching-reports/{result_id}``               Read one
POST   ``.../bullet-rewrites``                            Rewrite submitted bullets
GET    ``.../bullet-rewrites?limit=&cursor=``             List, newest-first
GET    ``.../bullet-rewrites/{result_id}``                Read one
POST   ``.../interview-question-sets``                    Generate a question set
GET    ``.../interview-question-sets?limit=&cursor=``     List, newest-first
GET    ``.../interview-question-sets/{result_id}``        Read one
====== ================================================== ==================

Contract highlights (Requirements 5.6, 6.3, 10.3, 13.5, 16.2, 16.4, 16.5,
16.8-16.10):

* **401 before any existence check** (Req 16.8): every route resolves the
  principal via :func:`~matchlayer_api.core.dependencies.get_current_user`
  before its handler body runs, so an unauthenticated request gets the
  401 ``unauthenticated`` envelope without touching the database.
* **Identical 404 for "not yours" and "not found"** (Req 5.6, 16.2):
  match ownership goes through ``Scoring_Service.get_match`` (the same
  lookup — and therefore the byte-identical ``not_found`` envelope — the
  matches router uses); result lookups return ``None`` uniformly for
  missing / other-owner / other-match / other-feature rows.
* **Cursor pagination** (Req 16.4, 16.10): ``limit`` 1..100 default 20
  validated by FastAPI Query bounds (out-of-bounds → 422
  ``validation_error``); the opaque base64 ``(created_at, id)`` cursor is
  decoded by ``services/llm/results.py``, which raises the 422 envelope
  on a malformed value. Descending ``created_at`` (UUIDv7) order.
* **``X-LLM-Quota-Remaining`` on every LLM feature response** (Req 13.5),
  including the 429: successes carry the pipeline's post-gate/post-reserve
  remaining; GETs and the 503 read the counter via the read-only gate
  (best-effort — an unreadable counter omits the header rather than
  failing the request); the 429 carries the rejection's own remaining.
* **429 Daily_Quota** (Req 13.2, 16.5): RFC 7807, ``detail`` states the
  configured daily limit and the next 00:00:00 UTC reset instant.
* **503 spend limit** (Req 10.3): RFC 7807 whose ``type``
  (``llm_spend_limit_reached``) identifies the spend limit; ``detail`` is
  user-safe fixed copy with no spend figures, key material, or provider
  account details.
* **RFC 7807 everywhere** (Req 16.5): pre-call gate rejections are built
  here in the canonical envelope shape (they must carry the quota header,
  which the shared exception handlers cannot); every other error path
  (401/404/422) flows through the registered foundation handlers.
* ``X-Robots-Tag: noindex, nofollow`` lands on every response via the
  app-level ``ApiNoIndexMiddleware`` (Req 16.7) — nothing to do here.

Streaming negotiation (design D3, Req 11.1): each POST takes a ``stream``
query parameter — explicit in the OpenAPI schema so it survives codegen.
Omitted/false returns the pipeline's terminal outcome — a validated
LLM_Result or a Fallback_Response — as the single 200 JSON body (Req 9.1);
``stream=true`` delivers it as the SSE stream built by ``sse.py``. Either
way the gates run first: ``prepare`` raises the 429/503 rejections before
any stream opens, so a rejected streaming request gets the same plain
RFC 7807 response as its non-streaming twin (Req 11.4).

Transaction model (mirrors the matches router): the orchestrator stages
LLM_Result and invocation-log rows and never commits; the router commits
after ``run()`` so the result row and its log row land together. Gate
rejections stage nothing, so no commit happens on those paths.

PRIVACY (``security.md``): resume text, JD text, bullet content, and
result payloads are Restricted-derived; this module never logs any of
them, and the error ``detail`` strings are fixed, PII-free copy.

Design reference: phase-3-llm-layer §"Routers and SSE (api/matches/llm/)".
Requirements: 5.6, 6.3, 10.3, 13.5, 16.1, 16.2, 16.4, 16.5, 16.8, 16.9,
16.10.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.api.matches.llm.schemas import (
    BulletRewriteEnvelope,
    BulletRewriteListResponse,
    BulletRewriteRequest,
    CoachingReportEnvelope,
    CoachingReportListResponse,
    InterviewQuestionSetEnvelope,
    InterviewQuestionSetListResponse,
)
from matchlayer_api.api.matches.llm.sse import llm_stream_response
from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import get_current_user
from matchlayer_api.core.errors import NotFoundError
from matchlayer_api.db.models import LLMResult, MatchResult, Resume, User
from matchlayer_api.ml.llm.availability import build_llm_client
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.bullets import BULLET_REWRITE_SPEC, BulletRewriteInput
from matchlayer_api.services.llm.cache import LLMCache, get_llm_cache
from matchlayer_api.services.llm.coach import RESUME_COACH_SPEC, ResumeCoachInput
from matchlayer_api.services.llm.orchestrator import (
    DailyQuotaExceededError,
    LLMFeatureSpec,
    LLMOrchestrator,
    SpendLimitExceededError,
)
from matchlayer_api.services.llm.questions import (
    INTERVIEW_QUESTIONS_SPEC,
    InterviewQuestionsInput,
)
from matchlayer_api.services.llm.quota import DailyQuota, QuotaAccountingError, get_daily_quota
from matchlayer_api.services.llm.results import get_result, list_results
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    CoachingReport,
    InterviewQuestionSet,
    LLMResultEnvelope,
)
from matchlayer_api.services.llm.spend import SpendCircuitBreaker, get_spend_circuit_breaker
from matchlayer_api.services.matching import Scoring_Service

_log = structlog.get_logger(__name__)

__all__ = ["router"]

# ---------------------------------------------------------------------------
# Reusable Annotated dependency aliases (module scope avoids ruff B008 while
# keeping the FastAPI dependency-injection contract identical — mirrors
# ``api/matches/router.py``).
# ---------------------------------------------------------------------------
_SessionDep = Annotated[AsyncSession, Depends(get_session)]
_CurrentUser = Annotated[User, Depends(get_current_user)]
_QuotaDep = Annotated[DailyQuota, Depends(get_daily_quota)]
_CacheDep = Annotated[LLMCache, Depends(get_llm_cache)]
_BreakerDep = Annotated[SpendCircuitBreaker, Depends(get_spend_circuit_breaker)]

# Cursor-pagination ``limit`` bounds (Requirements 16.4, 16.10;
# ``conventions.md`` "Pagination"). A value outside 1..100 fails FastAPI's
# query validation and surfaces as 422 ``validation_error``.
_DEFAULT_LIST_LIMIT: Final[int] = 20
_MIN_LIST_LIMIT: Final[int] = 1
_MAX_LIST_LIMIT: Final[int] = 100

# The single consistent location for the remaining Daily_Quota count on
# every LLM feature response, 429s included (Requirement 13.5).
_QUOTA_HEADER: Final[str] = "X-LLM-Quota-Remaining"

# The single streaming-negotiation mechanism (design D3, Req 11.1): a
# query parameter, explicit in the OpenAPI schema so both response modes
# stay documented on one operation and the flag survives codegen into the
# generated TS client.
_StreamParam = Annotated[
    bool,
    Query(
        description="When true, deliver the response as a Server-Sent "
        "Events stream (`text/event-stream`): zero or more `delta` events "
        "carrying incremental display text, then exactly one terminal "
        "event — `complete` (the validated result envelope), `degraded` "
        "(the fallback envelope), or `error` (an RFC 7807 body). "
        "Pre-stream gate rejections (401/404/422/429/503) return the same "
        "non-streaming RFC 7807 responses. When false or omitted, the "
        "documented JSON response body is returned.",
    ),
]

# OpenAPI documentation of the quota header on the success response
# (Requirement 13.5: "documented in the OpenAPI schema").
_QUOTA_HEADER_DOC: Final[dict[str, Any]] = {
    _QUOTA_HEADER: {
        "description": "The requesting user's remaining LLM Daily_Quota for "
        "the current UTC day. Present on every LLM feature response, "
        "including 429 rejections; omitted only when the quota counter is "
        "unreadable.",
        "schema": {"type": "string"},
    }
}
_LLM_FEATURE_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {"headers": _QUOTA_HEADER_DOC},
}


# ---------------------------------------------------------------------------
# RFC 7807 gate-rejection responses.
#
# The 429 and 503 are built here (not via registered exception handlers)
# because they must carry the ``X-LLM-Quota-Remaining`` header, which only
# the router knows (Requirement 13.5). The envelope shape is byte-compatible
# with ``core/errors.py``'s ``_problem_response``: type / title / detail /
# status / request_id, the ``request_id`` sourced from the same structlog
# contextvar the request-id middleware binds.
# ---------------------------------------------------------------------------


def _current_request_id() -> str | None:
    """The request_id bound by ``RequestIdMiddleware``, or ``None``."""
    value = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(value, str):
        return value
    return None


def _problem_response(
    *,
    type_: str,
    title: str,
    detail: str,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a JSONResponse with the canonical RFC 7807 envelope (Req 16.5)."""
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "detail": detail,
        "status": status_code,
        "request_id": _current_request_id(),
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def _quota_exceeded_response(exc: DailyQuotaExceededError) -> JSONResponse:
    """The 429 Daily_Quota rejection (Requirements 13.2, 13.5, 16.5).

    ``detail`` states the configured daily limit and the UTC reset instant,
    rebuilt here from the exception's structured fields (``limit`` /
    ``resets_at``) rather than ``str(exc)`` so no exception text — however
    fixed — ever flows into a response body (security.md: no exception
    details in error responses; CodeQL py/stack-trace-exposure). The
    response carries the requester's remaining count in
    ``X-LLM-Quota-Remaining``.
    """
    detail = (
        f"Daily LLM quota of {exc.limit} requests reached. "
        f"Quota resets at {exc.resets_at.isoformat()}."
    )
    return _problem_response(
        type_="llm_quota_exceeded",
        title="LLM Daily Quota Exceeded",
        detail=detail,
        status_code=429,
        headers={_QUOTA_HEADER: str(exc.remaining)},
    )


def _spend_limit_response(quota_remaining: int | None) -> JSONResponse:
    """The 503 Spend_Circuit_Breaker rejection (Requirement 10.3).

    The ``type`` identifies the spend limit as the cause; the ``detail`` is
    fixed user-safe copy carrying no spend figures, key material, or
    provider account details.
    """
    headers = {_QUOTA_HEADER: str(quota_remaining)} if quota_remaining is not None else None
    return _problem_response(
        type_="llm_spend_limit_reached",
        title="AI Features Temporarily Disabled",
        detail="AI features are temporarily disabled. Please try again later.",
        status_code=503,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Quota-header helpers (Requirement 13.5).
# ---------------------------------------------------------------------------


def _set_quota_header(response: Response, remaining: int | None) -> None:
    """Set ``X-LLM-Quota-Remaining`` when the count is known.

    ``None`` means the counter could not be read (quota accounting
    unavailable) — the header is omitted rather than fabricated.
    """
    if remaining is not None:
        response.headers[_QUOTA_HEADER] = str(remaining)


async def _read_quota_remaining(quota: DailyQuota, user_id: UUID) -> int | None:
    """Best-effort read of the remaining Daily_Quota for the header.

    Uses the read-only gate (never counts the request, Requirement 13.3);
    an unreadable counter yields ``None`` — a header can never turn a
    successful GET into a failure.
    """
    try:
        return (await quota.gate(str(user_id))).remaining
    except QuotaAccountingError:
        return None


# ---------------------------------------------------------------------------
# Lookup helpers.
# ---------------------------------------------------------------------------


async def _load_owned_match(
    session: AsyncSession, *, user_id: UUID, raw_match_id: str
) -> MatchResult:
    """Resolve the owned, non-deleted Match_Result or raise the single 404.

    Delegates to ``Scoring_Service.get_match`` — the same lookup (and
    therefore the byte-identical ``not_found`` envelope) the matches router
    uses, so "not yours", "not found", and a malformed id are mutually
    indistinguishable (Requirements 5.6, 16.2).
    """
    try:
        parsed = UUID(raw_match_id)
    except ValueError as exc:
        raise NotFoundError("Match not found.") from exc
    return await Scoring_Service().get_match(session, user_id=user_id, match_id=parsed)


async def _load_resume_text(session: AsyncSession, *, user_id: UUID, resume_id: UUID) -> str:
    """Load the match's Resume ``extracted_text`` for the prompt input.

    Scoped to the owning user; a missing row or a null ``extracted_text``
    degrades to an empty string (mirroring ``services/matching.py``) so the
    feature still runs against the JD + stored skill lists rather than
    failing. The text is Restricted PII — never logged here; the
    orchestrator redacts it before it enters the prompt, the hash, or the
    cache key (Requirement 3.1).
    """
    result = await session.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id)
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        return ""
    return resume.extracted_text or ""


async def _get_owned_result(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_result_id: UUID,
    feature: LLMFeature,
    raw_result_id: str,
) -> LLMResult:
    """Resolve one owned LLM_Result row or raise the single 404.

    A malformed id, a missing row, another user's row, another match's
    row, and another feature's row all yield the identical ``not_found``
    envelope (Requirements 16.2, 16.9).
    """
    try:
        parsed = UUID(raw_result_id)
    except ValueError as exc:
        raise NotFoundError("LLM result not found.") from exc
    row = await get_result(
        session,
        user_id=user_id,
        match_result_id=match_result_id,
        feature=feature,
        result_id=parsed,
    )
    if row is None:
        raise NotFoundError("LLM result not found.")
    return row


# ---------------------------------------------------------------------------
# Response projection.
# ---------------------------------------------------------------------------


def _stored_envelope[TResult: BaseModel](
    row: LLMResult, schema: type[TResult]
) -> LLMResultEnvelope[TResult]:
    """Project a persisted ``llm_results`` row onto its response envelope.

    Every stored row is a validated LLM output (fallbacks are never
    persisted, Requirement 9.5), so the envelope carries
    ``is_fallback=False`` with the persisted id, prompt version, and
    ``created_at`` (Requirements 16.3, 17.9). The runtime
    parameterization mirrors the orchestrator's ``_envelope_class`` —
    Pydantic caches it, so this is the same concrete class the response
    models in ``schemas.py`` alias.
    """
    envelope_cls = cast(
        "type[LLMResultEnvelope[TResult]]",
        LLMResultEnvelope[schema],  # type: ignore[valid-type]
    )
    return envelope_cls(
        id=str(row.id),
        is_fallback=False,
        fallback_reason=None,
        prompt_template_version=row.prompt_template_version,
        created_at=row.created_at,
        result=schema.model_validate(row.payload),
    )


# ---------------------------------------------------------------------------
# Pipeline composition.
# ---------------------------------------------------------------------------


def _build_orchestrator(
    *,
    session: AsyncSession,
    quota: DailyQuota,
    cache: LLMCache,
    breaker: SpendCircuitBreaker,
) -> LLMOrchestrator:
    """Compose the per-request orchestrator from the injected dependencies.

    ``build_llm_client`` is the provider-neutral factory from the
    ``ml/llm/availability.py`` composition root — this router never
    references provider-specific code (Requirement 1.1).
    """
    return LLMOrchestrator(
        session=session,
        quota=quota,
        cache=cache,
        breaker=breaker,
        client_factory=build_llm_client,
    )


async def _run_llm_feature[TInput, TResult: BaseModel](
    orchestrator: LLMOrchestrator,
    spec: LLMFeatureSpec[TInput, TResult],
    *,
    session: AsyncSession,
    response: Response,
    quota: DailyQuota,
    user_id: UUID,
    match: MatchResult,
    feature_input: TInput,
) -> LLMResultEnvelope[TResult] | JSONResponse:
    """Run the shared pipeline and map its outcome onto the HTTP contract.

    Exactly three outcomes (design §"Request pipeline"):

    * a 200 body — the validated LLM_Result envelope or the feature's
      Fallback_Response envelope, distinguished only by ``is_fallback``
      (Req 9.1, 9.2) — with the quota header from the pipeline's own
      post-gate/post-reserve remaining;
    * the 429 Daily_Quota rejection (gate or lost reserve race, Req 13.2);
    * the 503 spend-limit rejection (Req 10.3).

    The commit after ``run()`` lands the staged LLM_Result and
    invocation-log rows in one transaction (the router owns the commit;
    the services never commit). Gate rejections stage nothing.
    """
    try:
        outcome = await orchestrator.run(
            spec, user_id=user_id, match=match, feature_input=feature_input
        )
    except DailyQuotaExceededError as exc:
        return _quota_exceeded_response(exc)
    except SpendLimitExceededError:
        return _spend_limit_response(await _read_quota_remaining(quota, user_id))
    await session.commit()
    _set_quota_header(response, outcome.quota_remaining)
    return outcome.envelope


async def _run_llm_feature_stream[TInput, TResult: BaseModel](
    orchestrator: LLMOrchestrator,
    spec: LLMFeatureSpec[TInput, TResult],
    *,
    session: AsyncSession,
    quota: DailyQuota,
    user_id: UUID,
    match: MatchResult,
    feature_input: TInput,
) -> Response:
    """The ``stream=true`` path: gates first, then the SSE response.

    Every gate runs inside :meth:`LLMOrchestrator.prepare` **before** any
    stream opens (Req 11.4): a gate rejection returns the identical plain
    RFC 7807 response the non-streaming path produces — the 429 with the
    quota header, the 503 spend-limit envelope — and never an opened
    stream. A ``prepare`` product (immediate outcome or provider-call
    plan) becomes the ``text/event-stream`` response built by ``sse.py``,
    which drives the provider call, relays ``delta`` events, commits the
    staged rows, and emits exactly one terminal event (Req 11.2, 11.3).
    """
    try:
        prepared = await orchestrator.prepare(
            spec, user_id=user_id, match=match, feature_input=feature_input
        )
    except DailyQuotaExceededError as exc:
        return _quota_exceeded_response(exc)
    except SpendLimitExceededError:
        return _spend_limit_response(await _read_quota_remaining(quota, user_id))
    return llm_stream_response(orchestrator, prepared, session=session)


# ---------------------------------------------------------------------------
# The three sub-resource routers (Requirement 16.1). Each carries its full
# plural-kebab-case path prefix; the aggregate ``router`` below is what
# ``main.py`` mounts.
# ---------------------------------------------------------------------------

coaching_reports_router = APIRouter(
    prefix="/api/v1/matches/{match_id}/coaching-reports", tags=["llm"]
)
bullet_rewrites_router = APIRouter(
    prefix="/api/v1/matches/{match_id}/bullet-rewrites", tags=["llm"]
)
interview_question_sets_router = APIRouter(
    prefix="/api/v1/matches/{match_id}/interview-question-sets", tags=["llm"]
)


# ---------------------------------------------------------------------------
# coaching-reports (Requirements 5.1-5.7, 16.1).
# ---------------------------------------------------------------------------


@coaching_reports_router.post(
    "",
    response_model=CoachingReportEnvelope,
    responses=_LLM_FEATURE_RESPONSES,
)
async def create_coaching_report(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
    cache: _CacheDep,
    breaker: _BreakerDep,
    stream: _StreamParam = False,
) -> CoachingReportEnvelope | Response:
    """Generate (or reuse) a Coaching_Report for an owned match (Req 5.1).

    Runs the shared pipeline with the Resume_Coach spec: persisted-result
    reuse under the same active prompt version + model serves the stored
    report with no provider call and no quota consumption (Req 5.4); any
    LLM failure lands on the locally-derived fallback with 200 (Req 5.5,
    9.1). ``stream=true`` delivers the same outcome over SSE (Req 11.1).
    """
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    resume_text = await _load_resume_text(session, user_id=user.id, resume_id=match.resume_id)
    orchestrator = _build_orchestrator(session=session, quota=quota, cache=cache, breaker=breaker)
    feature_input = ResumeCoachInput(resume_text=resume_text)
    if stream:
        return await _run_llm_feature_stream(
            orchestrator,
            RESUME_COACH_SPEC,
            session=session,
            quota=quota,
            user_id=user.id,
            match=match,
            feature_input=feature_input,
        )
    return await _run_llm_feature(
        orchestrator,
        RESUME_COACH_SPEC,
        session=session,
        response=response,
        quota=quota,
        user_id=user.id,
        match=match,
        feature_input=feature_input,
    )


@coaching_reports_router.get(
    "",
    response_model=CoachingReportListResponse,
    responses=_LLM_FEATURE_RESPONSES,
)
async def list_coaching_reports(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
    limit: Annotated[int, Query(ge=_MIN_LIST_LIMIT, le=_MAX_LIST_LIMIT)] = _DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> CoachingReportListResponse:
    """One newest-first page of the match's Coaching_Reports (Req 16.4)."""
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    page = await list_results(
        session,
        user_id=user.id,
        match_result_id=match.id,
        feature=LLMFeature.RESUME_COACH,
        limit=limit,
        cursor=cursor,
    )
    _set_quota_header(response, await _read_quota_remaining(quota, user.id))
    return CoachingReportListResponse(
        items=[_stored_envelope(row, CoachingReport) for row in page.items],
        next_cursor=page.next_cursor,
    )


@coaching_reports_router.get(
    "/{result_id}",
    response_model=CoachingReportEnvelope,
    responses=_LLM_FEATURE_RESPONSES,
)
async def get_coaching_report(
    match_id: str,
    result_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
) -> CoachingReportEnvelope:
    """One persisted Coaching_Report by id (Req 16.3, 16.9)."""
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    row = await _get_owned_result(
        session,
        user_id=user.id,
        match_result_id=match.id,
        feature=LLMFeature.RESUME_COACH,
        raw_result_id=result_id,
    )
    _set_quota_header(response, await _read_quota_remaining(quota, user.id))
    return _stored_envelope(row, CoachingReport)


# ---------------------------------------------------------------------------
# bullet-rewrites (Requirements 6.1-6.7, 16.1).
# ---------------------------------------------------------------------------


@bullet_rewrites_router.post(
    "",
    response_model=BulletRewriteEnvelope,
    responses=_LLM_FEATURE_RESPONSES,
)
async def create_bullet_rewrite(
    match_id: str,
    body: BulletRewriteRequest,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
    cache: _CacheDep,
    breaker: _BreakerDep,
    stream: _StreamParam = False,
) -> BulletRewriteEnvelope | Response:
    """Rewrite the submitted bullets against an owned match (Req 6.1).

    ``BulletRewriteRequest`` validation (count 1..``llm_max_bullets``, no
    empty/whitespace bullet, each ≤ ``llm_max_bullet_chars``) runs before
    this handler; a violation is a 422 RFC 7807 response before any
    redaction, quota accounting, or LLM work — and before any stream
    opens (Req 6.3, 11.4). Bullets are Restricted PII and travel only
    into the pipeline, which redacts them. ``stream=true`` delivers the
    outcome over SSE (Req 11.1).
    """
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    orchestrator = _build_orchestrator(session=session, quota=quota, cache=cache, breaker=breaker)
    feature_input = BulletRewriteInput(bullets=tuple(body.bullets))
    if stream:
        return await _run_llm_feature_stream(
            orchestrator,
            BULLET_REWRITE_SPEC,
            session=session,
            quota=quota,
            user_id=user.id,
            match=match,
            feature_input=feature_input,
        )
    return await _run_llm_feature(
        orchestrator,
        BULLET_REWRITE_SPEC,
        session=session,
        response=response,
        quota=quota,
        user_id=user.id,
        match=match,
        feature_input=feature_input,
    )


@bullet_rewrites_router.get(
    "",
    response_model=BulletRewriteListResponse,
    responses=_LLM_FEATURE_RESPONSES,
)
async def list_bullet_rewrites(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
    limit: Annotated[int, Query(ge=_MIN_LIST_LIMIT, le=_MAX_LIST_LIMIT)] = _DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> BulletRewriteListResponse:
    """One newest-first page of the match's Bullet_Rewrites (Req 16.4)."""
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    page = await list_results(
        session,
        user_id=user.id,
        match_result_id=match.id,
        feature=LLMFeature.BULLET_REWRITE,
        limit=limit,
        cursor=cursor,
    )
    _set_quota_header(response, await _read_quota_remaining(quota, user.id))
    return BulletRewriteListResponse(
        items=[_stored_envelope(row, BulletRewrite) for row in page.items],
        next_cursor=page.next_cursor,
    )


@bullet_rewrites_router.get(
    "/{result_id}",
    response_model=BulletRewriteEnvelope,
    responses=_LLM_FEATURE_RESPONSES,
)
async def get_bullet_rewrite(
    match_id: str,
    result_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
) -> BulletRewriteEnvelope:
    """One persisted Bullet_Rewrite by id (Req 6.5, 16.3, 16.9)."""
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    row = await _get_owned_result(
        session,
        user_id=user.id,
        match_result_id=match.id,
        feature=LLMFeature.BULLET_REWRITE,
        raw_result_id=result_id,
    )
    _set_quota_header(response, await _read_quota_remaining(quota, user.id))
    return _stored_envelope(row, BulletRewrite)


# ---------------------------------------------------------------------------
# interview-question-sets (Requirements 7.1-7.7, 16.1).
# ---------------------------------------------------------------------------


@interview_question_sets_router.post(
    "",
    response_model=InterviewQuestionSetEnvelope,
    responses=_LLM_FEATURE_RESPONSES,
)
async def create_interview_question_set(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
    cache: _CacheDep,
    breaker: _BreakerDep,
    stream: _StreamParam = False,
) -> InterviewQuestionSetEnvelope | Response:
    """Generate an Interview_Question_Set for an owned match (Req 7.1).

    ``stream=true`` delivers the outcome over SSE (Req 11.1).
    """
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    resume_text = await _load_resume_text(session, user_id=user.id, resume_id=match.resume_id)
    orchestrator = _build_orchestrator(session=session, quota=quota, cache=cache, breaker=breaker)
    feature_input = InterviewQuestionsInput(resume_text=resume_text)
    if stream:
        return await _run_llm_feature_stream(
            orchestrator,
            INTERVIEW_QUESTIONS_SPEC,
            session=session,
            quota=quota,
            user_id=user.id,
            match=match,
            feature_input=feature_input,
        )
    return await _run_llm_feature(
        orchestrator,
        INTERVIEW_QUESTIONS_SPEC,
        session=session,
        response=response,
        quota=quota,
        user_id=user.id,
        match=match,
        feature_input=feature_input,
    )


@interview_question_sets_router.get(
    "",
    response_model=InterviewQuestionSetListResponse,
    responses=_LLM_FEATURE_RESPONSES,
)
async def list_interview_question_sets(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
    limit: Annotated[int, Query(ge=_MIN_LIST_LIMIT, le=_MAX_LIST_LIMIT)] = _DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> InterviewQuestionSetListResponse:
    """One newest-first page of the match's Interview_Question_Sets (Req 16.4)."""
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    page = await list_results(
        session,
        user_id=user.id,
        match_result_id=match.id,
        feature=LLMFeature.INTERVIEW_QUESTIONS,
        limit=limit,
        cursor=cursor,
    )
    _set_quota_header(response, await _read_quota_remaining(quota, user.id))
    return InterviewQuestionSetListResponse(
        items=[_stored_envelope(row, InterviewQuestionSet) for row in page.items],
        next_cursor=page.next_cursor,
    )


@interview_question_sets_router.get(
    "/{result_id}",
    response_model=InterviewQuestionSetEnvelope,
    responses=_LLM_FEATURE_RESPONSES,
)
async def get_interview_question_set(
    match_id: str,
    result_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    response: Response,
    quota: _QuotaDep,
) -> InterviewQuestionSetEnvelope:
    """One persisted Interview_Question_Set by id (Req 7.4, 16.3, 16.9)."""
    match = await _load_owned_match(session, user_id=user.id, raw_match_id=match_id)
    row = await _get_owned_result(
        session,
        user_id=user.id,
        match_result_id=match.id,
        feature=LLMFeature.INTERVIEW_QUESTIONS,
        raw_result_id=result_id,
    )
    _set_quota_header(response, await _read_quota_remaining(quota, user.id))
    return _stored_envelope(row, InterviewQuestionSet)


# ---------------------------------------------------------------------------
# Aggregate router — what ``main.py`` mounts.
# ---------------------------------------------------------------------------

router = APIRouter()
router.include_router(coaching_reports_router)
router.include_router(bullet_rewrites_router)
router.include_router(interview_question_sets_router)
