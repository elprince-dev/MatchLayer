"""Unit tests for ``services/llm/bullets.py`` (phase-3-llm-layer task 8.3).

Covers the four per-feature contributions of the Bullet_Rewriter:

* :class:`BulletRewriteRequest` bounds — 1..``llm_max_bullets`` bullets,
  none empty/whitespace-only, each <= ``llm_max_bullet_chars`` chars,
  rejected by Pydantic before any LLM work (Requirement 6.3).
* :func:`build_inputs` — template placeholder values plus the three
  delimited sections: numbered bullets (redacted as ``bullet``), the
  Job_Description text (redacted), and the stored missing skills read
  verbatim (Requirements 6.1, 6.4).
* :func:`validate_alignment` — exactly one entry per submitted bullet,
  in submission order, ``original`` matching byte-for-byte; any
  deviation raises ``ValueError`` (Requirement 6.7).
* :func:`build_fallback` — each bullet unchanged, rationale built only
  from stored missing skills / suggestions, schema-conformant even when
  both stored lists are empty (Requirement 6.6).

The settings-reading request validator is pinned via ``monkeypatch`` on
the module-level ``get_settings`` binding, mirroring
``tests/unit/test_llm_schemas.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from uuid_utils.compat import uuid7

import matchlayer_api.services.llm.bullets as bullets_module
from matchlayer_api.db.models import MatchResult
from matchlayer_api.ml.prompts.registry import LLMFeature
from matchlayer_api.services.llm.bullets import (
    BULLET_REWRITE_SPEC,
    BulletRewriteInput,
    BulletRewriteRequest,
    build_fallback,
    build_inputs,
    validate_alignment,
)
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    BulletRewriteEntry,
    FailureReason,
)

_MAX_BULLETS = 5
_MAX_BULLET_CHARS = 500


@pytest.fixture(autouse=True)
def _pin_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the request bounds to known values for every test."""
    monkeypatch.setattr(
        bullets_module,
        "get_settings",
        lambda: SimpleNamespace(
            llm_max_bullets=_MAX_BULLETS,
            llm_max_bullet_chars=_MAX_BULLET_CHARS,
        ),
    )


def _match(
    *,
    job_description: str = "Backend engineer role requiring Python and AWS.",
    missing: list[str] | None = None,
    suggestions: list[str] | None = None,
) -> MatchResult:
    return MatchResult(
        id=uuid7(),
        user_id=uuid7(),
        job_description_text=job_description,
        missing_keywords=missing if missing is not None else ["terraform", "kubernetes"],
        suggestions=suggestions if suggestions is not None else ["Add a metrics-driven summary."],
    )


def _rewrite(originals: list[str]) -> BulletRewrite:
    return BulletRewrite(
        entries=[
            BulletRewriteEntry(
                original=original,
                alternatives=[f"Improved: {original}"],
                rationale="Targets the job's missing skills.",
            )
            for original in originals
        ]
    )


# ---------------------------------------------------------------------------
# BulletRewriteRequest (Requirement 6.3).
# ---------------------------------------------------------------------------


class TestBulletRewriteRequest:
    def test_accepts_one_bullet(self) -> None:
        request = BulletRewriteRequest(bullets=["Shipped the payments service."])
        assert request.bullets == ["Shipped the payments service."]

    def test_accepts_max_bullets(self) -> None:
        bullets = [f"Bullet number {n}." for n in range(_MAX_BULLETS)]
        assert BulletRewriteRequest(bullets=bullets).bullets == bullets

    def test_accepts_bullet_at_exact_char_ceiling(self) -> None:
        bullet = "x" * _MAX_BULLET_CHARS
        assert BulletRewriteRequest(bullets=[bullet]).bullets == [bullet]

    def test_preserves_bullet_text_exactly(self) -> None:
        """No stripping/normalization — alignment compares byte-for-byte."""
        bullet = "  led a team of 4  "
        assert BulletRewriteRequest(bullets=[bullet]).bullets == [bullet]

    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteRequest(bullets=[])

    def test_rejects_too_many_bullets(self) -> None:
        bullets = [f"Bullet number {n}." for n in range(_MAX_BULLETS + 1)]
        with pytest.raises(ValidationError, match="at most"):
            BulletRewriteRequest(bullets=bullets)

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_rejects_empty_or_whitespace_only_bullet(self, bad: str) -> None:
        with pytest.raises(ValidationError, match="empty or whitespace-only"):
            BulletRewriteRequest(bullets=["A fine bullet.", bad])

    def test_rejects_bullet_over_char_ceiling(self) -> None:
        with pytest.raises(ValidationError, match="exceeds"):
            BulletRewriteRequest(bullets=["x" * (_MAX_BULLET_CHARS + 1)])

    def test_error_message_never_contains_bullet_content(self) -> None:
        """Bullet text is Restricted PII — bounds errors carry positions only."""
        secret = "y" * (_MAX_BULLET_CHARS + 1)
        with pytest.raises(ValidationError) as exc_info:
            BulletRewriteRequest(bullets=[secret])
        assert secret not in str(exc_info.value)

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            BulletRewriteRequest.model_validate({"bullets": ["ok"], "extra": True})


# ---------------------------------------------------------------------------
# build_inputs (Requirements 6.1, 6.4).
# ---------------------------------------------------------------------------


class TestBuildInputs:
    def test_values_fill_template_placeholders(self) -> None:
        inputs = build_inputs(_match(), BulletRewriteInput(bullets=("one", "two")))
        assert inputs.values == {
            "bullet_count": "2",
            "min_alternatives": "1",
            "max_alternatives": "3",
        }

    def test_sections_in_template_order_with_redaction_kinds(self) -> None:
        match = _match(job_description="JD text here.")
        inputs = build_inputs(match, BulletRewriteInput(bullets=("one",)))
        kinds = [(s.kind, s.redaction) for s in inputs.sections]
        assert kinds == [
            ("bullets", "bullet"),
            ("job_description", "job_description"),
            ("missing_skills", None),
        ]
        assert inputs.sections[1].text == "JD text here."

    def test_bullets_numbered_in_submission_order(self) -> None:
        inputs = build_inputs(
            _match(), BulletRewriteInput(bullets=("first bullet", "second bullet"))
        )
        assert inputs.sections[0].text == "1. first bullet\n2. second bullet"

    def test_missing_skills_verbatim(self) -> None:
        match = _match(missing=["Terraform ", "k8s"])
        inputs = build_inputs(match, BulletRewriteInput(bullets=("b",)))
        assert inputs.sections[2].text == "Terraform , k8s"

    def test_empty_missing_skills_render_placeholder(self) -> None:
        match = _match(missing=[])
        inputs = build_inputs(match, BulletRewriteInput(bullets=("b",)))
        assert inputs.sections[2].text == "(none)"


# ---------------------------------------------------------------------------
# validate_alignment (Requirement 6.7).
# ---------------------------------------------------------------------------


class TestValidateAlignment:
    def test_accepts_exact_alignment(self) -> None:
        bullets = ("alpha", "beta")
        validate_alignment(
            _match(), BulletRewriteInput(bullets=bullets), _rewrite(["alpha", "beta"])
        )

    def test_rejects_missing_entry(self) -> None:
        with pytest.raises(ValueError, match="exactly one entry"):
            validate_alignment(
                _match(), BulletRewriteInput(bullets=("alpha", "beta")), _rewrite(["alpha"])
            )

    def test_rejects_extra_entry(self) -> None:
        with pytest.raises(ValueError, match="exactly one entry"):
            validate_alignment(
                _match(),
                BulletRewriteInput(bullets=("alpha",)),
                _rewrite(["alpha", "beta"]),
            )

    def test_rejects_reordered_entries(self) -> None:
        with pytest.raises(ValueError, match="does not match"):
            validate_alignment(
                _match(),
                BulletRewriteInput(bullets=("alpha", "beta")),
                _rewrite(["beta", "alpha"]),
            )

    def test_rejects_inexact_original(self) -> None:
        """Byte-for-byte comparison — even whitespace drift is a deviation."""
        with pytest.raises(ValueError, match="does not match"):
            validate_alignment(
                _match(), BulletRewriteInput(bullets=("alpha ",)), _rewrite(["alpha"])
            )

    def test_error_never_contains_bullet_content(self) -> None:
        secret_bullet = "worked at ACME on secret project"
        with pytest.raises(ValueError) as exc_info:
            validate_alignment(
                _match(), BulletRewriteInput(bullets=(secret_bullet,)), _rewrite(["other"])
            )
        assert secret_bullet not in str(exc_info.value)


# ---------------------------------------------------------------------------
# build_fallback (Requirement 6.6).
# ---------------------------------------------------------------------------


class TestBuildFallback:
    def test_one_entry_per_bullet_original_unchanged(self) -> None:
        bullets = ("first", "second", "third")
        fallback = build_fallback(
            _match(), BulletRewriteInput(bullets=bullets), FailureReason.PROVIDER_ERROR
        )
        assert [entry.original for entry in fallback.entries] == list(bullets)
        for entry, bullet in zip(fallback.entries, bullets, strict=True):
            assert entry.alternatives == [bullet]

    def test_rationale_derived_from_stored_missing_skills_and_suggestions(self) -> None:
        fallback = build_fallback(
            _match(missing=["terraform"], suggestions=["Quantify your impact."]),
            BulletRewriteInput(bullets=("b",)),
            FailureReason.TIMEOUT,
        )
        rationale = fallback.entries[0].rationale
        assert "terraform" in rationale
        assert "Quantify your impact." in rationale

    def test_empty_stored_lists_still_yield_valid_rationale(self) -> None:
        fallback = build_fallback(
            _match(missing=[], suggestions=[]),
            BulletRewriteInput(bullets=("b",)),
            FailureReason.SCHEMA_VALIDATION_FAILED,
        )
        assert fallback.entries[0].rationale
        # Conforms to the same schema as LLM output (Req 9.2).
        BulletRewrite.model_validate(fallback.model_dump())

    def test_fallback_passes_alignment_check(self) -> None:
        bullets = ("alpha", "beta")
        feature_input = BulletRewriteInput(bullets=bullets)
        fallback = build_fallback(_match(), feature_input, FailureReason.PROVIDER_ERROR)
        validate_alignment(_match(), feature_input, fallback)

    def test_duplicate_stored_skills_not_repeated(self) -> None:
        fallback = build_fallback(
            _match(missing=["Docker", "docker "], suggestions=[]),
            BulletRewriteInput(bullets=("b",)),
            FailureReason.PROVIDER_ERROR,
        )
        assert fallback.entries[0].rationale.count("Docker") == 1


# ---------------------------------------------------------------------------
# Spec wiring.
# ---------------------------------------------------------------------------


class TestSpec:
    def test_spec_wiring(self) -> None:
        assert BULLET_REWRITE_SPEC.feature is LLMFeature.BULLET_REWRITE
        assert BULLET_REWRITE_SPEC.result_schema is BulletRewrite
        assert BULLET_REWRITE_SPEC.build_inputs is build_inputs
        assert BULLET_REWRITE_SPEC.build_fallback is build_fallback
        assert BULLET_REWRITE_SPEC.validate_extra is validate_alignment
        assert BULLET_REWRITE_SPEC.reuse_persisted is False
