"""LLM_Result persistence and newest-first cursor-paginated queries.

The single access layer for the ``llm_results`` table (design layout:
``services/llm/results.py``). Every row here is a **validated** LLM output —
Fallback_Responses are never persisted (Requirement 9.5); the orchestrator
only reaches :func:`persist_result` with a payload that has passed the
feature's Pydantic result schema.

Public surface:

* :func:`persist_result` — insert one validated LLM_Result row (UUIDv7 id,
  timezone-aware UTC ``created_at`` — Requirement 16.3).
* :func:`find_reusable_result` — the Resume_Coach persisted-result reuse
  lookup (design decision D7, Requirement 5.4): the newest row for a
  ``(match, feature)`` pair under the **same** prompt template version and
  LLM model, so an unchanged version+model serves the stored report without
  a provider call; a version/model change misses and forces a fresh call
  while old rows are retained (Requirement 5.7).
* :func:`list_results` — one newest-first page of a match's results for a
  feature, keyset-paginated on ``(created_at DESC, id DESC)`` exactly like
  ``services/resumes.py`` (Requirements 16.4, 16.10; ``conventions.md``
  "Pagination" — no offset pagination).
* :func:`get_result` — a single owned row by id, or ``None``. The caller
  maps ``None`` to the same 404 envelope whether the row is absent or owned
  by another user (Requirements 16.2, 16.9).

Ownership discipline (Requirement 5.6, ``security.md``): every query is
scoped to the requesting ``user_id``, so a result set can structurally never
include another account's rows. Payload content is Restricted-derived and is
never logged by this module.

Design reference: phase-3-llm-layer §"Backend layout" (``results.py``),
§"Data Models" (``llm_results``). Requirements: 5.4, 5.6, 5.7, 9.5, 16.2,
16.3, 16.4, 16.9, 16.10.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.core.errors import MatchLayerError
from matchlayer_api.db.models import LLMResult
from matchlayer_api.ml.prompts.registry import LLMFeature

__all__ = [
    "LLMResultPage",
    "find_reusable_result",
    "get_result",
    "list_results",
    "persist_result",
]


def _now() -> datetime:
    """Timezone-aware current UTC time (mirrors ``services.matching._now``)."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Cursor pagination (keyset on ``(created_at DESC, id DESC)``).
#
# Mirrors ``services/resumes.py``: the cursor is an opaque URL-safe base64
# token of ``"<created_at isoformat>|<id>"``. A malformed or undecodable
# token is a 422 ``validation_error`` (Requirement 16.10), and the mangled
# value is never echoed back in the error detail.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMResultPage:
    """One newest-first page of a match's LLM_Results for a feature.

    ``items`` are :class:`~matchlayer_api.db.models.LLMResult` ORM rows in
    strictly descending ``(created_at, id)`` order; the router projects each
    onto its response envelope. ``next_cursor`` is the opaque token for the
    following page, or ``None`` on the last page (Requirement 16.4).
    """

    items: list[LLMResult]
    next_cursor: str | None


def _encode_cursor(row: LLMResult) -> str:
    """Return an opaque keyset cursor for *row* (``created_at`` + ``id``)."""
    raw = f"{row.created_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque keyset *cursor* into ``(created_at, id)``.

    Raises:
        MatchLayerError: A 422 ``validation_error`` envelope when the cursor
            is not valid base64url, is not well-formed, or does not parse
            into a timestamp and a UUID (Requirement 16.10). The malformed
            value is never echoed back in the ``detail``.
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


# ---------------------------------------------------------------------------
# Persistence.
# ---------------------------------------------------------------------------


async def persist_result(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_result_id: UUID,
    feature: LLMFeature,
    prompt_template_version: int,
    llm_model: str,
    # dict[str, Any]: the schema-validated JSONB payload — JSON is
    # inherently heterogeneous (nested objects, arrays, strings), so Any is
    # the honest value type, matching the model column.
    payload: dict[str, Any],
) -> LLMResult:
    """Insert one validated LLM_Result row and flush it.

    Called by the orchestrator only after terminal schema validation
    succeeded — fallbacks and invalid outputs never reach this function
    (Requirement 9.5). The id is a UUIDv7 (time-ordered, exposed as a
    string — Requirement 16.3) and ``created_at`` is an explicit
    timezone-aware UTC timestamp so the value is materialized on the ORM
    row without a post-flush refresh (the ``services.matching`` pattern).

    The row is staged and flushed; the router owns the commit.

    Args:
        session: The request-scoped :class:`AsyncSession`.
        user_id: The owning User_Account id.
        match_result_id: The Match_Result the feature ran against.
        feature: Which LLM_Feature produced the payload.
        prompt_template_version: The registry-active Prompt_Template version
            the output was produced under.
        llm_model: The LLM_Model identifier the call was made with.
        payload: The schema-validated structured output as a JSON-shaped dict.

    Returns:
        The newly created, flushed :class:`LLMResult` row.
    """
    row = LLMResult(
        id=uuid7(),
        user_id=user_id,
        match_result_id=match_result_id,
        feature=feature.value,
        prompt_template_version=prompt_template_version,
        llm_model=llm_model,
        payload=payload,
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Queries.
# ---------------------------------------------------------------------------


async def find_reusable_result(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_result_id: UUID,
    feature: LLMFeature,
    prompt_template_version: int,
    llm_model: str,
) -> LLMResult | None:
    """Return the newest persisted result under the same version + model.

    The Resume_Coach persisted-result reuse lookup (design decision D7,
    Requirement 5.4): a hit is served without a provider call and without
    consuming Daily_Quota. The version and model filters make the reuse
    decision decidable from stored data alone — a changed active prompt
    version or configured model simply never matches, so the next POST
    performs a fresh call while old rows are retained (Requirement 5.7).

    Backed by the ``llm_results_match_feature_created_idx`` composite index
    (``match_result_id``, ``feature``, ``created_at DESC``).
    """
    stmt = (
        select(LLMResult)
        .where(
            LLMResult.user_id == user_id,
            LLMResult.match_result_id == match_result_id,
            LLMResult.feature == feature.value,
            LLMResult.prompt_template_version == prompt_template_version,
            LLMResult.llm_model == llm_model,
        )
        .order_by(LLMResult.created_at.desc(), LLMResult.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def list_results(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_result_id: UUID,
    feature: LLMFeature,
    limit: int,
    cursor: str | None,
) -> LLMResultPage:
    """Return one newest-first page of a match's results for *feature*.

    Ordered by ``created_at`` descending with ``id`` descending as the
    deterministic tiebreak (UUIDv7 ids are time-ordered — Requirement 16.4),
    scoped to the owning ``user_id`` (Requirement 5.6). Keyset pagination:
    a non-null ``cursor`` selects only rows strictly "older" than the last
    row of the previous page via a row-value comparison the composite index
    backs. One extra row beyond ``limit`` is fetched to decide whether a
    ``next_cursor`` is warranted without a second COUNT query.

    Args:
        session: The request-scoped :class:`AsyncSession`.
        user_id: The authenticated principal; every query is scoped to it.
        limit: Page size — the router validates it into 1..100 (Requirement
            16.10) before calling.
        match_result_id: The Match_Result whose results are listed.
        feature: The LLM_Feature sub-resource being listed.
        cursor: Opaque token from a previous page, or ``None`` for the
            first page.

    Returns:
        The page's rows plus the next-page cursor (``None`` when exhausted).

    Raises:
        MatchLayerError: A 422 ``validation_error`` when *cursor* is
            malformed (Requirement 16.10).
    """
    stmt = select(LLMResult).where(
        LLMResult.user_id == user_id,
        LLMResult.match_result_id == match_result_id,
        LLMResult.feature == feature.value,
    )
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(LLMResult.created_at, LLMResult.id) < (cursor_created_at, cursor_id)
        )
    stmt = stmt.order_by(LLMResult.created_at.desc(), LLMResult.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    page_items = rows[:limit]
    next_cursor = _encode_cursor(page_items[-1]) if has_more and page_items else None
    return LLMResultPage(items=page_items, next_cursor=next_cursor)


async def get_result(
    session: AsyncSession,
    *,
    user_id: UUID,
    match_result_id: UUID,
    feature: LLMFeature,
    result_id: UUID,
) -> LLMResult | None:
    """Return one owned result row by id, or ``None``.

    ``None`` covers "does not exist", "belongs to another user", "belongs
    to another match", and "belongs to another feature" identically, so the
    router's single 404 envelope discloses nothing about other accounts'
    data (Requirements 16.2, 16.9).
    """
    stmt = select(LLMResult).where(
        LLMResult.id == result_id,
        LLMResult.user_id == user_id,
        LLMResult.match_result_id == match_result_id,
        LLMResult.feature == feature.value,
    )
    return (await session.execute(stmt)).scalars().first()
