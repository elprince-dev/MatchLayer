"""Resume_Coach — the feature spec for overall coaching feedback.

Like its siblings (:mod:`matchlayer_api.services.llm.questions`,
``bullets``), this module contributes only what differs per feature —
the prompt-input builder and the fallback builder — packaged as the
:data:`RESUME_COACH_SPEC` the shared orchestrator executes. Everything
cross-cutting (quota, redaction, caching, spend control, validation,
invocation logging) lives in
:mod:`matchlayer_api.services.llm.orchestrator`.

Feature contract (design §"Feature services", Requirements 5.1-5.7):

* **Inputs** (Req 5.1, 5.3): the redacted resume text and redacted
  Job_Description text, plus the Match_Result's stored
  ``matched_keywords`` / ``missing_keywords`` read verbatim — the
  deterministic Phase 2 analysis grounds the coaching so the LLM never
  re-derives the skill gap from scratch. The skill lists are stored,
  already-PII-free derived data and pass through unredacted
  (``redaction=None``); resume and JD text are Restricted PII and carry
  their redaction kinds.
* **Output schema** (Req 5.2):
  :class:`~matchlayer_api.services.llm.schemas.CoachingReport` —
  summary, strengths, gaps, and 3..10 improvement actions with strictly
  increasing priority ranks. A bound or ordering violation is a
  schema-validation failure the orchestrator maps onto the fallback
  path (Req 8.3); no ``validate_extra`` is needed — every Requirement 5
  bound is expressible in the schema itself.
* **Persisted-result reuse** (Req 5.4, 5.7, design D7): the spec sets
  ``reuse_persisted=True``, opting into the orchestrator's pipeline
  step 2 — the newest persisted Coaching_Report under the *same* active
  Prompt_Template version and configured LLM_Model is served with no
  provider call and no Daily_Quota consumption. A version or model
  change makes the lookup miss, so the next request is a fresh call
  (subject to quota) while previously persisted rows are retained —
  ``results.find_reusable_result`` filters, never deletes.
* **Fallback** (Req 5.5): built exclusively from the Match_Result's
  stored rule-based ``suggestions`` and ``missing_keywords``.
  ``gaps`` mirrors the stored missing skills — an empty stored list is
  carried as an empty ``gaps`` list. ``improvements`` carries the
  stored suggestions in order; because the shared
  :class:`CoachingReport` schema requires at least 3 improvement
  actions (Req 9.2 — fallback content conforms to the *same* schema as
  LLM output), the list is topped up with generic actions when fewer
  than 3 stored suggestions exist, mirroring the
  Interview_Question_Generator's sanctioned generic top-up. Fields
  Requirement 5.5 does not source — ``strengths`` — are carried empty
  rather than fabricated.

PII discipline: this module never logs. The fallback derives only from
stored Match_Result fields owned by the requesting user (Req 9.3).

Design reference: phase-3-llm-layer §"Feature services (coach.py,
bullets.py, questions.py)". Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.schemas import (
    CoachingReport,
    FailureReason,
    ImprovementAction,
)

__all__ = [
    "RESUME_COACH_SPEC",
    "ResumeCoachInput",
    "build_fallback",
    "build_inputs",
]

_MIN_IMPROVEMENTS: Final[int] = 3
_MAX_IMPROVEMENTS: Final[int] = 10
"""The Coaching_Report improvement-count bounds (Req 5.2). Mirror the
``CoachingReport.improvements`` schema field's ``min_length`` /
``max_length``; they fill the template's ``{min_improvements}`` /
``{max_improvements}`` placeholders so the instruction text and the
validation gate can never disagree."""

_EMPTY_SKILLS_TEXT: Final[str] = "(none)"
"""Placeholder for an empty stored skill list, so the delimited prompt
region is never blank and the template's grounding instruction stays
meaningful."""


@dataclass(frozen=True)
class ResumeCoachInput:
    """The feature-specific request input for coaching.

    ``resume_text`` is the owning Resume's ``extracted_text`` (Restricted
    PII — loaded by the router alongside the ownership check, redacted by
    the orchestrator before it enters the prompt, the hash, or the cache
    key). The Match_Result itself carries every other input the feature
    needs.
    """

    resume_text: str


# ---------------------------------------------------------------------------
# Prompt inputs (Req 5.1, 5.3).
# ---------------------------------------------------------------------------


def _format_skills(skills: Sequence[object]) -> str:
    """Format a stored skill list for its delimited prompt region.

    The stored values are included verbatim (Req 5.3 — the prompt is
    grounded in the persisted Phase 2 analysis, never a re-computation);
    joining with a comma-space separator is the only transformation. An
    empty list renders as ``(none)`` so the region is never blank.
    """
    if not skills:
        return _EMPTY_SKILLS_TEXT
    return ", ".join(str(skill) for skill in skills)


def build_inputs(match: MatchResult, feature_input: ResumeCoachInput) -> PromptInputs:
    """Assemble the prompt inputs for one coaching request.

    ``values`` fills the ``resume_coach`` template's
    ``{min_improvements}`` / ``{max_improvements}`` placeholders
    (Req 2.6) — both PII-free constants. ``sections`` carries the four
    delimited user-content regions the v1 template documents, in
    template order: the resume and Job_Description text flagged for
    redaction (Req 3.1), and the stored matched/missing skill lists
    passing through verbatim (Req 5.3).
    """
    return PromptInputs(
        values={
            "min_improvements": str(_MIN_IMPROVEMENTS),
            "max_improvements": str(_MAX_IMPROVEMENTS),
        },
        sections=(
            PromptSection(
                kind="resume",
                text=feature_input.resume_text,
                redaction="resume",
            ),
            PromptSection(
                kind="job_description",
                text=match.job_description_text,
                redaction="job_description",
            ),
            PromptSection(
                kind="matched_skills",
                text=_format_skills(match.matched_keywords),
                redaction=None,
            ),
            PromptSection(
                kind="missing_skills",
                text=_format_skills(match.missing_keywords),
                redaction=None,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Fallback (Req 5.5, 9.2, 9.3).
# ---------------------------------------------------------------------------

_FALLBACK_SUMMARY: Final[str] = (
    "AI coaching is temporarily unavailable, so this report was built from "
    "MatchLayer's deterministic analysis of your resume against this job "
    "description: the skill gaps it detected and its rule-based suggestions."
)
"""The fixed fallback ``summary`` — the schema requires a non-empty
summary, and this one honestly describes how the content was produced
without referencing any Restricted data."""

# Generic improvement actions used to top the fallback up to the schema's
# 3-improvement floor when fewer than 3 stored suggestions exist —
# including when the stored list is empty (Req 5.5 meets the Req 9.2
# same-schema rule the way the question generator's generic top-up does).
# At least ``_MIN_IMPROVEMENTS`` entries so the floor is always reachable
# from zero stored suggestions.
_GENERIC_FALLBACK_ACTIONS: Final[tuple[str, ...]] = (
    "Mirror the job description's own wording for the skills and "
    "responsibilities you genuinely have, so both automated screens and "
    "human reviewers can find them quickly.",
    "Lead each experience bullet with a strong action verb and a concrete, "
    "quantified outcome (numbers, scale, or impact) instead of listing "
    "duties.",
    "Trim content that is not relevant to this specific role so your most "
    "job-relevant experience appears in the top third of the resume.",
)


def _clean_entries(raw: Sequence[object]) -> list[str]:
    """Normalize a stored JSONB list for fallback content.

    Coerces each entry to text, strips surrounding whitespace, drops
    empties, and de-duplicates case-insensitively in first-occurrence
    order — so a stored duplicate never yields two identical fallback
    lines, and a whitespace-only entry never violates the schema's
    non-empty action constraint. (Prompt grounding uses the verbatim
    lists instead; this cleaning is output-quality hygiene for the
    fallback only.)
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
    return cleaned


def build_fallback(
    match: MatchResult,
    feature_input: ResumeCoachInput,
    reason: FailureReason,
) -> CoachingReport:
    """Build the Fallback_Response Coaching_Report (Req 5.5, 9.3).

    Derived exclusively from the Match_Result's stored rule-based
    ``suggestions`` and ``missing_keywords`` — no LLM call, no data
    outside the requesting user's own match:

    * ``gaps`` carries the stored missing skills; an empty stored list is
      carried as an empty ``gaps`` list (Req 5.5).
    * ``improvements`` carries the stored suggestions in stored order
      with priority ranks 1..n, capped at the schema's 10-action ceiling
      and topped up with generic actions to its 3-action floor so the
      result validates against the same :class:`CoachingReport` schema
      as LLM output (Req 9.2).
    * ``strengths`` is carried empty — Requirement 5.5 sources the
      fallback from suggestions and missing skills only, and fabricating
      strengths would overstate what the deterministic analysis claims.

    The failure ``reason`` travels in the response envelope (set by the
    orchestrator), not in the content, so it is unused here;
    ``feature_input`` is part of the uniform builder signature.
    """
    del feature_input, reason  # envelope concerns; content is match-derived only

    gaps = [
        f"The job description asks for {skill}, which MatchLayer's analysis "
        "did not find in your resume."
        for skill in _clean_entries(match.missing_keywords)
    ]

    actions = _clean_entries(match.suggestions)[:_MAX_IMPROVEMENTS]
    for generic in _GENERIC_FALLBACK_ACTIONS:
        if len(actions) >= _MIN_IMPROVEMENTS:
            break
        actions.append(generic)

    return CoachingReport(
        summary=_FALLBACK_SUMMARY,
        strengths=[],
        gaps=gaps,
        improvements=[
            ImprovementAction(priority=rank, action=action)
            for rank, action in enumerate(actions, start=1)
        ],
    )


RESUME_COACH_SPEC: Final[LLMFeatureSpec[ResumeCoachInput, CoachingReport]] = LLMFeatureSpec(
    feature=LLMFeature.RESUME_COACH,
    result_schema=CoachingReport,
    build_inputs=build_inputs,
    build_fallback=build_fallback,
    reuse_persisted=True,
)
"""The Resume_Coach's parameterization of the shared pipeline (design
D1). ``reuse_persisted=True`` opts into the orchestrator's
persisted-result reuse step (design D7, Req 5.4): a stored
Coaching_Report under the same active prompt version + model is served
with no provider call and no quota; a version/model change misses the
lookup, making the next request a fresh call while old rows are retained
(Req 5.7). No ``validate_extra`` — every Requirement 5 bound is enforced
by the :class:`CoachingReport` schema itself."""
