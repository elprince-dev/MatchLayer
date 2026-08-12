"""Interview_Question_Generator — the feature spec for interview questions.

The thinnest of the three Phase 3 feature services (design D1): this
module contributes only what differs per feature — the prompt-input
builder and the fallback builder — packaged as the
:data:`INTERVIEW_QUESTIONS_SPEC` the shared orchestrator executes.
Everything cross-cutting (quota, redaction, caching, spend control,
validation, invocation logging) lives in
:mod:`matchlayer_api.services.llm.orchestrator`.

Feature contract (design §"Feature services", Requirements 7.1-7.7):

* **Inputs** (Req 7.1, 7.6): the redacted resume text and redacted
  Job_Description text, plus the Match_Result's stored
  ``matched_keywords`` / ``missing_keywords`` read verbatim — the
  deterministic Phase 2 analysis grounds the questions (particularly
  ``experience-gap`` ones) so the LLM never re-derives the gap. The
  skill lists are stored, already-PII-free derived data and pass
  through unredacted (``redaction=None``); resume and JD text are
  Restricted PII and carry their redaction kinds.
* **Output schema** (Req 7.2, 7.3):
  :class:`~matchlayer_api.services.llm.schemas.InterviewQuestionSet` —
  5..``MATCHLAYER_LLM_MAX_QUESTIONS`` questions, closed category enum,
  question <= 300 chars, reason <= 500 chars. An out-of-bounds count is
  a schema-validation failure the orchestrator maps onto the fallback
  path; the response is never truncated, padded, or partially
  delivered (Req 7.7, 8.3). No ``validate_extra`` is needed — unlike
  the Bullet_Rewriter, every bound is expressible in the schema itself.
* **Fallback** (Req 7.5): template-based questions built exclusively
  from the Match_Result's stored matched/missing skills —
  ``experience-gap`` questions from missing skills, ``technical``
  depth questions from matched skills — topped up with generic
  questions so the set always carries at least 5 entries (generics are
  the entire set when both stored lists are empty), capped at the
  configured ceiling, conforming to the same
  :class:`InterviewQuestionSet` schema as LLM output (Req 9.2, 9.3).

Startup validation guarantees ``llm_max_questions >= 5`` (config.py,
Req 7.8), so the fallback's floor always fits under the ceiling.

PII discipline: this module never logs. The fallback derives only from
stored Match_Result fields owned by the requesting user (Req 9.3).

Design reference: phase-3-llm-layer §"Feature services (coach.py,
bullets.py, questions.py)". Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from matchlayer_api.config import get_settings
from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.orchestrator import (
    LLMFeatureSpec,
    PromptInputs,
    PromptSection,
)
from matchlayer_api.services.llm.schemas import (
    FailureReason,
    InterviewQuestion,
    InterviewQuestionCategory,
    InterviewQuestionSet,
)

__all__ = [
    "INTERVIEW_QUESTIONS_SPEC",
    "InterviewQuestionsInput",
    "build_fallback",
    "build_inputs",
]

_MIN_QUESTIONS: Final[int] = 5
"""The Interview_Question_Set floor (Req 7.3). Mirrors the schema's
``min_length``; the configurable ceiling comes from settings."""

_QUESTION_MAX_CHARS: Final[int] = 300
_REASON_MAX_CHARS: Final[int] = 500
"""Schema field bounds (Req 7.2), used to length-guard templated
fallback questions built from arbitrary stored skill strings."""

_EMPTY_SKILLS_TEXT: Final[str] = "(none)"
"""Placeholder for an empty stored skill list, so the delimited prompt
region is never blank and the template's grounding instruction stays
meaningful."""


@dataclass(frozen=True)
class InterviewQuestionsInput:
    """The feature-specific request input for interview questions.

    ``resume_text`` is the owning Resume's ``extracted_text`` (Restricted
    PII — loaded by the router alongside the ownership check, redacted by
    the orchestrator before it enters the prompt, the hash, or the cache
    key). The Match_Result itself carries every other input the feature
    needs.
    """

    resume_text: str


# ---------------------------------------------------------------------------
# Prompt inputs (Req 7.1, 7.6).
# ---------------------------------------------------------------------------


def _format_skills(skills: Sequence[object]) -> str:
    """Format a stored skill list for its delimited prompt region.

    The stored values are included verbatim (Req 7.6 — the prompt is
    grounded in the persisted Phase 2 analysis, never a re-computation);
    joining with a comma-space separator is the only transformation. An
    empty list renders as ``(none)`` so the region is never blank.
    """
    if not skills:
        return _EMPTY_SKILLS_TEXT
    return ", ".join(str(skill) for skill in skills)


def build_inputs(match: MatchResult, feature_input: InterviewQuestionsInput) -> PromptInputs:
    """Assemble the prompt inputs for one interview-questions request.

    ``values`` fills the ``interview_questions`` template's
    ``{min_questions}`` / ``{max_questions}`` placeholders (Req 2.6) —
    both PII-free. ``sections`` carries the four delimited user-content
    regions the v1 template documents, in template order: the resume and
    Job_Description text flagged for redaction (Req 3.1), and the stored
    matched/missing skill lists passing through verbatim (Req 7.6).
    """
    settings = get_settings()
    return PromptInputs(
        values={
            "min_questions": str(_MIN_QUESTIONS),
            "max_questions": str(settings.llm_max_questions),
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
# Fallback (Req 7.5, 9.2, 9.3).
# ---------------------------------------------------------------------------

# Generic template questions used to top the fallback up to the 5-question
# floor — and as the entire set when both stored skill lists are empty
# (Req 7.5). At least ``_MIN_QUESTIONS`` entries so the floor is always
# reachable from zero skill-derived questions. Each entry respects the
# schema bounds by construction (validated at import time).
_GENERIC_FALLBACK_QUESTIONS: Final[tuple[InterviewQuestion, ...]] = (
    InterviewQuestion(
        question=(
            "Tell me about a recent project you are proud of. What was your "
            "specific contribution, and what was the outcome?"
        ),
        category=InterviewQuestionCategory.BEHAVIORAL,
        reason=(
            "A near-universal opening question interviewers use to gauge "
            "ownership and impact, whatever the role."
        ),
    ),
    InterviewQuestion(
        question=(
            "Describe a time you disagreed with a teammate or stakeholder. "
            "How did you work through it?"
        ),
        category=InterviewQuestionCategory.BEHAVIORAL,
        reason=(
            "Collaboration and conflict-resolution questions appear in almost every interview loop."
        ),
    ),
    InterviewQuestion(
        question=(
            "Tell me about a time something you delivered went wrong. What "
            "did you do, and what did you change afterwards?"
        ),
        category=InterviewQuestionCategory.BEHAVIORAL,
        reason=(
            "Interviewers routinely probe how candidates handle failure and "
            "what they learn from it."
        ),
    ),
    InterviewQuestion(
        question=(
            "How do you approach getting up to speed on a technology or "
            "domain you have not worked with before?"
        ),
        category=InterviewQuestionCategory.BEHAVIORAL,
        reason=(
            "Every role involves ramping up on unfamiliar ground, so "
            "interviewers test how deliberately candidates learn."
        ),
    ),
    InterviewQuestion(
        question=("Why are you interested in this role, and what makes you a strong fit for it?"),
        category=InterviewQuestionCategory.BEHAVIORAL,
        reason=("Motivation and fit questions are standard in nearly every interview."),
    ),
    InterviewQuestion(
        question=("What questions would you ask in your first weeks to become productive quickly?"),
        category=InterviewQuestionCategory.BEHAVIORAL,
        reason=(
            "Interviewers often close by testing how a candidate plans "
            "their own onboarding and ramp-up."
        ),
    ),
)


def _clean_skills(raw: Sequence[object]) -> list[str]:
    """Normalize a stored skill list for fallback question templating.

    Coerces each entry to text, strips surrounding whitespace, drops
    empties, and de-duplicates case-insensitively in first-occurrence
    order — so one stored duplicate never yields two identical fallback
    questions. (Prompt grounding uses the verbatim list instead; this
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
    return cleaned


def _templated_question(
    question: str, category: InterviewQuestionCategory, reason: str
) -> InterviewQuestion | None:
    """Build one templated fallback question, or ``None`` if a stored
    skill string pushes the rendered text past the schema bounds.

    Skipping (rather than truncating) keeps every produced question
    schema-conformant without ever altering content (Req 7.5, 7.7 in
    spirit: no truncation anywhere); the generic top-up guarantees the
    floor regardless of skips.
    """
    if len(question) > _QUESTION_MAX_CHARS or len(reason) > _REASON_MAX_CHARS:
        return None
    return InterviewQuestion(question=question, category=category, reason=reason)


def _gap_question(skill: str) -> InterviewQuestion | None:
    """An ``experience-gap`` question for one missing skill (Req 7.5)."""
    return _templated_question(
        question=(
            f"This role calls for {skill}, which didn't stand out in your "
            "resume. How would you get up to speed with it?"
        ),
        category=InterviewQuestionCategory.EXPERIENCE_GAP,
        reason=(
            f"The job description asks for {skill} and MatchLayer's analysis "
            "did not find it in your resume, so an interviewer is likely to "
            "probe this gap."
        ),
    )


def _depth_question(skill: str) -> InterviewQuestion | None:
    """A ``technical`` depth question for one matched skill (Req 7.5)."""
    return _templated_question(
        question=(
            f"Can you walk me through a specific piece of work where you "
            f"used {skill}? What was your individual contribution?"
        ),
        category=InterviewQuestionCategory.TECHNICAL,
        reason=(
            f"{skill} appears in both your resume and the job description, "
            "so an interviewer is likely to test the depth of your "
            "experience with it."
        ),
    )


def build_fallback(
    match: MatchResult,
    feature_input: InterviewQuestionsInput,
    reason: FailureReason,
) -> InterviewQuestionSet:
    """Build the Fallback_Response question set (Req 7.5, 9.3).

    Derived exclusively from the Match_Result's stored skill lists — no
    LLM call, no data outside the requesting user's own match:
    ``experience-gap`` questions from missing skills first (the highest
    preparation value), ``technical`` depth questions from matched
    skills next, then generic questions until the 5-question floor is
    met. The total is capped at ``llm_max_questions`` so the result
    validates against the same :class:`InterviewQuestionSet` schema as
    LLM output. The failure ``reason`` travels in the response envelope
    (set by the orchestrator), not in the content, so it is unused here;
    ``feature_input`` is part of the uniform builder signature.
    """
    del feature_input, reason  # envelope concerns; content is match-derived only
    max_questions = get_settings().llm_max_questions

    questions: list[InterviewQuestion] = []
    for skill in _clean_skills(match.missing_keywords):
        gap = _gap_question(skill)
        if gap is not None:
            questions.append(gap)
    for skill in _clean_skills(match.matched_keywords):
        depth = _depth_question(skill)
        if depth is not None:
            questions.append(depth)

    # Cap at the configured ceiling, then top up to the floor with
    # generics. Startup validation guarantees ceiling >= floor (Req 7.8),
    # so the final count always lands in [5, llm_max_questions].
    questions = questions[:max_questions]
    for generic in _GENERIC_FALLBACK_QUESTIONS:
        if len(questions) >= _MIN_QUESTIONS:
            break
        questions.append(generic)

    return InterviewQuestionSet(questions=questions)


INTERVIEW_QUESTIONS_SPEC: Final[LLMFeatureSpec[InterviewQuestionsInput, InterviewQuestionSet]] = (
    LLMFeatureSpec(
        feature=LLMFeature.INTERVIEW_QUESTIONS,
        result_schema=InterviewQuestionSet,
        build_inputs=build_inputs,
        build_fallback=build_fallback,
    )
)
"""The Interview_Question_Generator's parameterization of the shared
pipeline (design D1). No ``validate_extra`` — every Requirement 7 bound
is enforced by the schema itself — and no persisted-result reuse (that
step is the Resume_Coach's, design D7); repeat suppression comes from
the LLM_Cache (Req 15.2)."""
