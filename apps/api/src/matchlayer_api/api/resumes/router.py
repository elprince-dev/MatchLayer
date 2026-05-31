"""``Resumes_Router``: the ``/api/v1/resumes`` HTTP surface.

Pure HTTP-shape concerns only -- this module owns request parsing,
status codes, the transaction boundary, idempotency replay, and the
pre-service guards that must short-circuit before any object write; all
business logic is delegated to :class:`~matchlayer_api.services.resumes.Resume_Service`
(Components and Interfaces import-boundary rule). Mounted by
``create_app`` via ``app.include_router(resumes_router)`` in task 12.2 --
this module only defines the :class:`~fastapi.APIRouter`.

Routes (design "Resumes_Router" route table):

============================  ==========================================  ===================
Method & path                 Behavior                                    Key requirements
============================  ==========================================  ===================
``POST /api/v1/resumes``      multipart ``file``; 413 if declared length  2.1, 2.2, 2.8, 2.9
                              over ``MATCHLAYER_RESUME_MAX_BYTES``;
                              honors ``Idempotency-Key``; 201 safe shape
``GET /api/v1/resumes``       cursor list, ``created_at`` desc; ``limit``  4.1, 4.2, 4.3
                              1-100 else 422 ``validation_error``
``GET /api/v1/resumes/{id}``  single owned resume; 404 ``not_found`` if    4.4, 1.5, 1.6
                              missing/deleted/other-owner
``DELETE /api/v1/resumes/{id}``  soft delete, 204, idempotent             4.5, 4.6
============================  ==========================================  ===================

Every route depends on :func:`~matchlayer_api.core.dependencies.get_current_user`
(401 ``unauthenticated`` before any work -- Requirements 1.1-1.3) and on
``user_rate_limit("resume")`` (per-user, per-minute limit -> 429
``rate_limited`` / fail-closed 503 ``rate_limiter_unavailable`` --
Requirements 11.1, 11.3, 11.7). The rate-limit dependency itself composes
``get_current_user``; FastAPI resolves the shared sub-dependency once per
request, so the handler's ``user`` parameter and the limiter see the same
principal.

Transaction boundary. ``Resume_Service`` stages its work on the
request-scoped session and never commits (mirroring ``Auth_Service``), so
this router owns every commit:

* Happy-path create -> commit so the ``resumes`` row and the
  ``resume_uploaded`` audit row land in one transaction (Requirement 2.7).
* Quota reject -> the service stages a ``quota_rejected`` audit row and
  raises :class:`~matchlayer_api.core.errors.QuotaExceededError`; this
  router commits that audit row before the 429 leaves the app, mirroring
  the auth router's reject-path-commit precedent (Requirement 11.6).
* Soft delete -> commit so ``deleted_at`` and the ``resume_deleted`` audit
  row persist together (Requirement 4.5).

Requirements covered: 1.5, 1.6, 2.1, 2.2, 2.8, 2.9, 4.1, 4.3, 4.4, 4.5,
11.1, 11.3.
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from matchlayer_api.api.resumes.schemas import ResumeListResponse, ResumeResponse
from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.dependencies import (
    IdempotencyRecord,
    IdempotencyStoreDep,
    get_current_user,
    user_rate_limit,
)
from matchlayer_api.core.errors import PayloadTooLargeError, QuotaExceededError
from matchlayer_api.db.models import User
from matchlayer_api.services.resumes import Resume_Service

router = APIRouter(prefix="/api/v1/resumes", tags=["resumes"])

# Reusable Annotated dependency aliases. Declaring the ``Depends(...)`` call
# at module scope (rather than as a function default) keeps the FastAPI
# contract identical while avoiding ruff's B008 ("function call in argument
# default"), matching the pattern in ``auth/router.py``.
_SessionDep = Annotated[AsyncSession, Depends(get_session)]
_SettingsDep = Annotated[Settings, Depends(get_settings)]
_CurrentUser = Annotated[User, Depends(get_current_user)]

# The ``route`` segment of the Redis idempotency key
# (``idem:{user_id}:{route}:{key}``). Scopes a client-supplied
# ``Idempotency-Key`` to the resume-create endpoint so the same key on a
# different route is treated independently (Design "idempotency").
_IDEMPOTENCY_ROUTE: Final[str] = "resumes"

# The single multipart file part this endpoint accepts (Requirement 2.1:
# "a single file part named ``file``"). Declared as a module-level
# ``Annotated`` alias for the same B008 reason as the dependency aliases.
_UploadFileDep = Annotated[UploadFile, File(description="The resume file (PDF or DOCX).")]

# ``Idempotency-Key`` request header (``security.md`` idempotency rule for
# upload endpoints). FastAPI maps the ``idempotency_key`` parameter to this
# header by default; the explicit alias documents the wire name.
_IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def _declared_upload_length(*, upload: UploadFile, request: Request) -> int | None:
    """Return the declared length of the upload, or ``None`` if unknown.

    The 413 check is a *pre-service* guard (Requirement 2.2): it must
    short-circuit before the service reads bytes or writes any object to
    Resume_Storage. The declared length is taken from the multipart part's
    ``size`` (populated by Starlette's form parser as the part is spooled,
    without us reading it) and falls back to the request ``Content-Length``
    header when the part size is unavailable. A request whose length cannot
    be determined returns ``None`` and is allowed past this guard -- the
    service's downstream bounds (MIME, zip-bomb, extraction caps) still
    apply.

    Args:
        upload: The parsed multipart ``UploadFile``.
        request: The active request, for the ``Content-Length`` fallback.

    Returns:
        The declared byte length, or ``None`` when it cannot be determined.
    """
    if upload.size is not None:
        return upload.size
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit():
        return int(content_length)
    return None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ResumeResponse,
    dependencies=[Depends(user_rate_limit("resume"))],
)
async def create_resume(
    request: Request,
    file: _UploadFileDep,
    user: _CurrentUser,
    session: _SessionDep,
    settings: _SettingsDep,
    idempotency_store: IdempotencyStoreDep,
    idempotency_key: _IdempotencyKeyHeader = None,
) -> ResumeResponse:
    """Upload one resume; 201 with the safe field set.

    Pre-service guards run in the order the design's upload sequence
    prescribes -- the per-user rate limit (dependency) and the 413
    declared-length check both short-circuit before any object is written
    (Requirement 2.2) -- followed by idempotency replay, then the service
    orchestration (quota -> MIME -> zip-bomb -> store -> insert -> extract
    -> audit).

    On a quota breach the service stages a ``quota_rejected`` audit row and
    raises :class:`QuotaExceededError`; this handler commits that row before
    re-raising so the audit lands even though the 429 short-circuits the
    upload (Requirement 11.6). On success the ``resumes`` row and the
    ``resume_uploaded`` audit row commit together (Requirement 2.7), and the
    response carries only the safe field set -- never ``extracted_text``,
    ``storage_key``, or the raw bytes (Requirement 2.9).

    Args:
        request: The active request (used for the ``Content-Length``
            fallback in the 413 guard).
        file: The multipart ``file`` part (Requirement 2.1).
        user: The authenticated owner.
        session: The request-scoped session (this handler owns the commit).
        settings: Active settings (the ``resume_max_bytes`` ceiling).
        idempotency_store: Redis-backed store for idempotent replay.
        idempotency_key: Optional ``Idempotency-Key`` header (Requirement
            2.8).

    Returns:
        The created (or replayed) :class:`ResumeResponse`.

    Raises:
        PayloadTooLargeError: Declared length over ``MATCHLAYER_RESUME_MAX_BYTES``
            (413 ``payload_too_large``).
        QuotaExceededError: Daily Upload_Quota reached (429 ``quota_exceeded``).
    """
    # --- 413 declared-length guard (pre-service, Requirement 2.2) -------
    declared_length = _declared_upload_length(upload=file, request=request)
    if declared_length is not None and declared_length > settings.resume_max_bytes:
        raise PayloadTooLargeError(
            f"Uploaded file exceeds the maximum size of {settings.resume_max_bytes} bytes."
        )

    # --- Idempotency replay (Requirement 2.8) ---------------------------
    # A key that matches a stored outcome within the 24h TTL returns the
    # original 201 response without a second object write or row insert.
    if idempotency_key:
        replay = await idempotency_store.get(
            user_id=user.id, route=_IDEMPOTENCY_ROUTE, key=idempotency_key
        )
        if replay is not None:
            return ResumeResponse.model_validate(replay.body)

    # --- Create (the service stages; this router owns the commit) -------
    service = Resume_Service(settings=settings)
    try:
        resume = await service.create_resume(session, user=user, upload=file)
    except QuotaExceededError:
        # The service staged a ``quota_rejected`` audit row before raising;
        # commit it so the cost-as-DoS event is durable even though the
        # upload is refused (Requirement 11.6, reject-path-commit precedent).
        await session.commit()
        raise

    await session.commit()

    response_body = ResumeResponse.model_validate(resume)

    # --- Memoize the outcome for idempotent replay (Requirement 2.8) ----
    if idempotency_key:
        await idempotency_store.put(
            user_id=user.id,
            route=_IDEMPOTENCY_ROUTE,
            key=idempotency_key,
            record=IdempotencyRecord(
                resource_id=str(resume.id),
                status_code=status.HTTP_201_CREATED,
                body=response_body.model_dump(mode="json"),
            ),
        )

    return response_body


@router.get(
    "",
    response_model=ResumeListResponse,
    dependencies=[Depends(user_rate_limit("resume"))],
)
async def list_resumes(
    user: _CurrentUser,
    session: _SessionDep,
    settings: _SettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> ResumeListResponse:
    """List the caller's non-deleted resumes (cursor-paginated).

    ``limit`` is constrained to ``1..100`` by :class:`~fastapi.Query`; an
    out-of-range or non-numeric value raises FastAPI's
    ``RequestValidationError``, which the foundation handler renders as the
    422 ``validation_error`` envelope (Requirement 4.3). Results are scoped
    to the caller, ordered ``created_at`` descending, and projected onto the
    safe :class:`ResumeResponse` shape -- no ``extracted_text`` or
    ``storage_key`` (Requirements 4.1, 4.2).

    Args:
        user: The authenticated owner.
        session: The request-scoped session (read-only path, no commit).
        settings: Active settings.
        limit: Page size, ``1..100`` (default 20).
        cursor: Opaque cursor from a prior page, or ``None`` for the first.

    Returns:
        A :class:`ResumeListResponse` page plus the next cursor.
    """
    service = Resume_Service(settings=settings)
    page = await service.list_resumes(session, user=user, limit=limit, cursor=cursor)
    return ResumeListResponse(
        items=[ResumeResponse.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{resume_id}",
    response_model=ResumeResponse,
    dependencies=[Depends(user_rate_limit("resume"))],
)
async def get_resume(
    resume_id: UUID,
    user: _CurrentUser,
    session: _SessionDep,
    settings: _SettingsDep,
) -> ResumeResponse:
    """Return a single owned, non-deleted resume, or 404.

    The service collapses a missing row, a soft-deleted row, and a row
    owned by another User_Account into the same
    :class:`~matchlayer_api.core.errors.NotFoundError` (404 ``not_found``),
    so the existence of another account's resource is never disclosed
    (Requirements 1.5, 1.6, 4.4).

    Args:
        resume_id: The resume id from the path.
        user: The authenticated owner.
        session: The request-scoped session (read-only path, no commit).
        settings: Active settings.

    Returns:
        The owned :class:`ResumeResponse`.
    """
    service = Resume_Service(settings=settings)
    resume = await service.get_resume(session, user=user, resume_id=resume_id)
    return ResumeResponse.model_validate(resume)


@router.delete(
    "/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(user_rate_limit("resume"))],
)
async def delete_resume(
    resume_id: UUID,
    user: _CurrentUser,
    session: _SessionDep,
    settings: _SettingsDep,
) -> None:
    """Soft-delete the caller's resume; 204, idempotent.

    Delegates to the idempotent
    :meth:`~matchlayer_api.services.resumes.Resume_Service.soft_delete_resume`:
    an active owned row is stamped ``deleted_at`` and emits one
    ``resume_deleted`` audit row (Requirement 4.5); an already-soft-deleted
    owned row is a no-op with no second audit row (Requirement 4.6); a
    missing or other-owner id raises
    :class:`~matchlayer_api.core.errors.NotFoundError` (404, no disclosure).
    The commit persists ``deleted_at`` and the audit row together; on the
    no-op and 404 paths there is nothing staged to commit.

    Args:
        resume_id: The resume id from the path.
        user: The authenticated owner.
        session: The request-scoped session (this handler owns the commit).
        settings: Active settings.
    """
    service = Resume_Service(settings=settings)
    await service.soft_delete_resume(session, user=user, resume_id=resume_id)
    await session.commit()
