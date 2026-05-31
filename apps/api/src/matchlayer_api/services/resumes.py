"""``Resume_Service``: business logic for the resume surface.

This is the ONLY module in the API that reads or writes the ``resumes``
table (Components and Interfaces import-boundary rule). Every query it
issues carries ``WHERE user_id = :current_user`` so a query result can
never include a row owned by a different User_Account (Requirement 1.4,
per-user isolation). The router layer (``api/resumes/router.py``) owns
HTTP-shape concerns and the transaction boundary; this service stages
its work on the request-scoped :class:`AsyncSession` and never commits
(mirroring :class:`~matchlayer_api.services.auth.Auth_Service`).

Public surface (async):

* :meth:`Resume_Service.create_resume` -- the upload orchestration
  (quota -> MIME -> DOCX zip-bomb guard -> storage -> INSERT -> extract
  -> UPDATE -> audit), in the exact order the design prescribes.
* :meth:`Resume_Service.list_resumes` -- cursor-paginated, ``created_at``
  descending (Requirement 4.1).
* :meth:`Resume_Service.get_resume` -- a single owned, non-deleted row;
  raises :class:`~matchlayer_api.core.errors.NotFoundError` for a
  missing, soft-deleted, or other-owner id with no existence disclosure
  (Requirements 1.5, 1.6, 4.4).
* :meth:`Resume_Service.soft_delete_resume` -- idempotent soft delete
  (Requirements 4.5, 4.6).

PII discipline (CRITICAL -- ``security.md`` "Data classification" /
Requirements 2.6, 3.6). Three values handled here are Restricted PII and
are NEVER written to a log line, an error message, an Audit_Event
payload, or any telemetry that leaves the system:

* the client-supplied ``original_filename`` (stored verbatim for display
  only, never used to derive the storage key or any path),
* the uploaded file bytes, and
* the extracted resume text.

Resumes are referenced in logs and audit rows by their ``id`` only. The
``Audit_Service`` payload ``TypedDict`` schemas structurally exclude
these fields, so the mypy overload chain is the enforcement mechanism;
this module additionally never passes them to any logger.

Design reference: Components and Interfaces -- "Resume_Service"; the
upload data flow sequence; "Quota enforcement".
Requirements covered: 1.4, 2.5, 2.6, 2.7, 3.4, 3.5, 3.6, 4.1, 4.5, 4.6,
4.7, 11.4, 11.6.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import structlog
from fastapi import UploadFile
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.errors import (
    MatchLayerError,
    NotFoundError,
    QuotaExceededError,
    UnsupportedMediaTypeError,
)
from matchlayer_api.core.mime import detect as detect_mime
from matchlayer_api.core.storage import Resume_Storage, build_object_key, get_resume_storage
from matchlayer_api.db.models import Resume, User
from matchlayer_api.services.audit import Audit_Service
from matchlayer_api.services.extraction import extract, guard_docx_archive

__all__ = ["ResumePage", "Resume_Service"]

_log = structlog.get_logger(__name__)

# The validated media type stamped on the stored object and persisted in
# ``resumes.content_type``. Keyed by the Mime_Validator's magic-byte
# verdict (never the client ``Content-Type`` header or filename), so a
# spoofed header can never influence what is stored (Requirement 2.3).
_CONTENT_TYPE_BY_KIND: Final[dict[str, str]] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True, slots=True)
class ResumePage:
    """One page of a cursor-paginated resume listing (Requirement 4.1).

    The service returns ORM :class:`~matchlayer_api.db.models.Resume`
    rows; the router projects each onto the safe
    :class:`~matchlayer_api.api.resumes.schemas.ResumeResponse` shape and
    copies ``next_cursor`` onto the
    :class:`~matchlayer_api.api.resumes.schemas.ResumeListResponse` body.

    Attributes:
        items: The resumes on this page, ordered by ``created_at``
            descending (ties broken by ``id`` descending).
        next_cursor: An opaque keyset cursor for the following page, or
            ``None`` when this is the last page.
    """

    items: list[Resume]
    next_cursor: str | None


def _now() -> datetime:
    """Return a timezone-aware "now" in UTC.

    Centralised so the same UTC clock drives the daily-quota window, the
    soft-delete stamp, and the row timestamps. Tests freeze time by
    monkey-patching ``services.resumes._now``.
    """
    return datetime.now(UTC)


def _utc_day_start(now: datetime) -> datetime:
    """Return midnight UTC of the calendar day containing *now*.

    The Upload_Quota counts a user's ``resumes`` rows created at or after
    this instant (Design "Quota enforcement"): an exact, durable,
    Postgres-computed window that does not depend on Redis.
    """
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def _encode_cursor(resume: Resume) -> str:
    """Return an opaque keyset cursor for *resume* (``created_at`` + ``id``).

    The cursor encodes the composite sort key the listing orders by
    (``created_at`` DESC, ``id`` DESC), so the next page can be fetched
    with a strict row-value comparison against the matching composite
    index. It is base64url of ``"<created_at isoformat>|<id>"`` -- opaque
    to the client, which only echoes it back via ``?cursor=``.
    """
    raw = f"{resume.created_at.isoformat()}|{resume.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque keyset *cursor* into ``(created_at, id)``.

    Raises:
        MatchLayerError: A 422 ``validation_error`` envelope when the
            cursor is not valid base64url, is not well-formed, or does
            not parse into a timestamp and a UUID. The malformed value is
            never echoed back in the error ``detail``.
    """
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        timestamp_str, id_str = decoded.rsplit("|", 1)
        return datetime.fromisoformat(timestamp_str), UUID(id_str)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise MatchLayerError(
            "Invalid pagination cursor.",
            status_code=422,
            error_type="validation_error",
            title="Validation Error",
        ) from exc


class Resume_Service:  # noqa: N801 -- design uses the underscored class name.
    """Resume upload, retrieval, listing, and soft-deletion.

    Stateless and dependency-injected: every method takes the active
    request-scoped :class:`AsyncSession` so the audit row commits in the
    same transaction as the mutation that produced it (Audit Log §11.3).
    The service holds references to the object store, the audit service,
    and the active :class:`Settings`; no ORM session is cached on the
    instance.

    Construction is cheap, so the router allocates one per request; tests
    construct an instance inline, injecting a fake
    :class:`~matchlayer_api.core.storage.Resume_Storage` and/or a
    recording :class:`~matchlayer_api.services.audit.Audit_Service`.
    """

    __slots__ = ("_audit", "_settings", "_storage")

    def __init__(
        self,
        *,
        storage: Resume_Storage | None = None,
        audit: Audit_Service | None = None,
        settings: Settings | None = None,
    ) -> None:
        # ``storage`` is resolved lazily (see :meth:`_get_storage`) so the
        # read-only paths (list/get/delete) never construct a boto3 client.
        self._storage = storage
        self._audit = audit if audit is not None else Audit_Service()
        self._settings = settings if settings is not None else get_settings()

    def _get_storage(self) -> Resume_Storage:
        """Return the injected store, or the process-wide default once."""
        if self._storage is None:
            self._storage = get_resume_storage()
        return self._storage

    # ------------------------------------------------------------------
    # Upload (Requirements 2.5, 2.6, 2.7, 3.4, 3.5, 3.6, 11.4, 11.6).
    # ------------------------------------------------------------------

    async def create_resume(
        self,
        session: AsyncSession,
        *,
        user: User,
        upload: UploadFile,
    ) -> Resume:
        """Validate, store, extract, and persist one uploaded resume.

        Orchestration order is exactly as the design prescribes and must
        not be reordered -- each step gates the next:

        1. **Upload_Quota** (Requirement 11.4, 11.6). Count the user's
           ``resumes`` rows created in the current UTC calendar day
           (including later-soft-deleted ones -- the upload still
           happened). At or over ``MATCHLAYER_RESUME_DAILY_QUOTA``, emit a
           ``quota_rejected {quota: "upload"}`` audit row and raise
           :class:`~matchlayer_api.core.errors.QuotaExceededError` (429)
           before any object is written or any work is done.
        2. **Mime_Validator** (Requirement 2.3, mapped here). Sniff the
           true media type from the leading bytes; ``None`` (neither PDF
           nor DOCX) raises
           :class:`~matchlayer_api.core.errors.UnsupportedMediaTypeError`
           (415). The client ``Content-Type`` and filename are ignored.
        3. **DOCX zip-bomb guard** (Requirement 2.4). For a DOCX, reject a
           decompression bomb with
           :class:`~matchlayer_api.core.errors.MalformedUploadError` (422)
           before storage -- raised inside
           :func:`~matchlayer_api.services.extraction.guard_docx_archive`.
        4. **Resume_Storage.put** (Requirement 2.5, 2.10). Write the bytes
           under a fresh, filename-free ``<uuidv7>.<ext>`` key with no
           public-read.
        5. **INSERT** the row with ``extraction_status='pending'``
           (Requirement 2.7).
        6. **Resume_Extractor.extract** (Requirement 3.1-3.3). Bounded,
           fail-soft extraction.
        7. **UPDATE** the extraction columns from the outcome
           (Requirement 3.4, 3.5). A failure leaves ``extracted_text``
           null, sets ``extraction_status='failed'``, and logs a
           structured event naming the failure category and the resume id
           only -- never bytes or text (Requirement 3.7).
        8. **Audit** ``resume_uploaded {resume_id}`` (Requirement 2.7),
           internal id only.

        The client-supplied ``original_filename`` is stored verbatim for
        display only and is never logged or placed in an audit payload
        (Requirement 2.6); the uploaded bytes and extracted text are
        likewise never logged (Requirements 2.6, 3.6).

        Note on the transaction boundary: this method stages all work on
        *session* and does not commit. On the happy path the router
        commits after the call returns. On the quota-reject path the
        ``quota_rejected`` audit row is staged before the
        :class:`QuotaExceededError` is raised; the router is responsible
        for committing that row before the rejection leaves the app
        (the existing reject-path-commit precedent in the auth router).

        Args:
            session: Active request-scoped session.
            user: The authenticated owner (its ``id`` scopes every write).
            upload: The FastAPI ``UploadFile`` from the multipart body.
                The hard declared-length 413 check is the router's job
                (pre-service) and is not duplicated here.

        Returns:
            The persisted :class:`~matchlayer_api.db.models.Resume` row,
            with its extraction columns reflecting the outcome.

        Raises:
            QuotaExceededError: Daily Upload_Quota reached (429).
            UnsupportedMediaTypeError: Not a PDF or DOCX (415).
            MalformedUploadError: DOCX decompression-bomb guard tripped
                (422).
        """
        # --- 1. Upload_Quota (Postgres count for the UTC day) ---------
        now = _now()
        day_start = _utc_day_start(now)
        quota = self._settings.resume_daily_quota
        count = await session.scalar(
            select(func.count())
            .select_from(Resume)
            .where(Resume.user_id == user.id, Resume.created_at >= day_start)
        )
        uploaded_today = int(count or 0)
        if uploaded_today >= quota:
            # Stage the cost-as-DoS audit row, then refuse (Requirement
            # 11.6). The payload names only the quota category -- no PII.
            await self._audit.emit(
                session,
                event_type="quota_rejected",
                user_id=user.id,
                payload={"quota": "upload"},
            )
            reset_at = day_start + timedelta(days=1)
            raise QuotaExceededError(
                f"Daily resume upload quota of {quota} reached. "
                f"Quota resets at {reset_at.isoformat()}."
            )

        # --- 2. Read bytes + magic-byte MIME validation --------------
        # The bytes are Restricted PII from here on -- never logged.
        data = await upload.read()
        kind = detect_mime(data)
        if kind is None:
            raise UnsupportedMediaTypeError(
                "Uploaded file is not a supported PDF or DOCX document."
            )

        # --- 3. DOCX zip-bomb guard (fail-fast, before storage) ------
        if kind == "docx":
            guard_docx_archive(
                data,
                max_decompressed_bytes=self._settings.resume_max_decompressed_bytes,
                max_archive_entries=self._settings.resume_max_archive_entries,
            )

        # --- 4. Store under a fresh, filename-free UUIDv7 key --------
        content_type = _CONTENT_TYPE_BY_KIND[kind]
        storage_key = build_object_key(kind)
        await self._get_storage().put(key=storage_key, data=data, content_type=content_type)

        # --- 5. INSERT the row (extraction_status='pending') ---------
        # ``id`` is generated explicitly (matching Auth_Service) so it is
        # available for the audit payload without a flush round-trip; the
        # row timestamps are set in Python so the response carries them
        # without a post-commit refresh.
        resume = Resume(
            id=uuid7(),
            user_id=user.id,
            original_filename=upload.filename or "",
            storage_key=storage_key,
            content_type=content_type,
            byte_size=len(data),
            extracted_text=None,
            extraction_status="pending",
            extraction_char_count=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(resume)

        # --- 6. Bounded, fail-soft extraction ------------------------
        outcome = await extract(
            data,
            kind,
            timeout_seconds=self._settings.resume_extraction_timeout_seconds,
            max_extracted_chars=self._settings.resume_max_extracted_chars,
        )

        # --- 7. UPDATE extraction columns from the outcome -----------
        if outcome.status == "succeeded":
            resume.extracted_text = outcome.text
            resume.extraction_status = "succeeded"
            resume.extraction_char_count = outcome.char_count
        else:
            # Fail-soft (Requirement 3.5): persist the row as unparseable
            # rather than 5xx-ing the upload. Log the category + id only,
            # never bytes or text (Requirement 3.7).
            resume.extraction_status = "failed"
            resume.extracted_text = None
            resume.extraction_char_count = None
            _log.warning(
                "resume_extraction_failed",
                resume_id=str(resume.id),
                failure_category=outcome.failure_category,
            )
        resume.updated_at = now

        # --- 8. Audit resume_uploaded (internal id only, no PII) -----
        await self._audit.emit(
            session,
            event_type="resume_uploaded",
            user_id=user.id,
            payload={"resume_id": str(resume.id)},
        )

        return resume

    # ------------------------------------------------------------------
    # Listing (Requirement 4.1).
    # ------------------------------------------------------------------

    async def list_resumes(
        self,
        session: AsyncSession,
        *,
        user: User,
        limit: int,
        cursor: str | None,
    ) -> ResumePage:
        """Return one page of the caller's non-deleted resumes.

        Scoped to ``user_id`` (Requirement 1.4), filtered to
        ``deleted_at IS NULL``, and ordered ``created_at`` descending with
        ``id`` descending as the tie-breaker (Requirement 4.1). Cursor
        pagination uses a strict row-value comparison
        ``(created_at, id) < (cursor_created_at, cursor_id)`` that the
        composite ``resumes_user_created_idx`` index backs.

        The query fetches ``limit + 1`` rows to determine, without a
        second round-trip, whether a further page exists; the extra row is
        dropped from the page and its predecessor becomes the next cursor.

        Args:
            session: Active request-scoped session.
            user: The authenticated owner.
            limit: Page size (the router has already validated it is in
                ``1..100`` per Requirement 4.3).
            cursor: An opaque cursor from a prior page, or ``None`` for
                the first page.

        Returns:
            A :class:`ResumePage` of rows plus the next cursor (``None``
            on the last page).

        Raises:
            MatchLayerError: A 422 ``validation_error`` when *cursor* is
                malformed.
        """
        stmt = select(Resume).where(
            Resume.user_id == user.id,
            Resume.deleted_at.is_(None),
        )
        if cursor is not None:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(tuple_(Resume.created_at, Resume.id) < (cursor_created_at, cursor_id))
        stmt = stmt.order_by(Resume.created_at.desc(), Resume.id.desc()).limit(limit + 1)

        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        page_items = rows[:limit]
        next_cursor = _encode_cursor(page_items[-1]) if has_more and page_items else None
        return ResumePage(items=page_items, next_cursor=next_cursor)

    # ------------------------------------------------------------------
    # Single retrieval (Requirements 1.5, 1.6, 4.4).
    # ------------------------------------------------------------------

    async def get_resume(
        self,
        session: AsyncSession,
        *,
        user: User,
        resume_id: UUID,
    ) -> Resume:
        """Return the caller's single non-deleted resume, or raise 404.

        The lookup is scoped to ``user_id`` and ``deleted_at IS NULL``
        (Requirement 1.4), so a missing row, a soft-deleted row, and a row
        owned by a different User_Account all collapse to the same
        :class:`~matchlayer_api.core.errors.NotFoundError`. Returning an
        identical ``not_found`` envelope in every case means the existence
        of another account's resource is never disclosed (Requirements
        1.5, 1.6, 4.4).

        Args:
            session: Active request-scoped session.
            user: The authenticated owner.
            resume_id: The resume id from the path.

        Returns:
            The owned, non-deleted :class:`~matchlayer_api.db.models.Resume`.

        Raises:
            NotFoundError: The id is missing, soft-deleted, or owned by a
                different user (404, no disclosure).
        """
        result = await session.execute(
            select(Resume).where(
                Resume.id == resume_id,
                Resume.user_id == user.id,
                Resume.deleted_at.is_(None),
            )
        )
        resume = result.scalar_one_or_none()
        if resume is None:
            raise NotFoundError("Resume not found.")
        return resume

    # ------------------------------------------------------------------
    # Soft delete (Requirements 4.5, 4.6, 4.7).
    # ------------------------------------------------------------------

    async def soft_delete_resume(
        self,
        session: AsyncSession,
        *,
        user: User,
        resume_id: UUID,
    ) -> None:
        """Soft-delete the caller's resume; idempotent.

        Resolves the row scoped to ``user_id`` but **without** the
        ``deleted_at`` filter, so an already-soft-deleted owned row is
        distinguishable from a missing or other-owner one:

        * Missing or other-owner id -> :class:`NotFoundError` (404, no
          disclosure -- Requirements 1.5, 1.6).
        * Owned, already soft-deleted -> no-op return, emitting **no**
          second ``resume_deleted`` audit row, so a repeated DELETE is
          idempotent (Requirement 4.6).
        * Owned, currently active -> set ``deleted_at`` (and ``updated_at``)
          to now and emit one ``resume_deleted {resume_id}`` audit row
          (Requirement 4.5).

        The stored object in Resume_Storage and the ``extracted_text``
        column are intentionally retained after a soft delete during Phase
        1; hard deletion of bytes and a purge job are deferred to Phase 7
        per ``security.md`` (Requirement 4.7).

        Args:
            session: Active request-scoped session.
            user: The authenticated owner.
            resume_id: The resume id from the path.

        Raises:
            NotFoundError: The id is missing or owned by a different user.
        """
        result = await session.execute(
            select(Resume).where(
                Resume.id == resume_id,
                Resume.user_id == user.id,
            )
        )
        resume = result.scalar_one_or_none()
        if resume is None:
            # Missing or other-owner -> same 404 as a non-existent id, so
            # ownership is never disclosed (Requirements 1.5, 1.6).
            raise NotFoundError("Resume not found.")

        if resume.deleted_at is not None:
            # Already soft-deleted: idempotent no-op, no second audit row
            # (Requirement 4.6). Bytes and extracted_text are retained
            # (Requirement 4.7) -- nothing to do here.
            return

        now = _now()
        resume.deleted_at = now
        resume.updated_at = now
        await self._audit.emit(
            session,
            event_type="resume_deleted",
            user_id=user.id,
            payload={"resume_id": str(resume.id)},
        )
