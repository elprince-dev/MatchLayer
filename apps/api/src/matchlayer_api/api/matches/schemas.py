"""Pydantic v2 request/response models for the match surface.

These models are the source of truth for the match section of the
OpenAPI schema FastAPI emits at ``app.openapi()``. ``pnpm codegen``
consumes that schema to regenerate ``packages/shared-types/src/api-types.ts``
(via ``openapi-typescript``) and ``api-schemas.ts`` (via
``openapi-zod-client``); the curated re-exports ``CreateMatchRequest``,
``MatchResponse``, and ``MatchListResponse`` are added to ``index.ts``
(see ``tasks.md`` 13.1). Anything missing here is missing on the
frontend; anything wrong here is wrong on the frontend.

Model coverage (one Pydantic class per HTTP body in
``phase-1-matching/requirements.md`` §8, §9):

Requests:
  * :class:`CreateMatchRequest` -- body of ``POST /api/v1/matches``
    (Requirement 8.1). ``job_description`` carries a field validator that
    enforces the trimmed-length window
    ``MATCHLAYER_JD_MIN_CHARS``..``MATCHLAYER_JD_MAX_CHARS`` and raises so
    a violation returns 422 ``validation_error`` via the foundation RFC
    7807 handler (Requirements 8.2, 8.3).

Responses:
  * :class:`KeywordOut` -- one analyzed keyword ``{term, weight}``,
    mirroring the scorer's ``Keyword`` dataclass and the
    ``match_results.matched_keywords`` / ``missing_keywords`` JSONB shape.
  * :class:`SuggestionOut` -- one rule-based suggestion
    ``{keyword, text}``, mirroring the scorer's ``Suggestion`` dataclass
    and the ``match_results.suggestions`` JSONB shape.
  * :class:`ScoreBreakdownOut` -- the explainable score breakdown,
    mirroring the scorer's ``ScoreBreakdown`` dataclass and the
    ``match_results.score_breakdown`` JSONB shape (Requirement 5.5).
  * :class:`MatchResponse` -- the full Match_Result projection returned by
    ``POST /api/v1/matches`` and ``GET /api/v1/matches/{id}``. Carries
    **exactly** the ten fields Requirement 8.7 enumerates and **never**
    exposes ``job_description_text`` (Restricted PII per ``security.md``;
    Requirement 8.8).
  * :class:`MatchListItem` -- the trimmed projection in the
    ``GET /api/v1/matches`` list: ``{id, resume_id, score, created_at}``,
    which **omits** ``job_description_text`` (Requirement 9.2).
  * :class:`MatchListResponse` -- the ``GET /api/v1/matches`` body: a page
    of :class:`MatchListItem` plus an opaque ``next_cursor`` for
    cursor-based pagination (``conventions.md`` "Pagination";
    Requirement 9.1).

Design references:
  * Components and Interfaces -- ``api/matches/schemas.py`` is "Pydantic
    request/response models" only; no business logic, no DB calls.
  * Pydantic schemas (design §"api/matches/schemas.py") -- the field sets
    below mirror the design's declared shapes verbatim, including
    ``MatchResponse`` deliberately omitting ``job_description_text``.

Requirements covered: 8.1, 8.2, 8.3, 8.7, 9.1, 9.2.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from matchlayer_api.config import get_settings

# ``extra="forbid"`` on request schemas means a body with unknown fields
# is a 422, not a silent ignore -- the same tightening the auth request
# schemas apply so the OpenAPI codegen output is the literal wire shape.
_STRICT_CONFIG: Final[ConfigDict] = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Request models.
# ---------------------------------------------------------------------------


class CreateMatchRequest(BaseModel):
    """Body of ``POST /api/v1/matches`` (Requirement 8.1).

    Fields:
      * ``resume_id`` -- the string UUIDv7 of one of the caller's resumes.
        Ownership / existence / extractability are resolved server-side by
        the Scoring_Service (404 ``not_found`` for a missing, deleted, or
        other-owner resume per Requirement 8.4; 422 ``resume_not_extractable``
        when the resume's ``extraction_status`` is not ``succeeded`` per
        Requirement 8.5). The schema validates only that a non-empty string
        was supplied.
      * ``job_description`` -- the pasted job-description text. A baseline
        ``min_length=1`` floor protects FastAPI's request parser from an
        empty string; the real window is enforced by
        :meth:`_check_job_description_length`, which trims and checks the
        length against ``MATCHLAYER_JD_MIN_CHARS``..``MATCHLAYER_JD_MAX_CHARS``
        (Requirement 8.3). A violation raises ``ValueError``, which Pydantic
        surfaces as a 422 ``validation_error`` through the foundation RFC
        7807 handler (Requirement 8.2).
    """

    model_config = _STRICT_CONFIG

    resume_id: str = Field(
        min_length=1,
        description="UUIDv7 (string) of the resume to score. Ownership and "
        "extractability are resolved server-side; the schema only checks "
        "that a non-empty string was supplied.",
    )
    job_description: str = Field(
        min_length=1,
        description="Pasted job-description text. The trimmed length must fall "
        "within MATCHLAYER_JD_MIN_CHARS..MATCHLAYER_JD_MAX_CHARS "
        "(defaults 30..50000); a violation returns 422 validation_error.",
    )

    @field_validator("job_description")
    @classmethod
    def _check_job_description_length(cls, v: str) -> str:
        """Enforce the trimmed-length window (Requirement 8.3).

        The bounds are read from :func:`get_settings` (cached) so the window
        stays a single source of truth with the rest of the application
        config; ``schemas`` reading settings is permitted for this validator.
        The check is performed on the *trimmed* length (Requirement 8.3
        evaluates the length "after trimming"); the original value is
        returned unchanged so no surrounding whitespace is silently dropped
        from the stored ``job_description_text``.
        """
        settings = get_settings()
        trimmed_length = len(v.strip())
        if trimmed_length < settings.jd_min_chars:
            raise ValueError(
                f"job_description must be at least {settings.jd_min_chars} "
                f"characters after trimming; got {trimmed_length}"
            )
        if trimmed_length > settings.jd_max_chars:
            raise ValueError(
                f"job_description must be at most {settings.jd_max_chars} "
                f"characters after trimming; got {trimmed_length}"
            )
        return v


# ---------------------------------------------------------------------------
# Response sub-models.
# ---------------------------------------------------------------------------


class KeywordOut(BaseModel):
    """One analyzed keyword and its weight (``{term, weight}``).

    Mirrors the scorer's ``Keyword`` dataclass and the
    ``match_results.matched_keywords`` / ``missing_keywords`` JSONB shape.
    Lists of these are returned ordered by descending weight so the most
    important terms appear first (Requirement 6.6).

    ``from_attributes=True`` lets the router validate this either from the
    JSONB list-of-dicts loaded off the ORM row or from the scorer's
    ``Keyword`` dataclass attributes, without renaming.
    """

    model_config = ConfigDict(from_attributes=True)

    term: str = Field(
        description="Normalized keyword term (case-folded; lexicon-canonical for known skills).",
    )
    weight: float = Field(
        description="Relative importance of the term (lexicon weight for a "
        "known skill, otherwise the term's TF-IDF score).",
    )


class SuggestionOut(BaseModel):
    """One rule-based improvement suggestion (``{keyword, text}``).

    Mirrors the scorer's ``Suggestion`` dataclass and the
    ``match_results.suggestions`` JSONB shape. ``keyword`` is the missing
    term the suggestion addresses (empty only for the single affirmative
    suggestion emitted when nothing is missing, per Requirement 7.3);
    ``text`` is the plain-text, user-facing guidance.
    """

    model_config = ConfigDict(from_attributes=True)

    keyword: str = Field(
        description="The missing keyword this suggestion addresses; empty only "
        "for the affirmative 'already covered' suggestion.",
    )
    text: str = Field(
        description="Plain-text guidance phrased as an action for the user to "
        "take. Never fabricates experience, employers, dates, or credentials.",
    )


class ScoreBreakdownOut(BaseModel):
    """The explainable breakdown behind a score (Requirement 5.5).

    Mirrors the scorer's ``ScoreBreakdown`` dataclass and the
    ``match_results.score_breakdown`` JSONB shape field-for-field. The
    similarity and keyword-coverage components are the raw ``[0, 1]`` values
    before weighting, so a reader can recompute
    ``round(100 * (weight_similarity * similarity_component +
    weight_keyword * keyword_coverage_component))`` and arrive at
    ``final_score`` without re-running the algorithm.
    """

    model_config = ConfigDict(from_attributes=True)

    similarity_component: float = Field(
        description="TF-IDF cosine similarity of resume and JD, in [0, 1].",
    )
    keyword_coverage_component: float = Field(
        description="Fraction of the analyzed keyword set present in the resume, in [0, 1].",
    )
    weight_similarity: float = Field(
        description="Weight applied to the similarity component (default 0.6).",
    )
    weight_keyword: float = Field(
        description="Weight applied to the keyword-coverage component (default 0.4).",
    )
    final_score: int = Field(
        description="The combined, clamped integer score in [0, 100]; equals "
        "the enclosing MatchResponse.score.",
    )


# ---------------------------------------------------------------------------
# Response models.
# ---------------------------------------------------------------------------


class MatchResponse(BaseModel):
    """Full Match_Result projection (Requirement 8.7).

    Carries **exactly** the ten fields Requirement 8.7 enumerates: ``id``,
    ``resume_id``, ``score``, ``score_breakdown``, ``matched_keywords``,
    ``missing_keywords``, ``suggestions``, ``scorer_version``,
    ``created_at``, and ``updated_at``. Returned by ``POST /api/v1/matches``
    (201) and ``GET /api/v1/matches/{id}`` (Requirement 9.3).

    ``job_description_text`` is intentionally absent. It is Restricted PII
    per ``security.md`` "Data classification" and the design deliberately
    keeps it out of every match response body (Requirement 8.8); this
    exclusion is a security control, not a convenience.

    ``from_attributes=True`` lets the router build a response directly from
    the SQLAlchemy ``MatchResult`` row via
    ``MatchResponse.model_validate(match)``. The JSONB columns
    (``score_breakdown`` as a dict, ``matched_keywords`` /
    ``missing_keywords`` / ``suggestions`` as lists of dicts) validate
    against the nested models above, and the excluded ``job_description_text``
    column is simply never read.
    """

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[uuid.UUID, PlainSerializer(str, return_type=str)] = Field(
        description="UUIDv7 of the Match_Result, encoded as a string.",
    )
    resume_id: Annotated[uuid.UUID, PlainSerializer(str, return_type=str)] = Field(
        description="UUIDv7 (string) of the resume that was scored.",
    )
    score: int = Field(
        description="The final match score, an integer in [0, 100].",
    )
    score_breakdown: ScoreBreakdownOut = Field(
        description="The explainable component/weight breakdown behind the score.",
    )
    matched_keywords: list[KeywordOut] = Field(
        description="Analyzed keywords present in the resume, ordered by descending weight.",
    )
    missing_keywords: list[KeywordOut] = Field(
        description="Analyzed keywords absent from the resume, ordered by descending weight.",
    )
    suggestions: list[SuggestionOut] = Field(
        description="Rule-based improvement suggestions, ordered by descending "
        "missing-keyword weight.",
    )
    scorer_version: str = Field(
        description="The Scorer_Version (algorithm + lexicon version) the score "
        "was produced under, making the stored score reproducible.",
    )
    created_at: datetime = Field(
        description="Scoring timestamp (timezone-aware).",
    )
    updated_at: datetime = Field(
        description="Last-modified timestamp (timezone-aware).",
    )


class MatchListItem(BaseModel):
    """Trimmed Match_Result projection for the list view (Requirement 9.2).

    Carries the minimum fields Requirement 9.2 enumerates: ``id``,
    ``resume_id``, ``score``, and ``created_at``. ``job_description_text``
    is **omitted** (Restricted PII; the list never carries it), as are the
    heavier JSONB columns (breakdown, keywords, suggestions) which belong on
    the single-match detail view rather than every list row.

    ``from_attributes=True`` lets the router build each item from the
    SQLAlchemy ``MatchResult`` row; the unlisted columns are simply never
    read.
    """

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[uuid.UUID, PlainSerializer(str, return_type=str)] = Field(
        description="UUIDv7 of the Match_Result, encoded as a string.",
    )
    resume_id: Annotated[uuid.UUID, PlainSerializer(str, return_type=str)] = Field(
        description="UUIDv7 (string) of the resume that was scored.",
    )
    score: int = Field(
        description="The final match score, an integer in [0, 100].",
    )
    created_at: datetime = Field(
        description="Scoring timestamp (timezone-aware).",
    )


class MatchListResponse(BaseModel):
    """Body of ``GET /api/v1/matches`` (Requirement 9.1).

    A single page of the requesting User_Account's non-deleted
    Match_Results, ordered by ``created_at`` descending, plus an opaque
    ``next_cursor`` for cursor-based pagination per ``conventions.md``
    "Pagination" (offset pagination is explicitly avoided). ``next_cursor``
    is an opaque token the client echoes back via the ``cursor`` query
    parameter to fetch the following page; it is ``None`` when there are no
    further pages.

    Each item is a :class:`MatchListItem`, so no row in the list carries
    ``job_description_text`` (Requirement 9.2).
    """

    model_config = ConfigDict(extra="forbid")

    items: list[MatchListItem] = Field(
        description="The matches on this page, ordered by created_at descending.",
    )
    next_cursor: str | None = Field(
        default=None,
        description="Opaque pagination cursor for the next page, or null when "
        "this is the last page. Echo back via the ?cursor= query parameter.",
    )


__all__ = [
    "CreateMatchRequest",
    "KeywordOut",
    "MatchListItem",
    "MatchListResponse",
    "MatchResponse",
    "ScoreBreakdownOut",
    "SuggestionOut",
]
