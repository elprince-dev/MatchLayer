"""Unit tests for the Phase 3 LLM result schemas (task 2.2).

Exercises the field bounds and validators of
:mod:`matchlayer_api.services.llm.schemas`:

* ``CoachingReport`` — 3..10 improvements with strictly increasing
  priority ranks (Requirement 5.2).
* ``BulletRewriteEntry`` / ``BulletRewrite`` — 1..3 alternatives,
  non-empty rationale, ``original`` preserved verbatim (Requirement 6.2).
* ``InterviewQuestion`` / ``InterviewQuestionSet`` — category enum,
  question <= 300 chars, reason <= 500 chars, count in
  5..``MATCHLAYER_LLM_MAX_QUESTIONS`` (Requirements 7.2, 7.3).
* ``LLMResultEnvelope`` — ``is_fallback`` / ``fallback_reason`` /
  ``prompt_template_version`` / ``created_at`` nullability contract
  (fallbacks carry ``None`` for the persistence fields; persisted LLM
  results carry all of them).

The InterviewQuestionSet ceiling validator reads
``get_settings().llm_max_questions`` through the module-level binding in
``schemas.py``, so ceiling tests replace that binding via ``monkeypatch``
(the same pattern as ``tests/unit/test_semantic_adapter.py``) instead of
touching the cached process-wide settings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from matchlayer_api.services.llm import schemas as schemas_module
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    BulletRewriteEntry,
    CoachingReport,
    FailureReason,
    ImprovementAction,
    InterviewQuestion,
    InterviewQuestionCategory,
    InterviewQuestionSet,
    LLMResultEnvelope,
)

# ---------------------------------------------------------------------------
# Builders — minimal valid payloads each test mutates.
# ---------------------------------------------------------------------------


def _improvements(priorities: list[int]) -> list[dict[str, object]]:
    return [{"priority": p, "action": f"Do thing {i}"} for i, p in enumerate(priorities)]


def _coaching_report(priorities: list[int] | None = None) -> dict[str, object]:
    return {
        "summary": "Solid overlap with the role.",
        "strengths": ["Python", "FastAPI"],
        "gaps": ["Kubernetes"],
        "improvements": _improvements(priorities if priorities is not None else [1, 2, 3]),
    }


def _bullet_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "original": "Built a data pipeline",
        "alternatives": ["Engineered a fault-tolerant data pipeline"],
        "rationale": "Uses stronger action verbs matching the JD.",
    }
    entry.update(overrides)
    return entry


def _question(**overrides: object) -> dict[str, object]:
    q: dict[str, object] = {
        "question": "How did you scale the pipeline?",
        "category": "technical",
        "reason": "The JD emphasizes distributed systems experience.",
    }
    q.update(overrides)
    return q


def _question_set(count: int) -> dict[str, object]:
    return {"questions": [_question(question=f"Question number {i}?") for i in range(count)]}


@pytest.fixture
def max_questions(monkeypatch: pytest.MonkeyPatch) -> int:
    """Pin the InterviewQuestionSet ceiling to a known value (8) for the test."""
    ceiling = 8
    monkeypatch.setattr(
        schemas_module,
        "get_settings",
        lambda: SimpleNamespace(llm_max_questions=ceiling),
    )
    return ceiling


# ---------------------------------------------------------------------------
# CoachingReport — improvement count and priority ordering (Requirement 5.2).
# ---------------------------------------------------------------------------


class TestCoachingReport:
    def test_accepts_three_improvements_with_increasing_ranks(self) -> None:
        report = CoachingReport.model_validate(_coaching_report([1, 2, 3]))
        assert [a.priority for a in report.improvements] == [1, 2, 3]

    def test_accepts_ten_improvements(self) -> None:
        report = CoachingReport.model_validate(_coaching_report(list(range(1, 11))))
        assert len(report.improvements) == 10

    def test_accepts_non_contiguous_increasing_ranks(self) -> None:
        report = CoachingReport.model_validate(_coaching_report([1, 3, 7]))
        assert [a.priority for a in report.improvements] == [1, 3, 7]

    def test_rejects_fewer_than_three_improvements(self) -> None:
        with pytest.raises(ValidationError):
            CoachingReport.model_validate(_coaching_report([1, 2]))

    def test_rejects_more_than_ten_improvements(self) -> None:
        with pytest.raises(ValidationError):
            CoachingReport.model_validate(_coaching_report(list(range(1, 12))))

    def test_rejects_decreasing_priority_ranks(self) -> None:
        with pytest.raises(ValidationError, match="highest to lowest"):
            CoachingReport.model_validate(_coaching_report([3, 2, 1]))

    def test_rejects_duplicate_priority_ranks(self) -> None:
        with pytest.raises(ValidationError, match="strictly increasing"):
            CoachingReport.model_validate(_coaching_report([1, 2, 2]))

    def test_rejects_priority_rank_below_one(self) -> None:
        with pytest.raises(ValidationError):
            CoachingReport.model_validate(_coaching_report([0, 1, 2]))

    def test_rejects_whitespace_only_summary(self) -> None:
        payload = _coaching_report()
        payload["summary"] = "   "
        with pytest.raises(ValidationError):
            CoachingReport.model_validate(payload)

    def test_rejects_whitespace_only_action(self) -> None:
        with pytest.raises(ValidationError):
            ImprovementAction.model_validate({"priority": 1, "action": " \t "})

    def test_rejects_extra_keys(self) -> None:
        payload = _coaching_report()
        payload["score"] = 95
        with pytest.raises(ValidationError):
            CoachingReport.model_validate(payload)


# ---------------------------------------------------------------------------
# BulletRewrite — alternatives bounds and rationale (Requirement 6.2).
# ---------------------------------------------------------------------------


class TestBulletRewrite:
    def test_accepts_one_alternative(self) -> None:
        entry = BulletRewriteEntry.model_validate(_bullet_entry())
        assert len(entry.alternatives) == 1

    def test_accepts_three_alternatives(self) -> None:
        entry = BulletRewriteEntry.model_validate(
            _bullet_entry(alternatives=["Alt one", "Alt two", "Alt three"])
        )
        assert len(entry.alternatives) == 3

    def test_rejects_zero_alternatives(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteEntry.model_validate(_bullet_entry(alternatives=[]))

    def test_rejects_four_alternatives(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteEntry.model_validate(_bullet_entry(alternatives=["a1", "a2", "a3", "a4"]))

    def test_rejects_whitespace_only_alternative(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteEntry.model_validate(_bullet_entry(alternatives=["  "]))

    def test_rejects_empty_rationale(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteEntry.model_validate(_bullet_entry(rationale=""))

    def test_rejects_whitespace_only_rationale(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteEntry.model_validate(_bullet_entry(rationale="  \n "))

    def test_original_is_preserved_verbatim(self) -> None:
        """``original`` must byte-for-byte match the submission (Requirement 6.7),
        so no whitespace stripping is applied to it."""
        entry = BulletRewriteEntry.model_validate(_bullet_entry(original="  padded bullet  "))
        assert entry.original == "  padded bullet  "

    def test_rewrite_rejects_empty_entries(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewrite.model_validate({"entries": []})

    def test_rewrite_accepts_one_entry(self) -> None:
        rewrite = BulletRewrite.model_validate({"entries": [_bullet_entry()]})
        assert len(rewrite.entries) == 1

    def test_rejects_extra_keys(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteEntry.model_validate(_bullet_entry(confidence=0.9))


# ---------------------------------------------------------------------------
# InterviewQuestion / InterviewQuestionSet (Requirements 7.2, 7.3).
# ---------------------------------------------------------------------------


class TestInterviewQuestion:
    def test_accepts_each_category(self) -> None:
        for category in ("technical", "behavioral", "experience-gap"):
            question = InterviewQuestion.model_validate(_question(category=category))
            assert question.category == InterviewQuestionCategory(category)

    def test_rejects_unknown_category(self) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestion.model_validate(_question(category="cultural"))

    def test_accepts_question_at_300_chars(self) -> None:
        question = InterviewQuestion.model_validate(_question(question="q" * 300))
        assert len(question.question) == 300

    def test_rejects_question_over_300_chars(self) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestion.model_validate(_question(question="q" * 301))

    def test_accepts_reason_at_500_chars(self) -> None:
        question = InterviewQuestion.model_validate(_question(reason="r" * 500))
        assert len(question.reason) == 500

    def test_rejects_reason_over_500_chars(self) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestion.model_validate(_question(reason="r" * 501))

    def test_rejects_whitespace_only_question(self) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestion.model_validate(_question(question="   "))

    def test_rejects_whitespace_only_reason(self) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestion.model_validate(_question(reason="   "))

    def test_strips_surrounding_whitespace(self) -> None:
        question = InterviewQuestion.model_validate(_question(question="  trimmed?  "))
        assert question.question == "trimmed?"

    def test_rejects_extra_keys(self) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestion.model_validate(_question(difficulty="hard"))


class TestInterviewQuestionSet:
    def test_accepts_five_questions(self, max_questions: int) -> None:
        question_set = InterviewQuestionSet.model_validate(_question_set(5))
        assert len(question_set.questions) == 5

    def test_rejects_four_questions(self, max_questions: int) -> None:
        with pytest.raises(ValidationError):
            InterviewQuestionSet.model_validate(_question_set(4))

    def test_accepts_count_at_configured_ceiling(self, max_questions: int) -> None:
        question_set = InterviewQuestionSet.model_validate(_question_set(max_questions))
        assert len(question_set.questions) == max_questions

    def test_rejects_count_above_configured_ceiling(self, max_questions: int) -> None:
        with pytest.raises(ValidationError, match="MATCHLAYER_LLM_MAX_QUESTIONS"):
            InterviewQuestionSet.model_validate(_question_set(max_questions + 1))

    def test_rejects_extra_keys(self, max_questions: int) -> None:
        payload = _question_set(5)
        payload["topic"] = "backend"
        with pytest.raises(ValidationError):
            InterviewQuestionSet.model_validate(payload)


# ---------------------------------------------------------------------------
# LLMResultEnvelope — fallback marker and nullability contract
# (Requirements 9.2, 17.9 context; tested here per task 2.2 envelope fields).
# ---------------------------------------------------------------------------


class TestLLMResultEnvelope:
    def _report(self) -> CoachingReport:
        return CoachingReport.model_validate(_coaching_report())

    def test_fallback_envelope_defaults_persistence_fields_to_none(self) -> None:
        envelope = LLMResultEnvelope[CoachingReport](
            is_fallback=True,
            fallback_reason=FailureReason.TIMEOUT,
            result=self._report(),
        )
        assert envelope.is_fallback is True
        assert envelope.fallback_reason is FailureReason.TIMEOUT
        assert envelope.id is None
        assert envelope.prompt_template_version is None
        assert envelope.created_at is None

    def test_persisted_result_carries_all_envelope_fields(self) -> None:
        created = datetime(2026, 1, 15, 12, 30, tzinfo=UTC)
        envelope = LLMResultEnvelope[CoachingReport](
            id="0194e4a0-0000-7000-8000-000000000000",
            is_fallback=False,
            prompt_template_version=1,
            created_at=created,
            result=self._report(),
        )
        assert envelope.is_fallback is False
        assert envelope.fallback_reason is None
        assert envelope.id == "0194e4a0-0000-7000-8000-000000000000"
        assert envelope.prompt_template_version == 1
        assert envelope.created_at == created

    def test_is_fallback_is_required(self) -> None:
        with pytest.raises(ValidationError):
            LLMResultEnvelope[CoachingReport].model_validate({"result": _coaching_report()})

    def test_result_is_required(self) -> None:
        with pytest.raises(ValidationError):
            LLMResultEnvelope[CoachingReport].model_validate({"is_fallback": False})

    def test_fallback_reason_accepts_every_failure_category(self) -> None:
        for reason in FailureReason:
            envelope = LLMResultEnvelope[CoachingReport](
                is_fallback=True,
                fallback_reason=reason,
                result=self._report(),
            )
            assert envelope.fallback_reason is reason

    def test_rejects_unknown_fallback_reason(self) -> None:
        with pytest.raises(ValidationError):
            LLMResultEnvelope[CoachingReport].model_validate(
                {
                    "is_fallback": True,
                    "fallback_reason": "sunspots",
                    "result": _coaching_report(),
                }
            )

    def test_rejects_extra_keys(self) -> None:
        with pytest.raises(ValidationError):
            LLMResultEnvelope[CoachingReport].model_validate(
                {
                    "is_fallback": False,
                    "result": _coaching_report(),
                    "cached": True,
                }
            )

    def test_failure_reason_is_the_closed_documented_set(self) -> None:
        assert {r.value for r in FailureReason} == {
            "provider_error",
            "timeout",
            "schema_validation_failed",
            "redaction_failed",
            "prompt_template_missing",
            "quota_accounting_unavailable",
            "llm_unavailable",
        }
