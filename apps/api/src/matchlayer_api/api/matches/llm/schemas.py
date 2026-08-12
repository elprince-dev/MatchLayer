"""HTTP-layer schemas for the LLM sub-resource routers.

Concrete response models the routers expose through OpenAPI (design
§"Routers and SSE"; Requirements 8.4, 16.6): the parameterized
:class:`~matchlayer_api.services.llm.schemas.LLMResultEnvelope` aliases
(one per feature, so the generated TypeScript/Zod types in
``packages/shared-types/`` cover each feature's exact ``result`` shape)
and the cursor-paginated list envelopes (Requirement 16.4).

The feature ``result`` schemas themselves (CoachingReport, BulletRewrite,
InterviewQuestionSet) and the generic envelope live in
:mod:`matchlayer_api.services.llm.schemas` — the single source of truth
for every LLM response shape. The Bullet_Rewriter's request body
(:class:`~matchlayer_api.services.llm.bullets.BulletRewriteRequest`,
Requirement 6.3) is re-exported here so the router imports every wire
model from one place.

List envelopes mirror the ``{items, next_cursor}`` shape of the resumes
and matches list responses (``conventions.md`` "Pagination"): items are
full envelopes (the frontend loads the newest persisted result from the
list on page load, Requirement 17.8), ``next_cursor`` is the opaque token
for the following page or ``None`` on the last page.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from matchlayer_api.services.llm.bullets import BulletRewriteRequest
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    CoachingReport,
    InterviewQuestionSet,
    LLMResultEnvelope,
)

__all__ = [
    "BulletRewriteEnvelope",
    "BulletRewriteListResponse",
    "BulletRewriteRequest",
    "CoachingReportEnvelope",
    "CoachingReportListResponse",
    "InterviewQuestionSetEnvelope",
    "InterviewQuestionSetListResponse",
]


# ---------------------------------------------------------------------------
# Concrete envelope parameterizations (Requirements 8.4, 9.2).
#
# ``LLMResultEnvelope[T]`` is generic; FastAPI needs the concrete
# parameterization per endpoint so the OpenAPI schema (and therefore the
# generated TS/Zod types) carries each feature's exact ``result`` shape.
# Pydantic caches parameterizations, so these aliases are the *same*
# runtime classes the orchestrator's envelopes are instances of — the
# routers return orchestrator envelopes through these response models
# without any re-validation mismatch.
# ---------------------------------------------------------------------------

CoachingReportEnvelope = LLMResultEnvelope[CoachingReport]
"""The Resume_Coach response envelope (Requirement 5.2)."""

BulletRewriteEnvelope = LLMResultEnvelope[BulletRewrite]
"""The Bullet_Rewriter response envelope (Requirement 6.2)."""

InterviewQuestionSetEnvelope = LLMResultEnvelope[InterviewQuestionSet]
"""The Interview_Question_Generator response envelope (Requirement 7.2)."""


# ---------------------------------------------------------------------------
# Cursor-paginated list envelopes (Requirements 16.4, 16.10).
# ---------------------------------------------------------------------------


class CoachingReportListResponse(BaseModel):
    """One newest-first page of a match's persisted Coaching_Reports."""

    items: list[CoachingReportEnvelope] = Field(
        description="Persisted Coaching_Reports in descending created_at order.",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page; null on the last page. "
        "Clients pass it back unmodified.",
    )


class BulletRewriteListResponse(BaseModel):
    """One newest-first page of a match's persisted Bullet_Rewrites."""

    items: list[BulletRewriteEnvelope] = Field(
        description="Persisted Bullet_Rewrites in descending created_at order.",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page; null on the last page. "
        "Clients pass it back unmodified.",
    )


class InterviewQuestionSetListResponse(BaseModel):
    """One newest-first page of a match's persisted Interview_Question_Sets."""

    items: list[InterviewQuestionSetEnvelope] = Field(
        description="Persisted Interview_Question_Sets in descending created_at order.",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page; null on the last page. "
        "Clients pass it back unmodified.",
    )
