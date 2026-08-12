"""Pydantic v2 result schemas for the Phase 3 LLM features.

These models are the single source of truth for every LLM_Feature
response shape (Requirement 8.4): FastAPI exposes them through the
OpenAPI schema, and ``pnpm codegen`` regenerates the TypeScript types
(``openapi-typescript``) and Zod schemas (``openapi-zod-client``) in
``packages/shared-types/`` from that schema. Anything missing here is
missing on the frontend; anything wrong here is wrong on the frontend.

They serve double duty (design §"Pydantic response schemas"):

* **Provider constraint** — the ``output_schema`` sent to the LLM
  provider (Requirement 8.1) is ``result_schema.model_json_schema()``,
  so the same field bounds that gate server-side validation also
  constrain the model's structured output. ``extra="forbid"`` maps to
  ``additionalProperties: false`` in the emitted JSON Schema, which
  strict structured-output modes require.
* **Terminal validation** — at stream end the accumulated response is
  parsed and validated against the requesting feature's result schema
  (Requirement 8.2); a violation takes the Fallback_Response path,
  never a best-effort parse (Requirement 8.3).

Fallback content conforms to the *same* ``result`` schema as LLM
output (Requirements 5.5, 6.6, 7.5), so the frontend renders exactly
one shape per feature and distinguishes the two only by the envelope's
``is_fallback`` marker (Requirement 9.2).

Model coverage:

* :class:`FailureReason` — the closed failure-category enumeration
  (Requirement 9.2).
* :class:`ImprovementAction` / :class:`CoachingReport` — the
  Resume_Coach output (Requirement 5.2).
* :class:`BulletRewriteEntry` / :class:`BulletRewrite` — the
  Bullet_Rewriter output (Requirement 6.2).
* :class:`InterviewQuestion` / :class:`InterviewQuestionSet` — the
  Interview_Question_Generator output (Requirements 7.2, 7.3).
* :class:`LLMResultEnvelope` — the generic envelope every feature
  response is wrapped in (Requirements 9.2, 17.9).
"""

from __future__ import annotations

import itertools
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from matchlayer_api.config import get_settings

# ``extra="forbid"`` on every LLM output schema: an off-schema key in a
# model response is a validation failure → Fallback_Response path
# (Requirement 8.3), and the emitted JSON Schema carries
# ``additionalProperties: false`` as strict provider structured-output
# modes require (Requirement 8.1).
_STRICT_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid")

# Interview_Question_Set floor (Requirement 7.2 via design). The
# configurable ceiling ``MATCHLAYER_LLM_MAX_QUESTIONS`` is validated at
# startup to be >= this floor (config.py, Requirement 7.8), so the
# range 5..llm_max_questions is never empty.
_MIN_QUESTIONS: Final[int] = 5

# A string that is non-empty after stripping surrounding whitespace.
# ``strip_whitespace=True`` normalizes the value before the length
# check, so a whitespace-only string fails ``min_length=1`` rather than
# sneaking through as "technically non-empty". The min/max bounds
# surface as ``minLength``/``maxLength`` in the JSON Schema sent to the
# provider.
_NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FailureReason(StrEnum):
    """Closed set of LLM failure categories (Requirement 9.2).

    Every fallback-producing failure in the pipeline maps to exactly one
    of these values (design §"Orchestrator" failure taxonomy). The same
    values are recorded as ``llm_invocation_logs.failure_category`` and
    exposed to the frontend via :class:`LLMResultEnvelope.fallback_reason`
    so the Web_App can label degraded content honestly (Requirement 17.4).
    """

    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    REDACTION_FAILED = "redaction_failed"
    PROMPT_TEMPLATE_MISSING = "prompt_template_missing"
    # Redis unreachable during quota accounting → fallback without a
    # provider call (Requirement 13.8).
    QUOTA_ACCOUNTING_UNAVAILABLE = "quota_accounting_unavailable"
    # API key absent at startup → LLM_Unavailable fallback (Requirement 10.3).
    LLM_UNAVAILABLE = "llm_unavailable"


# ---------------------------------------------------------------------------
# Resume_Coach — Coaching_Report (Requirement 5.2).
# ---------------------------------------------------------------------------


class ImprovementAction(BaseModel):
    """One concrete improvement action with its explicit priority rank.

    ``priority`` is a rank: 1 is the highest-priority action, larger
    values are progressively lower priority (Requirement 5.2 — "each
    action carries an explicit priority rank").
    """

    model_config = _STRICT_CONFIG

    priority: int = Field(
        ge=1,
        description="Explicit priority rank: 1 is the highest-priority action; "
        "larger values are lower priority.",
    )
    action: _NonEmptyStr = Field(
        description="Concrete, user-actionable improvement instruction.",
    )


class CoachingReport(BaseModel):
    """The Resume_Coach structured output (Requirement 5.2).

    Overall feedback on a resume against a specific job description:
    a summary, strengths, gaps, and 3..10 prioritized improvement
    actions ordered from highest to lowest priority. A response
    violating the bounds or the ordering fails schema validation and
    takes the Fallback_Response path (Requirement 8.3).
    """

    model_config = _STRICT_CONFIG

    summary: _NonEmptyStr = Field(
        description="Overall summary of how the resume matches the job description.",
    )
    strengths: list[str] = Field(
        description="Strengths of the resume relative to the job description.",
    )
    gaps: list[str] = Field(
        description="Gaps or weaknesses of the resume relative to the job description.",
    )
    improvements: list[ImprovementAction] = Field(
        min_length=3,
        max_length=10,
        description="3 to 10 concrete improvement actions, ordered from highest "
        "priority (rank 1) to lowest priority.",
    )

    @field_validator("improvements")
    @classmethod
    def _check_descending_priority_order(
        cls, v: list[ImprovementAction]
    ) -> list[ImprovementAction]:
        """Enforce highest-to-lowest priority ordering (Requirement 5.2).

        ``priority`` is a rank (1 = highest priority), so a list ordered
        "from highest to lowest priority" carries strictly increasing
        rank values. Strict monotonicity also guarantees distinct ranks —
        two actions sharing a rank would make the required ordering
        ambiguous.
        """
        ranks = [item.priority for item in v]
        for previous, current in itertools.pairwise(ranks):
            if current <= previous:
                raise ValueError(
                    "improvements must be ordered from highest to lowest "
                    "priority (strictly increasing priority ranks); got rank "
                    f"{current} after rank {previous}"
                )
        return v


# ---------------------------------------------------------------------------
# Bullet_Rewriter — Bullet_Rewrite (Requirement 6.2).
# ---------------------------------------------------------------------------


class BulletRewriteEntry(BaseModel):
    """One submitted bullet paired with its rewritten alternatives.

    ``original`` must byte-for-byte match a submitted bullet text — the
    post-validation alignment check in the Bullet_Rewriter feature
    service compares it exactly (Requirement 6.7), so no whitespace
    normalization is applied here.
    """

    model_config = _STRICT_CONFIG

    original: str = Field(
        description="The submitted bullet text, exactly as submitted.",
    )
    alternatives: list[_NonEmptyStr] = Field(
        min_length=1,
        max_length=3,
        description="1 to 3 rewritten alternatives targeting the job description.",
    )
    rationale: _NonEmptyStr = Field(
        description="Non-empty explanation of how the rewrite better targets the job description.",
    )


class BulletRewrite(BaseModel):
    """The Bullet_Rewriter structured output (Requirement 6.2).

    Exactly one entry per submitted bullet, in submission order. The
    count/order/original-text alignment against the actual submission is
    enforced by the feature service (Requirement 6.7) because the
    submitted bullets are request state this schema cannot see; the
    schema itself guarantees at least one entry and each entry's bounds.
    """

    model_config = _STRICT_CONFIG

    entries: list[BulletRewriteEntry] = Field(
        min_length=1,
        description="One entry per submitted bullet, in submission order.",
    )


# ---------------------------------------------------------------------------
# Interview_Question_Generator — Interview_Question_Set (Requirements 7.2, 7.3).
# ---------------------------------------------------------------------------


class InterviewQuestionCategory(StrEnum):
    """Closed category set for interview questions (Requirement 7.2)."""

    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    EXPERIENCE_GAP = "experience-gap"


class InterviewQuestion(BaseModel):
    """One likely interview question (Requirement 7.2)."""

    model_config = _STRICT_CONFIG

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ] = Field(
        description="The question text: non-empty, at most 300 characters.",
    )
    category: InterviewQuestionCategory = Field(
        description="Exactly one of 'technical', 'behavioral', or 'experience-gap'.",
    )
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] = Field(
        description="Non-empty grounding (at most 500 characters) for why this "
        "resume + job-description pair makes the question likely.",
    )


class InterviewQuestionSet(BaseModel):
    """The Interview_Question_Generator structured output (Requirement 7.3).

    Between 5 and ``MATCHLAYER_LLM_MAX_QUESTIONS`` (default 15) questions.
    An out-of-bounds count is a validation failure — never truncated or
    padded (Requirement 7.7). The floor is a schema constant; the ceiling
    is read from settings by the validator below (design: "5..
    llm_max_questions (validator reads settings)"), mirroring the
    settings-reading validator precedent in ``api/matches/schemas.py``.
    Startup validation guarantees the ceiling is >= the floor
    (Requirement 7.8).
    """

    model_config = _STRICT_CONFIG

    questions: list[InterviewQuestion] = Field(
        min_length=_MIN_QUESTIONS,
        description="5 to MATCHLAYER_LLM_MAX_QUESTIONS likely interview questions.",
    )

    @field_validator("questions")
    @classmethod
    def _check_question_count_ceiling(cls, v: list[InterviewQuestion]) -> list[InterviewQuestion]:
        """Enforce the configurable upper bound (Requirement 7.3).

        The floor (5) is declared statically on the field so it appears in
        the JSON Schema sent to the provider; the ceiling is configuration
        (``MATCHLAYER_LLM_MAX_QUESTIONS``) and therefore checked here at
        validation time against the cached settings.
        """
        max_questions = get_settings().llm_max_questions
        if len(v) > max_questions:
            raise ValueError(
                f"questions must contain at most {max_questions} items "
                f"(MATCHLAYER_LLM_MAX_QUESTIONS); got {len(v)}"
            )
        return v


# ---------------------------------------------------------------------------
# Generic response envelope (Requirements 9.2, 17.9).
# ---------------------------------------------------------------------------


class LLMResultEnvelope[T: BaseModel](BaseModel):
    """The envelope every LLM feature response is wrapped in.

    Carried by non-streaming 200 bodies and by the ``complete`` /
    ``degraded`` SSE terminal events (design §"SSE wire format"). The
    ``result`` payload conforms to the same schema whether it was
    LLM-produced or fallback-built, so the frontend renders one shape
    per feature and relies solely on ``is_fallback`` to label degraded
    content (Requirements 9.2, 17.4).

    Field nullability encodes the fallback distinction: fallbacks are
    never persisted (Requirement 9.5), so ``id``,
    ``prompt_template_version``, and ``created_at`` are ``None`` for
    them, while persisted LLM results carry all three (Requirement 17.9).
    """

    model_config = _STRICT_CONFIG

    id: str | None = Field(
        default=None,
        description="UUIDv7 (string) of the persisted LLM_Result; null for "
        "fallbacks, which are never persisted.",
    )
    is_fallback: bool = Field(
        description="True when the result is a Fallback_Response built without "
        "the LLM; false for validated LLM output.",
    )
    fallback_reason: FailureReason | None = Field(
        default=None,
        description="The failure category that triggered the fallback; null "
        "for LLM-produced results.",
    )
    prompt_template_version: int | None = Field(
        default=None,
        description="The active Prompt_Template version the result was "
        "produced under; null for fallbacks.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Persistence timestamp (timezone-aware) of the LLM_Result; null for fallbacks.",
    )
    result: T = Field(
        description="The feature payload (CoachingReport, BulletRewrite, or "
        "InterviewQuestionSet). Fallback content conforms to the same schema.",
    )


__all__ = [
    "BulletRewrite",
    "BulletRewriteEntry",
    "CoachingReport",
    "FailureReason",
    "ImprovementAction",
    "InterviewQuestion",
    "InterviewQuestionCategory",
    "InterviewQuestionSet",
    "LLMResultEnvelope",
]
