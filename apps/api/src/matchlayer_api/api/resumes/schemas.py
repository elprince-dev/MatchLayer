"""Pydantic v2 response models for the resume surface.

These models are the source of truth for the resume section of the
OpenAPI schema FastAPI emits at ``app.openapi()``. ``pnpm codegen``
consumes that schema to regenerate ``packages/shared-types/src/api-types.ts``
(via ``openapi-typescript``) and ``api-schemas.ts`` (via
``openapi-zod-client``); the curated re-exports ``ResumeResponse`` and
``ResumeListResponse`` are added to ``index.ts`` (see ``tasks.md`` 13.1).
Anything missing here is missing on the frontend; anything wrong here is
wrong on the frontend.

Model coverage (one Pydantic class per HTTP body in
``phase-1-matching/requirements.md`` §2, §4):

Responses:
  * :class:`ResumeResponse` -- the safe Resume projection returned by
    ``POST /api/v1/resumes``, ``GET /api/v1/resumes/{id}``, and embedded
    in :class:`ResumeListResponse`. Carries **exactly** the seven fields
    Requirements 2.9 and 4.2 enumerate and **never** exposes
    ``extracted_text`` or ``storage_key`` (both Restricted/Confidential
    per ``security.md`` "Data classification").
  * :class:`ResumeListResponse` -- the ``GET /api/v1/resumes`` body:
    a page of :class:`ResumeResponse` items plus an opaque
    ``next_cursor`` for cursor-based pagination (``conventions.md``
    "Pagination"; Requirement 4.1).

Design references:
  * Components and Interfaces -- ``api/resumes/schemas.py`` is "Pydantic
    request/response models" only; no business logic, no DB calls.
  * Pydantic schemas (design §"api/resumes/schemas.py") -- the field
    sets below mirror the design's declared shapes verbatim.

Requirements covered: 2.9, 4.1, 4.2.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# ---------------------------------------------------------------------------
# Response models.
# ---------------------------------------------------------------------------


class ResumeResponse(BaseModel):
    """Safe Resume projection (Requirements 2.9, 4.2).

    Carries exactly the seven fields Requirements 2.9 and 4.2 enumerate:
    ``id``, ``original_filename``, ``content_type``, ``byte_size``,
    ``extraction_status``, ``created_at``, ``updated_at``.

    ``extracted_text`` and ``storage_key`` are intentionally absent. Both
    are sensitive per ``security.md``: ``extracted_text`` is Restricted
    PII (parsed resume content) and ``storage_key`` is the internal
    object-storage location. Neither may ever appear in an API response
    body. This exclusion is a hard security control, not a convenience --
    Property 17 (``tasks.md`` 10.2) asserts the response never exposes
    them.

    ``original_filename`` is Restricted PII for *logging* purposes (it is
    never written to a log line or audit payload, per Requirement 2.6),
    but it is the user's own filename returned to that same user over the
    authenticated, non-indexed API surface for display, so it belongs in
    the response body (Requirements 2.9, 4.2).

    Datetimes serialize as ISO 8601 with timezone (the database columns
    are ``timestamptz``); the ``conventions.md`` "ISO 8601 UTC with Z
    suffix" rule is honored on the wire by Pydantic's default datetime
    formatter when the source value is timezone-aware.

    ``from_attributes=True`` lets the router build a response directly
    from the SQLAlchemy ``Resume`` row via
    ``ResumeResponse.model_validate(resume)``; since the ORM attribute
    set is a strict superset of these fields, the excluded sensitive
    columns are simply never read.
    """

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[uuid.UUID, PlainSerializer(str, return_type=str)] = Field(
        description="UUIDv7 of the Resume, encoded as a string.",
    )
    original_filename: str = Field(
        description="Client-supplied filename, retained for display only. "
        "Never used to derive the storage key or any filesystem path.",
    )
    content_type: str = Field(
        description="Validated media type of the uploaded file "
        "(magic-byte-detected, not the client Content-Type header).",
    )
    byte_size: int = Field(
        description="Size of the stored file in bytes.",
    )
    extraction_status: Literal["pending", "succeeded", "failed"] = Field(
        description="Text-extraction outcome: 'pending' before extraction "
        "runs, 'succeeded' when non-whitespace text was stored, 'failed' "
        "when extraction errored, timed out, or yielded only whitespace.",
    )
    created_at: datetime = Field(
        description="Upload timestamp (timezone-aware).",
    )
    updated_at: datetime = Field(
        description="Last-modified timestamp (timezone-aware). Bumped when "
        "extraction columns are written and on soft delete.",
    )


class ResumeListResponse(BaseModel):
    """Body of ``GET /api/v1/resumes`` (Requirement 4.1).

    A single page of the requesting User_Account's non-deleted resumes,
    ordered by ``created_at`` descending, plus an opaque ``next_cursor``
    for cursor-based pagination per ``conventions.md`` "Pagination"
    (offset pagination is explicitly avoided). ``next_cursor`` is an
    opaque token the client echoes back via the ``cursor`` query
    parameter to fetch the following page; it is ``None`` when there are
    no further pages.

    Each item is a :class:`ResumeResponse`, so the same field-exclusion
    guarantees (no ``extracted_text``, no ``storage_key``) apply to every
    row in the list (Requirement 4.2).
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ResumeResponse] = Field(
        description="The resumes on this page, ordered by created_at descending.",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Opaque pagination cursor for the next page, or null when "
        "this is the last page. Echo back via the ?cursor= query parameter.",
    )


__all__ = [
    "ResumeListResponse",
    "ResumeResponse",
]
