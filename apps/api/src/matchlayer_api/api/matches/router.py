"""``Matches_Router``: ``/api/v1/matches/*`` endpoints.

Pure HTTP-shape concerns only — no business logic (Components and Interfaces
import-boundary rule). Every mutation and query delegates to
:class:`~matchlayer_api.services.matching.Scoring_Service`, which is the only
module permitted to read or write the ``match_results`` table.

Endpoints (design "Matches_Router" table; ``requirements.md`` §8, §9):

============================== ============================================== ====================
Method & path                  Behavior                                       Key requirements
============================== ============================================== ====================
``POST   /api/v1/matches``     JSON ``{resume_id, job_description}``; Pydantic 8.1-8.3, 8.7, 8.9
                               + JD length bounds → 422 ``validation_error``;
                               honors ``Idempotency-Key`` (replay stored 201);
                               201 with the full Match_Result field set.
``GET    /api/v1/matches``     Cursor-paginated list, ``created_at`` desc;     9.1, 9.2
                               items omit ``job_description_text``.
``GET    /api/v1/matches/{id}``Single owned match; 404 ``not_found`` if        1.5, 1.6, 9.3, 9.6
                               missing/deleted/other-owner; still returned
                               when its resume was later soft-deleted.
``DELETE /api/v1/matches/{id}``Soft delete, 204, idempotent.                   9.4, 9.5
============================== ============================================== ====================

Every route depends on :func:`~matchlayer_api.core.dependencies.get_current_user`
(401 ``unauthenticated`` for a missing/invalid/wrong-type token or a
soft-deleted principal, Requirements 1.1-1.3) and on
:func:`~matchlayer_api.core.dependencies.user_rate_limit` with the ``"match"``
endpoint (per-user, per-minute sliding window → 429 ``rate_limited`` +
``Retry-After`` on a normal rejection, or 503 ``rate_limiter_unavailable`` when
Redis is unreachable, Requirements 11.2, 11.3, 11.7). ``get_current_user`` is a
single shared callable, so FastAPI's per-request dependency cache resolves it
once even though both the route parameter and the rate-limit dependency request
it.

Transaction model (mirrors :mod:`matchlayer_api.auth.router`): the
``Scoring_Service`` stages its rows and never commits; the router owns the
commit so the ``match_results`` row and its ``match_created`` audit row land in
one transaction (Audit Log §11.3). On the daily-quota reject path the service
stages a ``quota_rejected`` audit row and raises
:class:`~matchlayer_api.core.errors.QuotaExceededError`; the router commits that
staged audit row before the 429 leaves (mirroring the auth login router, which
commits before raising on a failed-login outcome). The ``not_found`` and
``resume_not_extractable`` paths stage nothing, so they simply propagate to the
foundation RFC 7807 handler and the request-scoped session is discarded.

PRIVACY (``security.md``; Requirement 8.8): ``job_description_text`` is
Restricted PII and is never returned by any response model here — ``GET`` list
items use :class:`MatchListItem` (which omits it) and the single-match views use
:class:`MatchResponse` (which omits it). The router never logs it.

Design reference: "Matches_Router", "Per-user rate limiting and idempotency",
"Error Handling". Requirements covered: 8.1, 8.2, 8.3, 8.7, 8.9, 9.1, 9.2, 9.3,
9.4, 9.5, 9.6, 11.2, 11.3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.api.matches.schemas import (
    AnalyzeAcceptedResponse,
    CreateMatchRequest,
    MatchListItem,
    MatchListResponse,
    MatchResponse,
)
from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import (
    IdempotencyRecord,
    IdempotencyStoreDep,
    get_current_user,
    user_rate_limit,
)
from matchlayer_api.core.errors import (
    JobQueueUnavailableError,
    NotFoundError,
    QuotaExceededError,
)
from matchlayer_api.db.models import AgentJob, MatchResult, User
from matchlayer_api.services.agent_jobs.queue import JobMessage, JobQueue, get_job_queue
from matchlayer_api.services.agent_jobs.service import create_job, mark_failed
from matchlayer_api.services.llm.quota import (
    DailyQuota,
    QuotaAccountingError,
    get_daily_quota,
)
from matchlayer_api.services.matching import Scoring_Service

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/matches", tags=["matches"])

# ---------------------------------------------------------------------------
# Reusable Annotated dependency aliases. Declaring the ``Depends(...)`` call at
# module scope (rather than as a function default) avoids ruff B008 while
# keeping the FastAPI dependency-injection contract identical (mirrors
# ``auth/router.py`` and ``core/dependencies.py``).
# ---------------------------------------------------------------------------
_SessionDep = Annotated[AsyncSession, Depends(get_session)]
_CurrentUser = Annotated[User, Depends(get_current_user)]

# Built once at import time so the same closure (and therefore one dependency
# cache slot) is reused across every request; used as a route-level dependency
# on all four endpoints (Requirements 11.2, 11.3).
_MatchRateLimit = Depends(user_rate_limit("match"))

# The async analyze endpoint carries its own (tighter) per-user budget —
# MATCHLAYER_AGENT_ANALYZE_RATE_LIMIT_PER_MINUTE, default 10/min
# (phase-4-agentic Requirement 10.7).
_AnalyzeRateLimit = Depends(user_rate_limit("analyze"))

# Dependency aliases for the analyze endpoint (phase-4-agentic). The
# Daily_Quota handle backs the read-only precheck (Requirement 9.4); the
# JobQueue handle is resolved via ``Depends`` (rather than called inline)
# so tests can substitute a fake through ``app.dependency_overrides``.
_DailyQuotaDep = Annotated[DailyQuota, Depends(get_daily_quota)]
_JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]

# Minimum remaining Daily_Quota units required to accept an analyze
# request: the Agent_Graph makes up to two LLM calls (Resume_Analysis +
# Improvement), each reserving one unit at call initiation (phase-4
# Requirement 9.4; design §7 step 3).
_ANALYZE_MIN_QUOTA_UNITS: Final[int] = 2

# The route segment under which idempotency keys are namespaced in Redis
# (``idem:{user_id}:matches:{key}``); distinct from the resume route so the
# same client-supplied key on a different endpoint is treated independently.
_IDEMPOTENCY_ROUTE: Final[str] = "matches"

# Cursor-pagination ``limit`` bounds (Requirement 9.1; ``conventions.md``
# "Pagination"). A value outside 1..100 fails FastAPI's query validation and
# surfaces as 422 ``validation_error`` through the foundation handler.
_DEFAULT_LIST_LIMIT: Final[int] = 20
_MIN_LIST_LIMIT: Final[int] = 1
_MAX_LIST_LIMIT: Final[int] = 100


# ---------------------------------------------------------------------------
# Response projection helpers.
# ---------------------------------------------------------------------------


def _match_response(match: MatchResult) -> MatchResponse:
    """Project a ``MatchResult`` ORM row onto the full :class:`MatchResponse`.

    The UUID columns are rendered to strings explicitly (Pydantic v2 does not
    coerce ``UUID`` into a ``str`` field), and the JSONB columns
    (``score_breakdown`` dict; ``matched_keywords`` / ``missing_keywords`` /
    ``suggestions`` lists of dicts) validate into the nested response models
    field-for-field. ``job_description_text`` is deliberately never read, so it
    cannot leak into the response body (Requirement 8.8).
    """
    return MatchResponse.model_validate(
        {
            "id": str(match.id),
            "resume_id": str(match.resume_id),
            "score": match.score,
            "score_breakdown": match.score_breakdown,
            "matched_keywords": match.matched_keywords,
            "missing_keywords": match.missing_keywords,
            "suggestions": match.suggestions,
            "scorer_version": match.scorer_version,
            "created_at": match.created_at,
            "updated_at": match.updated_at,
        }
    )


def _match_list_item(match: MatchResult) -> MatchListItem:
    """Project a ``MatchResult`` row onto the trimmed :class:`MatchListItem`.

    Carries only ``{id, resume_id, score, created_at}`` — the heavier JSONB
    columns and the Restricted ``job_description_text`` are never read for the
    list view (Requirement 9.2).
    """
    return MatchListItem.model_validate(
        {
            "id": str(match.id),
            "resume_id": str(match.resume_id),
            "score": match.score,
            "created_at": match.created_at,
        }
    )


def _parse_match_id(raw: str) -> UUID:
    """Parse a path ``{id}`` segment into a :class:`UUID`, else 404 ``not_found``.

    A syntactically invalid id cannot match any row, so it is mapped to the same
    ``not_found`` envelope as a missing/other-owner match rather than a 422 —
    consistent with the no-disclosure rule (Requirements 1.5, 1.6) and the
    task's "prefer 404 not_found" guidance for malformed ids.
    """
    try:
        return UUID(raw)
    except ValueError as exc:
        raise NotFoundError("Match not found.") from exc


# ---------------------------------------------------------------------------
# POST /api/v1/matches  (Requirements 8.1-8.3, 8.7, 8.9)
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=MatchResponse,
    dependencies=[_MatchRateLimit],
)
async def create_match(
    body: CreateMatchRequest,
    user: _CurrentUser,
    session: _SessionDep,
    idempotency_store: IdempotencyStoreDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> MatchResponse:
    """Score a resume against a job description and persist the Match_Result.

    The request body is validated by :class:`CreateMatchRequest`, whose
    ``job_description`` field validator enforces the trimmed-length window
    ``MATCHLAYER_JD_MIN_CHARS``..``MATCHLAYER_JD_MAX_CHARS`` — a violation (or
    any other Pydantic failure) surfaces as 422 ``validation_error`` before this
    handler runs (Requirements 8.2, 8.3).

    Idempotency (Requirement 8.9): when an ``Idempotency-Key`` header matches a
    record stored for this user within the preceding 24h, the original 201
    response is replayed without creating a second Match_Result. Otherwise the
    service creates the match, the router commits, and the response is stored
    under the key for future replays.

    Failure mapping:
      * ``resume_id`` that is malformed, or does not resolve to an owned,
        non-deleted resume → 404 ``not_found`` (Requirement 8.4; no disclosure).
      * referenced resume whose ``extraction_status != 'succeeded'`` → 422
        ``resume_not_extractable`` (Requirement 8.5).
      * daily Scoring_Quota reached → 429 ``quota_exceeded``; the service stages
        a ``quota_rejected`` audit row which this handler commits before the
        error propagates (Requirement 11.6 audit; the ``detail`` + ``Retry-After``
        are owned by the service/dependency layer).
    """
    # Idempotency replay: a stored outcome short-circuits all work, so no second
    # Match_Result is created (Requirement 8.9). The stored body was produced by
    # ``MatchResponse(...).model_dump(mode="json")`` below, so revalidating it
    # reproduces the identical 201 response.
    if idempotency_key:
        record = await idempotency_store.get(
            user_id=user.id, route=_IDEMPOTENCY_ROUTE, key=idempotency_key
        )
        if record is not None:
            return MatchResponse.model_validate(record.body)

    # A malformed ``resume_id`` is mapped to ``not_found`` (no disclosure),
    # matching how the service treats a missing/other-owner resume.
    try:
        resume_id = UUID(body.resume_id)
    except ValueError as exc:
        raise NotFoundError("Resume not found.") from exc

    svc = Scoring_Service()
    try:
        match = await svc.create_match(
            session,
            user_id=user.id,
            resume_id=resume_id,
            job_description=body.job_description,
        )
    except QuotaExceededError:
        # The service staged a ``quota_rejected`` audit row before raising;
        # commit it so the rejection is durably recorded even though the request
        # fails (mirrors the auth login router committing before it raises).
        await session.commit()
        raise

    # Commit so the ``match_results`` row and its ``match_created`` audit row
    # land in one transaction (Audit Log §11.3).
    await session.commit()

    response = _match_response(match)

    # Memoize the outcome for replay. ``put`` is first-writer-wins (SET NX) and
    # fails soft, so a Redis blip never turns the successful creation into a 5xx.
    if idempotency_key:
        await idempotency_store.put(
            user_id=user.id,
            route=_IDEMPOTENCY_ROUTE,
            key=idempotency_key,
            record=IdempotencyRecord(
                resource_id=str(match.id),
                status_code=status.HTTP_201_CREATED,
                body=response.model_dump(mode="json"),
            ),
        )

    return response


# ---------------------------------------------------------------------------
# GET /api/v1/matches  (Requirements 9.1, 9.2)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=MatchListResponse,
    dependencies=[_MatchRateLimit],
)
async def list_matches(
    user: _CurrentUser,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=_MIN_LIST_LIMIT, le=_MAX_LIST_LIMIT)] = _DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> MatchListResponse:
    """Return one cursor-paginated page of the caller's non-deleted matches.

    Ordered by ``created_at`` descending (ties broken by ``id`` descending),
    scoped to the requesting user (Requirements 1.4, 9.1). ``limit`` outside
    1..100 fails query validation → 422 ``validation_error``. Each item is a
    :class:`MatchListItem`, which omits ``job_description_text`` (Requirement
    9.2). ``next_cursor`` is ``None`` on the last page.
    """
    svc = Scoring_Service()
    page = await svc.list_matches(session, user_id=user.id, limit=limit, cursor=cursor)
    return MatchListResponse(
        items=[_match_list_item(match) for match in page.items],
        next_cursor=page.next_cursor,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/matches/{id}  (Requirements 1.5, 1.6, 9.3, 9.6)
# ---------------------------------------------------------------------------


@router.get(
    "/{match_id}",
    response_model=MatchResponse,
    dependencies=[_MatchRateLimit],
)
async def get_match(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
) -> MatchResponse:
    """Return one owned, non-deleted Match_Result.

    A missing, soft-deleted, or other-owner match (or a malformed id) yields the
    ``not_found`` envelope, so another account's match is indistinguishable from
    one that does not exist (Requirements 1.5, 1.6, 9.3). The match is returned
    even when its referenced resume was later soft-deleted — the score and
    analysis are retained independently of the resume's lifecycle (Requirement
    9.6, guaranteed by the service's query, which does not filter on the
    resume's ``deleted_at``).
    """
    parsed = _parse_match_id(match_id)
    svc = Scoring_Service()
    match = await svc.get_match(session, user_id=user.id, match_id=parsed)
    return _match_response(match)


# ---------------------------------------------------------------------------
# DELETE /api/v1/matches/{id}  (Requirements 9.4, 9.5)
# ---------------------------------------------------------------------------


@router.delete(
    "/{match_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_MatchRateLimit],
)
async def delete_match(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
) -> None:
    """Soft-delete an owned Match_Result; idempotent (Requirements 9.4, 9.5).

    On the first delete of an owned, non-deleted match the service sets
    ``deleted_at`` and stages a ``match_deleted`` audit row; the router commits
    so both land together. A match that is already soft-deleted, does not exist,
    is owned by another user, or carries a malformed id is a silent no-op that
    emits no second audit row — every case returns 204 uniformly, disclosing
    nothing about another account's data (Requirements 1.4, 9.5).
    """
    try:
        parsed = UUID(match_id)
    except ValueError:
        # A malformed id cannot identify any match; treat it as already-absent
        # so the idempotent delete contract holds (204, no disclosure).
        return

    svc = Scoring_Service()
    await svc.soft_delete_match(session, user_id=user.id, match_id=parsed)
    # Commit the staged ``deleted_at`` + ``match_deleted`` audit row (a no-op
    # commit is harmless when the call was an already-deleted/missing no-op).
    await session.commit()


# ---------------------------------------------------------------------------
# POST /api/v1/matches/{id}/analyze  (phase-4-agentic Requirements 9.4, 10.1,
# 10.4, 10.5, 10.6, 10.7, 11.1, 11.6, 11.8; design §7, decisions D5, D6)
# ---------------------------------------------------------------------------


def _next_utc_midnight(moment: datetime) -> datetime:
    """The next 00:00:00 UTC strictly after *moment*'s calendar day.

    The instant the Daily_Quota resets, surfaced in the 429 ``detail``
    so the caller knows when they may retry (Requirement 9.4's UTC reset
    time). Mirrors the identically named helpers in
    ``services/matching.py`` and ``services/llm/orchestrator.py``.
    """
    day_start = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start + timedelta(days=1)


def _analyze_response(job: AgentJob) -> AnalyzeAcceptedResponse:
    """Project an Agent_Job onto the 202 body (Requirement 10.1).

    Identifiers and a relative poll URL only — never match or resume
    content. ``status`` is the job's current Job_Status: ``queued`` for
    a fresh job, possibly ``running`` on the idempotent-reuse path.
    """
    return AnalyzeAcceptedResponse.model_validate(
        {
            "id": str(job.id),
            "status": job.status,
            "job_url": f"/api/v1/jobs/{job.id}",
        }
    )


@router.post(
    "/{match_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalyzeAcceptedResponse,
    dependencies=[_AnalyzeRateLimit],
)
async def analyze_match(
    match_id: str,
    user: _CurrentUser,
    session: _SessionDep,
    quota: _DailyQuotaDep,
    queue: _JobQueueDep,
) -> AnalyzeAcceptedResponse:
    """Accept an async multi-agent analysis of an owned Match_Result.

    Executes the design §7 sequence exactly — no Agent runs synchronously
    in this request path, and the handler never reads Resume
    ``extracted_text`` or ``job_description_text`` (Requirement 11.8; all
    resume-content processing happens in the Agent_Worker):

    1. **Authn + ownership** — the route-level ``analyze`` rate limit
       composes :func:`get_current_user` (401 first), and the owned
       Match_Result is resolved through the same ``Scoring_Service``
       lookup as ``GET /matches/{id}``, so missing, other-owner, and
       malformed ids collapse to one indistinguishable 404 ``not_found``
       envelope (Requirement 10.4).
    2. **Rate limit** — ``MATCHLAYER_AGENT_ANALYZE_RATE_LIMIT_PER_MINUTE``
       (default 10/min) per user; 429 ``rate_limited`` on breach
       (Requirement 10.7).
    3. **Quota precheck** — read-only Daily_Quota gate requiring at least
       2 remaining units (the run's worst-case LLM call count). Fewer →
       429 RFC 7807 with the UTC reset time; no job row is created and
       no message is enqueued (Requirement 9.4). The gate never counts
       the request — actual reservation happens per-call inside the
       LLM agents. An unreadable quota counter is treated as
       pass-through with one structured warning: the agents' atomic
       reserve remains the authoritative spend control (Requirements
       9.9, 13.8 fail-safe posture), so availability of the precheck
       never blocks or double-counts anything.
    4. **In-flight idempotency** — insert-first via the partial unique
       index (D5); an existing non-terminal job is returned with 202
       and NOT re-enqueued (Requirement 10.5).
    5. **Persist → commit → enqueue** (D6) — the ``queued`` row is
       committed before the SQS send so no message can ever reference an
       uncommitted job (Requirement 11.1). On enqueue failure the job is
       transitioned to ``failed`` and committed (no orphaned ``queued``
       row) and a 503 ``job_queue_unavailable`` RFC 7807 envelope is
       returned with fixed display-safe copy (Requirement 11.6). Trace
       context is injected into the message attributes by
       :meth:`JobQueue.enqueue` itself (Requirement 13.4).
    6. **202 Accepted** — ``{id, status, job_url}`` (Requirement 10.1).

    ``X-Robots-Tag: noindex, nofollow`` lands on every response via the
    ``ApiNoIndexMiddleware`` covering ``/api/v1/*`` (Requirement 10.6).
    """
    # 1. Ownership: same lookup + envelope as GET /matches/{id} — the
    # not-owned and not-found cases are byte-identical (Requirement 10.4).
    parsed = _parse_match_id(match_id)
    match = await Scoring_Service().get_match(session, user_id=user.id, match_id=parsed)

    # 3. Read-only quota precheck (Requirement 9.4). Runs BEFORE any job
    # row exists so a rejection provably creates nothing.
    remaining: int | None
    try:
        remaining = (await quota.gate(str(user.id))).remaining
    except QuotaAccountingError:
        # Fail-safe pass-through: the per-call atomic reserve inside each
        # LLM agent is the authoritative control (Requirement 9.9). One
        # structured warning, no PII, identifiers only.
        _log.warning("agent_analyze_quota_precheck_unavailable", match_id=str(match.id))
        remaining = None
    if remaining is not None and remaining < _ANALYZE_MIN_QUOTA_UNITS:
        resets_at = _next_utc_midnight(datetime.now(UTC))
        raise QuotaExceededError(
            f"Running an analysis requires at least {_ANALYZE_MIN_QUOTA_UNITS} "
            f"remaining daily LLM quota units; {remaining} remain. "
            f"Quota resets at {resets_at.isoformat()}."
        )

    # 4. Create-with-idempotency (D5). An existing non-terminal job is
    # returned as-is: 202 with its id, and — critically — no duplicate
    # message is enqueued (Requirement 10.5).
    creation = await create_job(session, user_id=user.id, match_id=match.id)
    if not creation.created:
        return _analyze_response(creation.job)

    job = creation.job

    # 5. Persist before enqueue (Requirement 11.1 / D6): commit the
    # ``queued`` row so the message the worker receives always references
    # a durable job.
    await session.commit()

    try:
        await queue.enqueue(
            JobMessage(job_id=str(job.id), match_id=str(match.id), user_id=str(user.id))
        )
    except Exception as exc:
        # Enqueue-failure compensation (Requirement 11.6 / D6): transition
        # the committed row to ``failed`` so no orphaned ``queued`` job
        # remains and the partial unique index unblocks a retry. The log
        # line carries the exception class only — the message could embed
        # the queue URL or endpoint address, which must never leak.
        _log.warning(
            "agent_job_enqueue_failed",
            job_id=str(job.id),
            reason=type(exc).__name__,
        )
        await mark_failed(
            session,
            job_id=job.id,
            error={
                "type": "enqueue_failed",
                "detail": "The analysis could not be queued. Please try again later.",
            },
        )
        await session.commit()
        raise JobQueueUnavailableError(
            "The analysis service is temporarily unavailable. Please try again later."
        ) from None

    # 6. 202 Accepted with the pollable job URL (Requirement 10.1).
    return _analyze_response(job)


__all__ = ["router"]
