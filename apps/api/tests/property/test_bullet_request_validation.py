"""Feature: phase-3-llm-layer — Property 11.

Property 11: Bullet request validation rejects invalid input before any call.

    *For any* submitted bullet list, the request is rejected with a 422
    RFC 7807 response before any provider call if and only if the count
    is outside 1..``llm_max_bullets``, any bullet is empty or
    whitespace-only, or any bullet exceeds ``llm_max_bullet_chars``; all
    other lists are accepted.

**Validates: Requirements 6.3**

The unit under test is :class:`BulletRewriteRequest`, the Pydantic body
of ``POST /api/v1/matches/{matchId}/bullet-rewrites``. FastAPI validates
the request body before the route handler runs, and a
:class:`pydantic.ValidationError` there surfaces as the app's 422
RFC 7807 response — so at this level "rejected with 422 before any
provider call" is exactly "model construction raises
``ValidationError``": an invalid submission never reaches the router
body, and therefore never reaches redaction, quota accounting, or the
LLM_Client (the HTTP-layer mapping itself is covered by the task 10.9
router integration tests).

Three properties pin the contract:

* **Outcome matches the bound oracle (the iff)** — for generated lists
  mixing valid, empty/whitespace-only, and over-length bullets at
  counts from 0 to beyond the ceiling, construction raises
  ``ValidationError`` exactly when the Property 11 predicate says the
  list is invalid.
* **Valid lists are accepted verbatim** — any in-bounds list of
  non-blank, in-length bullets constructs, and the accepted value
  preserves every bullet byte-for-byte (no stripping/normalization —
  the Requirement 6.7 alignment check compares exactly).
* **Count violations alone are rejected** — a list of individually
  valid bullets whose count is 0 or above ``llm_max_bullets`` is
  rejected, pinning the count branch deterministically.

Whitespace semantics: the validator classifies "whitespace-only" via
Python's ``str.strip()`` (``if not bullet.strip()``), so the oracle here
uses ``str.strip()`` too — not pydantic-core's ``strip_whitespace``
definition, which disagrees on characters like ``\\x1f``.

The settings-reading validator is pinned by patching the module-level
``get_settings`` binding per example (the pattern of
``tests/unit/test_llm_bullets.py``, applied inside the test body because
Hypothesis re-runs the body, not pytest fixtures, per example).
"""

# Feature: phase-3-llm-layer, Property 11: Bullet request validation rejects invalid input before any call  # noqa: E501

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

import matchlayer_api.services.llm.bullets as bullets_module
from matchlayer_api.services.llm.bullets import BulletRewriteRequest

# ---------------------------------------------------------------------------
# Pinned request bounds
# ---------------------------------------------------------------------------

_MAX_BULLETS = 5
# Kept intentionally small (the real default is 500) so over-length
# bullets are cheap to generate and shrink; the validator reads the
# ceiling from settings either way.
_MAX_BULLET_CHARS = 120


def _pinned_settings() -> SimpleNamespace:
    return SimpleNamespace(
        llm_max_bullets=_MAX_BULLETS,
        llm_max_bullet_chars=_MAX_BULLET_CHARS,
    )


def _construct(bullets: list[str]) -> BulletRewriteRequest:
    """Run request validation under the pinned bounds."""
    with mock.patch.object(bullets_module, "get_settings", _pinned_settings):
        return BulletRewriteRequest(bullets=bullets)


def _is_valid(bullets: list[str]) -> bool:
    """The Property 11 predicate, stated independently of the validator.

    ``str.strip()`` deliberately matches the validator's whitespace
    definition (Python semantics, not pydantic-core's).
    """
    return 1 <= len(bullets) <= _MAX_BULLETS and all(
        bullet.strip() and len(bullet) <= _MAX_BULLET_CHARS for bullet in bullets
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A bullet that satisfies every per-bullet bound: non-blank under
# str.strip() and within the character ceiling.
_valid_bullet = st.text(min_size=1, max_size=_MAX_BULLET_CHARS).filter(lambda s: bool(s.strip()))

# Empty or whitespace-only under str.strip(): drawn from characters
# Python treats as strippable whitespace (including the C1/EBCDIC-era
# separators \x1c-\x1f and \x85/\xa0), plus the empty string. The filter
# is a guard so the strategy stays correct if the alphabet drifts.
_blank_bullet = st.text(
    alphabet=" \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0",
    max_size=8,
).filter(lambda s: not s.strip())

# Over the character ceiling (may additionally be blank — still invalid).
_over_length_bullet = st.text(
    min_size=_MAX_BULLET_CHARS + 1,
    max_size=_MAX_BULLET_CHARS + 16,
)

_any_bullet = st.one_of(_valid_bullet, _blank_bullet, _over_length_bullet)

# Mixed lists spanning both sides of every bound: counts 0..ceiling+3,
# bullets from all three classes.
_mixed_bullet_lists = st.lists(_any_bullet, min_size=0, max_size=_MAX_BULLETS + 3)

# In-bounds lists of individually valid bullets (the acceptance branch).
_valid_bullet_lists = st.lists(_valid_bullet, min_size=1, max_size=_MAX_BULLETS)

# Out-of-bounds counts of individually valid bullets (the count branch
# in isolation: nothing else about the list is wrong).
_count_violating_lists = st.one_of(
    st.just([]),
    st.lists(_valid_bullet, min_size=_MAX_BULLETS + 1, max_size=_MAX_BULLETS + 3),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(bullets=_mixed_bullet_lists)
def test_validation_outcome_matches_bound_oracle(bullets: list[str]) -> None:
    """The iff (Requirement 6.3): construction raises ``ValidationError``
    exactly when the count is outside 1..``llm_max_bullets``, any bullet
    is empty/whitespace-only, or any bullet exceeds
    ``llm_max_bullet_chars`` — and accepts every other list."""
    if _is_valid(bullets):
        request = _construct(bullets)
        assert request.bullets == bullets
    else:
        with pytest.raises(ValidationError):
            _construct(bullets)


@settings(max_examples=100, deadline=None)
@given(bullets=_valid_bullet_lists)
def test_valid_lists_are_accepted_verbatim(bullets: list[str]) -> None:
    """Acceptance (Requirement 6.3): every in-bounds list of non-blank,
    in-length bullets constructs, preserving each bullet byte-for-byte
    (no stripping — Requirement 6.7's alignment check compares exactly)."""
    request = _construct(bullets)

    assert request.bullets == bullets


@settings(max_examples=100, deadline=None)
@given(bullets=_count_violating_lists)
def test_count_violations_alone_are_rejected(bullets: list[str]) -> None:
    """Count branch (Requirement 6.3): a list of individually valid
    bullets with count 0 or above ``llm_max_bullets`` is rejected."""
    with pytest.raises(ValidationError):
        _construct(bullets)
