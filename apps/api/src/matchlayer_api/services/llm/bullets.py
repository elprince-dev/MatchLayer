"""Bullet_Rewriter — the feature spec for resume bullet rewriting.

Like its siblings (:mod:`matchlayer_api.services.llm.coach`,
:mod:`matchlayer_api.services.llm.questions`), this module contributes
only what differs per feature — the request-body validation, the
prompt-input builder, the post-schema alignment check, and the fallback
builder — packaged as the :data:`BULLET_REWRITE_SPEC` the shared
orchestrator executes. Everything cross-cutting (quota, redaction,
caching, spend control, schema validation, invocation logging) lives in
:mod:`matchlayer_api.services.llm.orchestrator`.

Feature contract (design §"Feature services", Requirements 6.1-6.7):

* **Request validation** (Req 6.3): :class:`BulletRewriteRequest` is the
  Pydantic body of ``POST /api/v1/matches/{matchId}/bullet-rewrites``.
  It rejects — as a FastAPI 422 RFC 7807 response, before any redaction,
  quota accounting, or LLM_Provider work — a bullet count outside
  1..``MATCHLAYER_LLM_MAX_BULLETS``, any empty or whitespace-only bullet,
  and any bullet longer than ``MATCHLAYER_LLM_MAX_BULLET_CHARS``
  characters. Accepted bullet text is preserved **exactly as submitted**
  (no stripping or normalization) because the alignment check below
  compares byte-for-byte.
* **Inputs** (Req 6.1, 6.4): the submitted bullets (numbered, in
  submission order), the Match_Result's Job_Description text, and its
  stored ``missing_keywords`` read verbatim — so rewrites target the
  specific job, never generic resume advice. Bullets and JD text are
  Restricted PII and carry their redaction kinds; the stored skill list
  is already-PII-free derived data and passes through unredacted.
* **Output schema** (Req 6.2):
  :class:`~matchlayer_api.services.llm.schemas.BulletRewrite` — at least
  one entry, each with 1..3 alternatives and a non-empty rationale. The
  per-request bounds the schema cannot see (entry count == submitted
  count, submission order, exact ``original`` match) are enforced by
  :func:`validate_alignment`, the spec's ``validate_extra`` hook: any
  deviation raises :class:`ValueError`, which the orchestrator treats as
  a schema-validation failure onto the fallback path (Req 6.7).
* **Fallback** (Req 6.6): each submitted bullet unchanged (as both the
  entry's ``original`` and its single alternative), paired with a
  rationale built exclusively from the Match_Result's stored
  ``missing_keywords`` and rule-based ``suggestions`` — no LLM call, no
  data outside the requesting user's own match (Req 9.2, 9.3). Empty
  stored lists yield a generic guidance sentence, keeping the rationale
  schema-conformant.

PII discipline: this module never logs. Submitted bullet text is
Restricted PII; it travels only inside :class:`BulletRewriteInput` and
the prompt sections the orchestrator redacts (Req 3.1).

Design reference: phase-3-llm-layer §"Feature services (coach.py,
bullets.py, questions.py)". Requirements: 6.1, 6.2, 6.3, 6.4, 6.6, 6.7.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from matchlayer_api.config import get_settings
from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    BulletRewriteEntry,
    FailureReason,
)

__all__ = [
    "BULLET_REWRITE_SPEC",
    "BulletRewriteInput",
    "BulletRewriteRequest",
    "build_fallback",
    "build_inputs",
    "validate_alignment",
]

_MIN_ALTERNATIVES: Final[int] = 1
_MAX_ALTERNATIVES: Final[int] = 3
"""Per-entry alternative bounds (Req 6.2). Mirror the
``BulletRewriteEntry.alternatives`` schema bounds; surfaced to the LLM
through the template's ``{min_alternatives}`` / ``{max_alternatives}``
placeholders so prompt and validation state the same contract."""

_EMPTY_SKILLS_TEXT: Final[str] = "(none)"
"""Placeholder for an empty stored skill list, so the delimited prompt
region is never blank and the template's targeting instruction stays
meaningful."""

# Caps on how many stored items the fallback rationale enumerates —
# output-quality hygiene only (the full lists remain in the Match_Result
# the user already sees on the results page).
_FALLBACK_MAX_SKILLS: Final[int] = 5
_FALLBACK_MAX_SUGGESTIONS: Final[int] = 3


# ---------------------------------------------------------------------------
# Request validation (Req 6.3) — 422 before any LLM work.
# ---------------------------------------------------------------------------


class BulletRewriteRequest(BaseModel):
    """Body of ``POST /api/v1/matches/{matchId}/bullet-rewrites`` (Req 6.3).

    Validation failures surface as FastAPI's 422 RFC 7807 response before
    the router touches the orchestrator, so no redaction, quota, cache, or
    LLM_Provider work happens for an invalid submission. Bullet text is
    accepted exactly as submitted — no stripping — because the
    Requirement 6.7 alignment check compares ``original`` byte-for-byte
    against the submitted text.

    The count ceiling and per-bullet length ceiling are configuration
    (``MATCHLAYER_LLM_MAX_BULLETS`` / ``MATCHLAYER_LLM_MAX_BULLET_CHARS``)
    and therefore checked by the validator below at validation time,
    mirroring the settings-reading validator precedent in
    ``services/llm/schemas.py`` (InterviewQuestionSet's ceiling).
    """

    model_config = ConfigDict(extra="forbid")

    bullets: list[str] = Field(
        min_length=1,
        description="1 to MATCHLAYER_LLM_MAX_BULLETS resume bullet texts to "
        "rewrite, in the order they should be rewritten. None may be empty "
        "or whitespace-only; each is at most MATCHLAYER_LLM_MAX_BULLET_CHARS "
        "characters.",
    )

    @field_validator("bullets")
    @classmethod
    def _check_bullet_bounds(cls, v: list[str]) -> list[str]:
        """Enforce the configurable request bounds (Requirement 6.3).

        The floor (1 bullet) is declared statically on the field so it
        appears in the OpenAPI schema; the count ceiling and per-bullet
        character ceiling are configuration and checked here. Error
        messages carry positions and limits only — never bullet content,
        which is Restricted PII.
        """
        settings = get_settings()
        if len(v) > settings.llm_max_bullets:
            raise ValueError(
                f"bullets must contain at most {settings.llm_max_bullets} "
                f"items (MATCHLAYER_LLM_MAX_BULLETS); got {len(v)}"
            )
        for position, bullet in enumerate(v, start=1):
            if not bullet.strip():
                raise ValueError(f"bullet {position} is empty or whitespace-only")
            if len(bullet) > settings.llm_max_bullet_chars:
                raise ValueError(
                    f"bullet {position} exceeds {settings.llm_max_bullet_chars} "
                    f"characters (MATCHLAYER_LLM_MAX_BULLET_CHARS); got "
                    f"{len(bullet)}"
                )
        return v


@dataclass(frozen=True)
class BulletRewriteInput:
    """The feature-specific request input for bullet rewriting.

    ``bullets`` is the validated submission, exactly as submitted and in
    submission order (Restricted PII — redacted by the orchestrator before
    it enters the prompt, the hash, or the cache key). Stored as a tuple
    so the input is immutable across the pipeline and the alignment check
    compares against precisely what was submitted.
    """

    bullets: tuple[str, ...]


# ---------------------------------------------------------------------------
# Prompt inputs (Req 6.1, 6.4).
# ---------------------------------------------------------------------------


def _format_bullets(bullets: Sequence[str]) -> str:
    """Format the submitted bullets for their delimited prompt region.

    One numbered line per bullet, in submission order, exactly as the
    ``bullet_rewrite`` template documents its ``bullets`` region. The
    bullet text itself is included verbatim (redaction happens downstream
    in the orchestrator); numbering is the only addition.
    """
    return "\n".join(f"{position}. {bullet}" for position, bullet in enumerate(bullets, start=1))


def _format_skills(skills: Sequence[object]) -> str:
    """Format the stored missing-skill list for its delimited prompt region.

    The stored values are included verbatim (Req 6.4 — the prompt is
    grounded in the persisted Phase 2 analysis, never a re-computation);
    joining with a comma-space separator is the only transformation. An
    empty list renders as ``(none)`` so the region is never blank.
    """
    if not skills:
        return _EMPTY_SKILLS_TEXT
    return ", ".join(str(skill) for skill in skills)


def build_inputs(match: MatchResult, feature_input: BulletRewriteInput) -> PromptInputs:
    """Assemble the prompt inputs for one bullet-rewrite request.

    ``values`` fills the ``bullet_rewrite`` template's ``{bullet_count}`` /
    ``{min_alternatives}`` / ``{max_alternatives}`` placeholders (Req 2.6)
    — all PII-free. ``sections`` carries the three delimited user-content
    regions the v1 template documents, in template order: the submitted
    bullets and the Job_Description text flagged for redaction (Req 3.1),
    and the stored missing-skill list passing through verbatim (Req 6.4).
    """
    return PromptInputs(
        values={
            "bullet_count": str(len(feature_input.bullets)),
            "min_alternatives": str(_MIN_ALTERNATIVES),
            "max_alternatives": str(_MAX_ALTERNATIVES),
        },
        sections=(
            PromptSection(
                kind="bullets",
                text=_format_bullets(feature_input.bullets),
                redaction="bullet",
            ),
            PromptSection(
                kind="job_description",
                text=match.job_description_text,
                redaction="job_description",
            ),
            PromptSection(
                kind="missing_skills",
                text=_format_skills(match.missing_keywords),
                redaction=None,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Post-schema alignment check (Req 6.7).
# ---------------------------------------------------------------------------


def validate_alignment(
    match: MatchResult,
    feature_input: BulletRewriteInput,
    result: BulletRewrite,
) -> None:
    """Verify the response aligns with the submission (Requirement 6.7).

    The ``BulletRewrite`` schema cannot see the request, so this hook
    enforces the per-request bounds after Pydantic validation: exactly one
    entry per submitted bullet, in submission order, with each entry's
    ``original`` matching the submitted text **exactly** (byte-for-byte —
    no whitespace normalization, per the schema's documented contract).
    Any deviation raises :class:`ValueError`, which the orchestrator maps
    onto ``schema_validation_failed`` and the fallback path. Error
    messages carry positions and counts only — never bullet content.
    """
    del match  # alignment is between the submission and the response only
    submitted = feature_input.bullets
    if len(result.entries) != len(submitted):
        raise ValueError(
            f"bullet rewrite must contain exactly one entry per submitted "
            f"bullet: submitted {len(submitted)}, got {len(result.entries)}"
        )
    for position, (bullet, entry) in enumerate(
        zip(submitted, result.entries, strict=True), start=1
    ):
        if entry.original != bullet:
            raise ValueError(
                f"bullet rewrite entry {position} does not match the "
                f"submitted bullet at that position exactly"
            )


# ---------------------------------------------------------------------------
# Fallback (Req 6.6, 9.2, 9.3).
# ---------------------------------------------------------------------------


def _clean_items(raw: Sequence[object], *, limit: int) -> list[str]:
    """Normalize a stored list for fallback rationale templating.

    Coerces each entry to text, strips surrounding whitespace, drops
    empties, de-duplicates case-insensitively in first-occurrence order,
    and caps the count — so one stored duplicate never repeats in the
    guidance. (Prompt grounding uses the verbatim list instead; this
    cleaning is output-quality hygiene for the fallback only.)
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw:
        text = str(entry).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _build_guidance(match: MatchResult) -> str:
    """Build the fallback rationale from stored match fields (Req 6.6).

    Derived exclusively from the Match_Result's stored ``missing_keywords``
    and rule-based ``suggestions`` — empty lists simply contribute nothing,
    and the fixed lead sentence keeps the rationale non-empty (and thus
    schema-conformant) even when both lists are empty.
    """
    parts = ["AI rewriting was unavailable, so this bullet is returned unchanged."]
    missing = _clean_items(match.missing_keywords, limit=_FALLBACK_MAX_SKILLS)
    if missing:
        parts.append(
            "Where truthful, consider working in the skills this job asks "
            f"for that MatchLayer did not find in your resume: {', '.join(missing)}."
        )
    suggestions = _clean_items(match.suggestions, limit=_FALLBACK_MAX_SUGGESTIONS)
    if suggestions:
        parts.append("MatchLayer's analysis also suggests: " + " ".join(suggestions))
    return " ".join(parts)


def build_fallback(
    match: MatchResult,
    feature_input: BulletRewriteInput,
    reason: FailureReason,
) -> BulletRewrite:
    """Build the Fallback_Response bullet rewrite (Req 6.6, 9.3).

    Each submitted bullet comes back unchanged — as the entry's
    ``original`` and as its single alternative — paired with guidance
    built exclusively from the Match_Result's stored missing skills and
    rule-based suggestions. No LLM call, no data outside the requesting
    user's own match. The result conforms to the same
    :class:`BulletRewrite` schema as LLM output (Req 9.2); the failure
    ``reason`` travels in the response envelope (set by the orchestrator),
    not in the content, so it is unused here.
    """
    del reason  # envelope concern; content is match-derived only
    guidance = _build_guidance(match)
    return BulletRewrite(
        entries=[
            BulletRewriteEntry(
                original=bullet,
                alternatives=[bullet],
                rationale=guidance,
            )
            for bullet in feature_input.bullets
        ]
    )


BULLET_REWRITE_SPEC: Final[LLMFeatureSpec[BulletRewriteInput, BulletRewrite]] = LLMFeatureSpec(
    feature=LLMFeature.BULLET_REWRITE,
    result_schema=BulletRewrite,
    build_inputs=build_inputs,
    build_fallback=build_fallback,
    validate_extra=validate_alignment,
)
"""The Bullet_Rewriter's parameterization of the shared pipeline (design
D1). ``validate_extra`` carries the Requirement 6.7 alignment check the
schema cannot express; no persisted-result reuse (that step is the
Resume_Coach's, design D7) — repeat suppression comes from the LLM_Cache
(Req 15.2), and persisted results stay retrievable via GET (Req 6.5)."""
