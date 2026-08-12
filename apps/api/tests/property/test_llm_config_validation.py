"""Feature: phase-3-llm-layer — Property 21.

# Feature: phase-3-llm-layer, Property 21: Config validation rejects non-positive bounds at startup

Property 21: Config validation rejects non-positive bounds at startup.

    *For any* single setting from the Phase 3 numeric set
    (``llm_timeout_seconds``, ``llm_max_output_tokens``, ``llm_daily_quota``,
    ``llm_monthly_spend_limit_usd``, ``llm_max_bullets``,
    ``llm_max_bullet_chars``, ``llm_max_questions``,
    ``llm_cache_ttl_seconds``) assigned a non-positive value — or
    ``llm_max_questions`` assigned a value below 5 — constructing
    ``Settings`` fails with an error message naming that setting; and any
    assignment of positive (and >=5 for questions) values constructs
    successfully.

**Validates: Requirements 7.8, 18.2**

The ``Settings._llm_numeric_settings_positive`` model validator (task 1.1)
enforces two constraints:

1. Every numeric LLM setting is strictly positive.
2. ``llm_max_questions`` is at least 5 — the Interview_Question_Set schema
   floor — so a configured upper bound can never sit below the schema's
   lower bound.

Three properties pin the contract across generated inputs:

* **Non-positive values are rejected** — any one setting from the numeric
  set assigned a value <= 0 raises :class:`pydantic.ValidationError` whose
  message names the offending ``MATCHLAYER_LLM_*`` env var (Requirement
  18.2's "identify the misconfigured setting by name").
* **Sub-floor question bounds are rejected** — ``llm_max_questions`` in
  ``1..4`` (positive, so only the floor check can trip) raises
  :class:`pydantic.ValidationError` naming ``MATCHLAYER_LLM_MAX_QUESTIONS``
  (Requirement 7.8).
* **Valid values construct** — a positive value for any setting (>= 5 when
  the setting is ``llm_max_questions``) builds a ``Settings`` instance that
  stores the value verbatim.

``Settings`` is constructed with explicit kwargs for every required field
(mirroring ``tests/property/test_weight_validation.py``) so the cases are
hermetic and never depend on the repo ``.env``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from matchlayer_api.config import Settings

# ---------------------------------------------------------------------------
# Hermetic Settings kwargs (mirrors tests/property/test_weight_validation.py)
# ---------------------------------------------------------------------------

# 33 bytes UTF-8 — clears the 32-byte floor in
# ``Settings._jwt_secret_min_length``. Same synthetic constant the auth unit
# tests use so the value is recognizably a test fixture.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

# Every field ``Settings`` requires, with placeholder values that pass
# Pydantic validation without touching the repo's ``.env``. The
# ``llm_*`` numeric fields keep their defaults; each property overrides
# exactly the one setting under test.
_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "environment": "development",
    "log_level": "info",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "redis_url": "redis://localhost:6379/0",
    "s3_endpoint_url": None,
    "s3_region": "us-east-1",
    "s3_access_key_id": "test",
    "s3_secret_access_key": "test",
    "s3_bucket": "test-bucket",
    "cors_allowed_origins": [],
    "jwt_secret": _TEST_SECRET,
}

# ---------------------------------------------------------------------------
# The Phase 3 numeric setting set under test (design Property 21)
# ---------------------------------------------------------------------------

# (env var name asserted in the error message, Settings field name,
#  whether the field is Decimal-typed). ``llm_monthly_spend_limit_usd`` is
# the one Decimal in the set; every other field is an int.
_NUMERIC_SETTINGS: tuple[tuple[str, str, bool], ...] = (
    ("MATCHLAYER_LLM_TIMEOUT_SECONDS", "llm_timeout_seconds", False),
    ("MATCHLAYER_LLM_MAX_OUTPUT_TOKENS", "llm_max_output_tokens", False),
    ("MATCHLAYER_LLM_DAILY_QUOTA", "llm_daily_quota", False),
    ("MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD", "llm_monthly_spend_limit_usd", True),
    ("MATCHLAYER_LLM_MAX_BULLETS", "llm_max_bullets", False),
    ("MATCHLAYER_LLM_MAX_BULLET_CHARS", "llm_max_bullet_chars", False),
    ("MATCHLAYER_LLM_MAX_QUESTIONS", "llm_max_questions", False),
    ("MATCHLAYER_LLM_CACHE_TTL_SECONDS", "llm_cache_ttl_seconds", False),
)

_QUESTIONS_FLOOR = 5

_setting = st.sampled_from(_NUMERIC_SETTINGS)

# Non-positive magnitudes: zero and a wide negative range. Converted to
# ``Decimal`` for the spend-limit field so the generated value matches the
# field's declared type exactly.
_non_positive_int = st.integers(min_value=-(10**6), max_value=0)

# Positive magnitudes for the acceptance property. The floor of 5 keeps a
# single strategy valid for every field in the set, including
# ``llm_max_questions`` (whose extra constraint is exactly >= 5); values
# below 5 for the *other* fields are separately known-good because their
# defaults (e.g. ``llm_max_bullets = 5``) are near that region and the
# validator only checks positivity for them.
_positive_int = st.integers(min_value=1, max_value=10**6)


def _coerce(value: int, is_decimal: bool) -> int | Decimal:
    """Match the generated magnitude to the field's declared type."""
    return Decimal(value) if is_decimal else value


def _build_settings(field_name: str, value: int | Decimal) -> Settings:
    # Merge into one ``dict[str, Any]`` before unpacking: the pydantic mypy
    # plugin types ``Settings.__init__`` per field, so a second unpack typed
    # ``dict[str, int | Decimal]`` would be rejected against non-numeric
    # fields even though only ``field_name`` is ever present at runtime.
    kwargs: dict[str, Any] = {**_BASE_SETTINGS_KWARGS, field_name: value}
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(setting=_setting, magnitude=_non_positive_int)
def test_non_positive_values_are_rejected(setting: tuple[str, str, bool], magnitude: int) -> None:
    """Rejection (Requirement 18.2): any single Phase 3 numeric setting
    assigned a non-positive value raises ``ValidationError`` at construction,
    and the error message names the offending ``MATCHLAYER_LLM_*`` env var."""
    env_name, field_name, is_decimal = setting

    with pytest.raises(ValidationError) as excinfo:
        _build_settings(field_name, _coerce(magnitude, is_decimal))

    assert env_name in str(excinfo.value)


@settings(max_examples=100, deadline=None)
@given(value=st.integers(min_value=1, max_value=_QUESTIONS_FLOOR - 1))
def test_sub_floor_question_bounds_are_rejected(value: int) -> None:
    """Floor rejection (Requirement 7.8): a positive ``llm_max_questions``
    below 5 raises ``ValidationError`` naming
    ``MATCHLAYER_LLM_MAX_QUESTIONS`` — the positivity check cannot be what
    trips here, so this pins the schema-floor branch specifically."""
    with pytest.raises(ValidationError) as excinfo:
        _build_settings("llm_max_questions", value)

    assert "MATCHLAYER_LLM_MAX_QUESTIONS" in str(excinfo.value)


@settings(max_examples=100, deadline=None)
@given(setting=_setting, magnitude=_positive_int)
def test_positive_values_construct(setting: tuple[str, str, bool], magnitude: int) -> None:
    """Acceptance (Requirements 7.8, 18.2): any positive assignment — with
    ``llm_max_questions`` clamped to >= 5 — builds a ``Settings`` instance
    that stores the value verbatim."""
    _env_name, field_name, is_decimal = setting
    if field_name == "llm_max_questions":
        magnitude = max(magnitude, _QUESTIONS_FLOOR)
    value = _coerce(magnitude, is_decimal)

    built = _build_settings(field_name, value)

    assert getattr(built, field_name) == value
