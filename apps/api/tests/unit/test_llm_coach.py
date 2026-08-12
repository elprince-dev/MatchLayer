"""Unit tests for ``services/llm/coach.py`` (phase-3-llm-layer task 8.2).

Covers the two feature-specific contributions the Resume_Coach makes to
the shared pipeline (design D1):

* ``build_inputs`` — the four delimited prompt regions in template order,
  with the resume/JD text flagged for redaction and the stored skill lists
  passed through verbatim (Requirements 5.1, 5.3).
* ``build_fallback`` — content derived only from the stored rule-based
  suggestions and missing skills: gaps mirror the missing skills (an empty
  stored list carried as an empty gaps list), improvements carry the
  stored suggestions with generic top-up to the schema's 3-action floor
  and a cap at its 10-action ceiling, and the result validates against the
  same ``CoachingReport`` schema as LLM output (Requirements 5.5, 9.2,
  9.3).
* ``RESUME_COACH_SPEC`` wiring — feature id, result schema, no
  ``validate_extra``, and ``reuse_persisted=True`` (design D7 — the
  persisted-result reuse behavior itself, Requirements 5.4/5.7, is
  exercised in the orchestrator tests).

Cross-cutting behavior (validation gate, fallback envelope, grounding
across generated inputs) belongs to the orchestrator tests and property
tests 8.5-8.9.
"""

from __future__ import annotations

from typing import Any

from uuid_utils.compat import uuid7

from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.coach import (
    RESUME_COACH_SPEC,
    ResumeCoachInput,
    build_fallback,
    build_inputs,
)
from matchlayer_api.services.llm.schemas import CoachingReport, FailureReason


def _match(
    *,
    matched: list[Any] | None = None,
    missing: list[Any] | None = None,
    suggestions: list[Any] | None = None,
    jd_text: str = "We need a senior backend engineer.",
) -> MatchResult:
    return MatchResult(
        id=uuid7(),
        user_id=uuid7(),
        job_description_text=jd_text,
        matched_keywords=matched if matched is not None else [],
        missing_keywords=missing if missing is not None else [],
        suggestions=suggestions if suggestions is not None else [],
    )


_INPUT = ResumeCoachInput(resume_text="Jane Doe. Built APIs with Python.")


# ---------------------------------------------------------------------------
# build_inputs (Requirements 5.1, 5.3).
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

    def test_values_carry_improvement_count_bounds(self) -> None:
        inputs = build_inputs(_match(), _INPUT)

        assert inputs.values == {"min_improvements": "3", "max_improvements": "10"}


# ---------------------------------------------------------------------------
# build_fallback (Requirements 5.5, 9.2, 9.3).
# ---------------------------------------------------------------------------


def _fallback(match: MatchResult) -> CoachingReport:
    return build_fallback(match, _INPUT, FailureReason.PROVIDER_ERROR)


class TestBuildFallback:
    def test_empty_stored_lists_carry_empty_gaps_and_generic_floor(self) -> None:
        result = _fallback(_match())

        assert isinstance(result, CoachingReport)
        assert result.gaps == []  # empty stored list carried as empty (Req 5.5)
        assert result.strengths == []
        assert len(result.improvements) == 3  # generic top-up to the schema floor

    def test_gaps_carry_each_missing_skill(self) -> None:
        result = _fallback(_match(missing=["kubernetes", "go"]))

        assert len(result.gaps) == 2
        assert "kubernetes" in result.gaps[0]
        assert "go" in result.gaps[1]

    def test_suggestions_carried_verbatim_in_stored_order(self) -> None:
        stored = [
            "Add cloud experience to your skills section.",
            "Quantify the impact of your API work.",
            "Mention CI/CD tooling you have used.",
        ]
        result = _fallback(_match(suggestions=stored))

        assert [a.action for a in result.improvements] == stored
        assert [a.priority for a in result.improvements] == [1, 2, 3]

    def test_fewer_than_three_suggestions_topped_up_with_generics(self) -> None:
        stored = ["Add cloud experience to your skills section."]
        result = _fallback(_match(suggestions=stored))

        assert len(result.improvements) == 3
        assert result.improvements[0].action == stored[0]

    def test_suggestions_capped_at_ten(self) -> None:
        stored = [f"Suggestion number {i}." for i in range(15)]
        result = _fallback(_match(suggestions=stored))

        assert len(result.improvements) == 10
        assert [a.action for a in result.improvements] == stored[:10]

    def test_duplicate_and_blank_entries_are_dropped(self) -> None:
        result = _fallback(
            _match(
                missing=["Docker", "docker", "  "],
                suggestions=["Do X.", "do x.", "", "Do Y.", "Do Z."],
            )
        )

        assert len(result.gaps) == 1
        assert "Docker" in result.gaps[0]
        assert [a.action for a in result.improvements] == ["Do X.", "Do Y.", "Do Z."]

    def test_fallback_validates_against_the_result_schema(self) -> None:
        result = _fallback(_match(missing=["go"], suggestions=["Do X.", "Do Y."]))

        revalidated = CoachingReport.model_validate(result.model_dump())
        assert 3 <= len(revalidated.improvements) <= 10


# ---------------------------------------------------------------------------
# Spec wiring (design D1, D7).
# ---------------------------------------------------------------------------


class TestSpecWiring:
    def test_spec_targets_the_resume_coach_feature(self) -> None:
        assert RESUME_COACH_SPEC.feature is LLMFeature.RESUME_COACH
        assert RESUME_COACH_SPEC.result_schema is CoachingReport

    def test_spec_opts_into_persisted_result_reuse(self) -> None:
        assert RESUME_COACH_SPEC.reuse_persisted is True  # design D7, Req 5.4
        assert RESUME_COACH_SPEC.validate_extra is None
