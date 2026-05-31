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

from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.api.matches.schemas import (
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
from matchlayer_api.core.errors import NotFoundError, QuotaExceededError
from matchlayer_api.db.models import MatchResult, User
from matchlayer_api.services.matching import Scoring_Service

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


__all__ = ["router"]
