"""Feature: phase-2-nlp-embeddings — Property 6.

Property 6: Invalid score weights are rejected at settings construction.

    *For any* weight pair that does not sum to 1.0 within ±0.001 or in which
    either weight lies outside [0, 1], constructing ``Settings`` raises a
    validation error naming the weight misconfiguration; *for any* valid
    weight pair, construction succeeds.

**Validates: Requirements 3.9**

The ``Settings._score_weights_sum_to_one`` model validator (extended by
phase-2 task 8.1) enforces two constraints, in order:

1. Each of ``score_weight_similarity`` / ``score_weight_keyword`` lies in
   the inclusive range ``[0, 1]``.
2. The pair sums to ``1.0`` within an absolute tolerance of ``±0.001``.

Three properties pin the contract across generated inputs:

* **Valid pairs construct** — both weights in ``[0, 1]`` and the sum within
  the tolerance → ``Settings`` builds and stores the pair verbatim.
* **Out-of-range weights are rejected** — either weight outside ``[0, 1]``
  raises :class:`pydantic.ValidationError`, even when the pair happens to
  sum to ``1.0`` (e.g. ``1.5 + -0.5``).
* **Out-of-tolerance sums are rejected** — both weights in ``[0, 1]`` but
  the sum off by more than the tolerance raises
  :class:`pydantic.ValidationError`.

Both rejection properties additionally assert the error message names the
weight env vars, so an operator can locate the misconfiguration from the
error alone.

Generator note on the tolerance boundary: to keep the properties free of
IEEE-754 edge flakiness, generated *valid* sums stay within ``±9e-4`` of
``1.0`` (a clear margin inside the ``±0.001`` tolerance) and generated
*invalid* sums deviate by at least ``2e-3`` (a clear margin outside it).
The exact boundary behavior is an implementation detail of
:func:`math.isclose` and is deliberately not probed here.

``Settings`` is constructed with explicit kwargs for every required field
(mirroring ``tests/unit/test_matching_config_and_errors.py``) so the cases
are hermetic and never depend on the repo ``.env``.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from matchlayer_api.config import Settings

# ---------------------------------------------------------------------------
# Hermetic Settings kwargs (mirrors tests/unit/test_matching_config_and_errors)
# ---------------------------------------------------------------------------

# 33 bytes UTF-8 — clears the 32-byte floor in
# ``Settings._jwt_secret_min_length``. Same synthetic constant the auth unit
# tests use so the value is recognizably a test fixture.
_TEST_SECRET = "test-jwt-secret-32-byte-floor-pad"  # gitleaks:allow — synthetic test value

# Every field ``Settings`` requires, with placeholder values that pass
# Pydantic validation without touching the repo's ``.env``. The
# ``score_weight_*`` fields are intentionally omitted so each property
# supplies the pair under test.
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
# Weight-pair generators
# ---------------------------------------------------------------------------

# Margins around the ±0.001 sum tolerance. Valid pairs stay comfortably
# inside; invalid pairs deviate comfortably outside. The gap between the two
# absorbs the float error accumulated while composing the pair.
_VALID_SUM_MARGIN = 9e-4
_INVALID_SUM_MARGIN = 2e-3

# A weight safely inside the inclusive [0, 1] range (endpoints included).
_in_range_weight = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)

# A weight clearly outside [0, 1]: at least 1e-3 beyond either endpoint so
# the range check (not float noise) is what trips.
_out_of_range_weight = st.one_of(
    st.floats(min_value=-1e6, max_value=-1e-3, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1.0 + 1e-3, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _valid_weight_pairs(draw: st.DrawFn) -> tuple[float, float]:
    """A pair with both weights in [0, 1] and the sum within the tolerance.

    Draw one weight freely, then place the other at ``1 - w`` shifted by a
    delta within the valid margin, discarding the rare draws that push the
    complement outside [0, 1].
    """
    w_similarity = draw(_in_range_weight)
    delta = draw(
        st.floats(
            min_value=-_VALID_SUM_MARGIN,
            max_value=_VALID_SUM_MARGIN,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    w_keyword = 1.0 - w_similarity + delta
    assume(0.0 <= w_keyword <= 1.0)
    return (w_similarity, w_keyword)


@st.composite
def _out_of_range_pairs(draw: st.DrawFn) -> tuple[float, float]:
    """A pair in which at least one weight lies clearly outside [0, 1].

    Half the time the *other* weight is chosen so the pair still sums to
    ~1.0 — the hard case proving the range check is independent of the sum
    check (e.g. ``1.5 + -0.5``).
    """
    bad = draw(_out_of_range_weight)
    complement_sums_to_one = draw(st.booleans())
    other = 1.0 - bad if complement_sums_to_one else draw(_in_range_weight)
    bad_is_similarity = draw(st.booleans())
    return (bad, other) if bad_is_similarity else (other, bad)


@st.composite
def _out_of_tolerance_pairs(draw: st.DrawFn) -> tuple[float, float]:
    """A pair with both weights in [0, 1] but the sum clearly off 1.0.

    Both weights are drawn independently from [0, 1]; pairs whose sum lands
    within the invalid margin of 1.0 are discarded, leaving only sums that
    deviate by more than twice the documented tolerance.
    """
    w_similarity = draw(_in_range_weight)
    w_keyword = draw(_in_range_weight)
    assume(abs(w_similarity + w_keyword - 1.0) > _INVALID_SUM_MARGIN)
    return (w_similarity, w_keyword)


def _build_settings(w_similarity: float, w_keyword: float) -> Settings:
    return Settings(
        **_BASE_SETTINGS_KWARGS,
        score_weight_similarity=w_similarity,
        score_weight_keyword=w_keyword,
    )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(pair=_valid_weight_pairs())
def test_valid_weight_pairs_construct(pair: tuple[float, float]) -> None:
    """Acceptance (Requirement 3.9): any pair with both weights in [0, 1]
    summing to 1.0 within the tolerance builds a ``Settings`` instance that
    stores the pair verbatim."""
    w_similarity, w_keyword = pair

    built = _build_settings(w_similarity, w_keyword)

    assert built.score_weight_similarity == w_similarity
    assert built.score_weight_keyword == w_keyword


@settings(max_examples=200, deadline=None)
@given(pair=_out_of_range_pairs())
def test_out_of_range_weights_are_rejected(pair: tuple[float, float]) -> None:
    """Range rejection (Requirement 3.9): a weight outside [0, 1] raises
    ``ValidationError`` at construction — even when the pair sums to 1.0 —
    and the error names the weight env vars."""
    w_similarity, w_keyword = pair

    with pytest.raises(ValidationError) as excinfo:
        _build_settings(w_similarity, w_keyword)

    message = str(excinfo.value)
    assert "MATCHLAYER_SCORE_WEIGHT_SIMILARITY" in message
    assert "MATCHLAYER_SCORE_WEIGHT_KEYWORD" in message


@settings(max_examples=200, deadline=None)
@given(pair=_out_of_tolerance_pairs())
def test_out_of_tolerance_sums_are_rejected(pair: tuple[float, float]) -> None:
    """Sum rejection (Requirement 3.9): both weights in [0, 1] but the sum
    off 1.0 by more than the ±0.001 tolerance raises ``ValidationError``
    naming the weight env vars."""
    w_similarity, w_keyword = pair

    with pytest.raises(ValidationError) as excinfo:
        _build_settings(w_similarity, w_keyword)

    message = str(excinfo.value)
    assert "MATCHLAYER_SCORE_WEIGHT_SIMILARITY" in message
    assert "MATCHLAYER_SCORE_WEIGHT_KEYWORD" in message
