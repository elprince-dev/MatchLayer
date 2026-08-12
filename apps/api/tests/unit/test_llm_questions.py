"""Unit tests for ``services/llm/questions.py`` (phase-3-llm-layer task 8.4).

Covers the two feature-specific contributions the Interview_Question_Generator
makes to the shared pipeline (design D1):

* ``build_inputs`` — the four delimited prompt regions in template order,
  with the resume/JD text flagged for redaction and the stored skill lists
  passed through verbatim (Requirements 7.1, 7.6).
* ``build_fallback`` — template-based questions from missing (experience-gap)
  and matched (technical) skills, generic top-up to the 5-question floor,
  ceiling cap, and schema conformance (Requirements 7.5, 7.7 — no
  truncation: oversized templated questions are skipped, never clipped).
* ``INTERVIEW_QUESTIONS_SPEC`` wiring — feature id, result schema, no
  ``validate_extra``, no persisted-result reuse (that step is the coach's).

Cross-cutting behavior (validation gate, fallback envelope, grounding across
generated inputs) belongs to the orchestrator tests and property tests
8.5-8.8.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from uuid_utils.compat import uuid7

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm import questions as questions_module
from matchlayer_api.services.llm import schemas as schemas_module
from matchlayer_api.services.llm.questions import (
    INTERVIEW_QUESTIONS_SPEC,
    InterviewQuestionsInput,
    build_fallback,
    build_inputs,
)
from matchlayer_api.services.llm.schemas import (
    FailureReason,
    InterviewQuestionCategory,
    InterviewQuestionSet,
)

_CEILING = 8


@pytest.fixture(autouse=True)
def _pin_max_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``llm_max_questions`` to a known ceiling for every test.

    Patched in both modules that read it: ``questions.py`` (prompt values,
    fallback cap) and ``schemas.py`` (the InterviewQuestionSet ceiling
    validator) — the same pattern as ``tests/unit/test_llm_schemas.py``.
    """
    fake_settings = SimpleNamespace(llm_max_questions=_CEILING)
    monkeypatch.setattr(questions_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(schemas_module, "get_settings", lambda: fake_settings)


def _match(
    *,
    matched: list[Any] | None = None,
    missing: list[Any] | None = None,
    jd_text: str = "We need a senior backend engineer.",
) -> MatchResult:
    return MatchResult(
        id=uuid7(),
        user_id=uuid7(),
        job_description_text=jd_text,
        matched_keywords=matched if matched is not None else [],
        missing_keywords=missing if missing is not None else [],
    )


_INPUT = InterviewQuestionsInput(resume_text="Jane Doe. Built APIs with Python.")


# ---------------------------------------------------------------------------
# build_inputs (Requirements 7.1, 7.6).
# ---------------------------------------------------------------------------


class TestBuildInputs:
    def test_sections_follow_template_order_with_redaction_kinds(self) -> None:
        inputs = build_inputs(_match(matched=["python"], missing=["kubernetes"]), _INPUT)

        kinds = [section.kind for section in inputs.sections]
        assert kinds == ["resume", "job_description", "matched_skills", "missing_skills"]
        redactions = [section.redaction for section in inputs.sections]
        assert redactions == ["resume", "job_description", None, None]

    def test_resume_and_jd_text_pass_through_raw(self) -> None:
        match = _match(jd_text="JD body")
        inputs = build_inputs(match, _INPUT)

        assert inputs.sections[0].text == _INPUT.resume_text
        assert inputs.sections[1].text == "JD body"

    def test_skills_rendered_verbatim_comma_joined(self) -> None:
        match = _match(matched=["Python", "aws"], missing=["Kubernetes", "Go"])
        inputs = build_inputs(match, _INPUT)

        assert inputs.sections[2].text == "Python, aws"
        assert inputs.sections[3].text == "Kubernetes, Go"

    def test_empty_skill_lists_render_placeholder(self) -> None:
        inputs = build_inputs(_match(), _INPUT)

        assert inputs.sections[2].text == "(none)"
        assert inputs.sections[3].text == "(none)"

    def test_values_carry_question_count_bounds(self) -> None:
        inputs = build_inputs(_match(), _INPUT)

        assert inputs.values == {"min_questions": "5", "max_questions": str(_CEILING)}


# ---------------------------------------------------------------------------
# build_fallback (Requirements 7.5, 7.7, 9.2, 9.3).
# ---------------------------------------------------------------------------


def _fallback(match: MatchResult) -> InterviewQuestionSet:
    return build_fallback(match, _INPUT, FailureReason.PROVIDER_ERROR)


class TestBuildFallback:
    def test_empty_skill_lists_yield_five_generic_questions(self) -> None:
        result = _fallback(_match())

        assert isinstance(result, InterviewQuestionSet)
        assert len(result.questions) == 5
        assert all(q.category == InterviewQuestionCategory.BEHAVIORAL for q in result.questions)

    def test_missing_skills_yield_gap_questions_first(self) -> None:
        result = _fallback(_match(matched=["python"], missing=["kubernetes"]))

        first = result.questions[0]
        assert first.category == InterviewQuestionCategory.EXPERIENCE_GAP
        assert "kubernetes" in first.question
        second = result.questions[1]
        assert second.category == InterviewQuestionCategory.TECHNICAL
        assert "python" in second.question

    def test_generic_top_up_meets_the_floor(self) -> None:
        result = _fallback(_match(missing=["kubernetes"]))

        assert len(result.questions) == 5
        categories = [q.category for q in result.questions]
        assert categories[0] == InterviewQuestionCategory.EXPERIENCE_GAP
        assert categories[1:] == [InterviewQuestionCategory.BEHAVIORAL] * 4

    def test_count_capped_at_configured_ceiling(self) -> None:
        many = [f"skill-{i}" for i in range(_CEILING + 5)]
        result = _fallback(_match(missing=many))

        assert len(result.questions) == _CEILING

    def test_duplicate_and_blank_skills_are_dropped(self) -> None:
        result = _fallback(_match(missing=["Docker", "docker", "  ", "DOCKER"]))

        gap_questions = [
            q for q in result.questions if q.category == InterviewQuestionCategory.EXPERIENCE_GAP
        ]
        assert len(gap_questions) == 1
        assert "Docker" in gap_questions[0].question

    def test_oversized_skill_is_skipped_not_truncated(self) -> None:
        huge_skill = "x" * 400  # pushes the templated question past 300 chars
        result = _fallback(_match(missing=[huge_skill]))

        assert len(result.questions) == 5
        assert all(huge_skill not in q.question for q in result.questions)
        assert all(len(q.question) <= 300 for q in result.questions)

    def test_fallback_validates_against_the_result_schema(self) -> None:
        result = _fallback(_match(matched=["python", "sql"], missing=["go"]))

        revalidated = InterviewQuestionSet.model_validate(result.model_dump())
        assert 5 <= len(revalidated.questions) <= _CEILING


# ---------------------------------------------------------------------------
# Spec wiring (design D1).
# ---------------------------------------------------------------------------


class TestSpecWiring:
    def test_spec_targets_the_interview_questions_feature(self) -> None:
        assert INTERVIEW_QUESTIONS_SPEC.feature is LLMFeature.INTERVIEW_QUESTIONS
        assert INTERVIEW_QUESTIONS_SPEC.result_schema is InterviewQuestionSet

    def test_spec_has_no_extra_validation_and_no_persisted_reuse(self) -> None:
        assert INTERVIEW_QUESTIONS_SPEC.validate_extra is None
        assert INTERVIEW_QUESTIONS_SPEC.reuse_persisted is False
