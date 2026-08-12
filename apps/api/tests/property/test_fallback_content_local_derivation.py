"""Feature: phase-3-llm-layer — Property 10.

# Feature: phase-3-llm-layer, Property 10: Fallback content is schema-conformant and locally derived

Property 10: Fallback content is schema-conformant and locally derived.

    *For any* Match_Result stored fields (matched/missing keywords and
    suggestions, including empty lists) and any valid submitted bullets,
    each feature's Fallback_Response validates against the same result
    schema as the LLM path, is marked ``is_fallback=true`` with a failure
    reason, and is derived only from the stored fields: the coach fallback
    carries the stored suggestions/missing skills (empty lists carried as
    empty), the rewriter fallback carries every submitted bullet
    unchanged, and the question-generator fallback contains at least 5
    schema-valid questions even when both skill lists are empty.

**Validates: Requirements 5.5, 6.6, 7.5, 9.2, 9.3**

One property per feature service, each exercising ``build_fallback``
directly over generated Match_Result stored fields (arbitrary strings,
whitespace-only entries, case-variant duplicates, empty lists) and — for
the Bullet_Rewriter — generated valid submissions:

* **Schema conformance + envelope marking (Req 9.2)** — every fallback
  re-validates against the *same* Pydantic result schema the LLM path
  uses (``model_validate(model_dump())`` round trip, exercising every
  bound and validator), and wraps into
  ``LLMResultEnvelope[schema](is_fallback=True, fallback_reason=...)``
  for every ``FailureReason`` value — the machine-readable fallback
  marker and closed failure-reason enum the requirement demands.
* **Local derivation (Req 9.3)** — every content item is traceable to a
  stored Match_Result field or a committed generic constant: coach gaps
  mirror the stored missing keywords one-for-one, coach improvements are
  the stored suggestions (verbatim, in stored order) plus generic
  top-up, bullet guidance names the stored skills/suggestions, and every
  fallback question either embeds a stored skill or is one of the
  committed generic questions. Nothing else can appear.
* **Documented interpretation** (coach.py / questions.py docstrings,
  task 8.2): stored JSONB entries are normalized for fallback *content*
  (stripped, empties dropped, case-insensitive de-dup in first-occurrence
  order — ``_clean`` below re-implements that documented rule as the
  oracle); the coach tops improvements up with generic actions to the
  CoachingReport schema's 3-action floor and caps at its 10-action
  ceiling; the question generator tops up with generics to the
  5-question floor and caps at ``llm_max_questions``.

The Bullet_Rewriter's ``alternatives`` field is schema-constrained
(``strip_whitespace=True``), so the single fallback alternative carries
the submitted bullet modulo that documented surrounding-whitespace
normalization; ``original`` carries it byte-for-byte (the Req 6.7
alignment contract).

Settings are read once at module scope (real values, matching the
startup-validated invariants ``llm_max_questions >= 5`` and
``llm_max_bullets >= 1``) so no function-scoped fixture flows into a
``@given`` body.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.db.models import MatchResult
from matchlayer_api.services.llm import coach as coach_module
from matchlayer_api.services.llm import questions as questions_module
from matchlayer_api.services.llm.bullets import BulletRewriteInput
from matchlayer_api.services.llm.bullets import build_fallback as build_bullet_fallback
from matchlayer_api.services.llm.coach import ResumeCoachInput
from matchlayer_api.services.llm.coach import build_fallback as build_coach_fallback
from matchlayer_api.services.llm.questions import InterviewQuestionsInput
from matchlayer_api.services.llm.questions import build_fallback as build_questions_fallback
from matchlayer_api.services.llm.schemas import (
    BulletRewrite,
    BulletRewriteEntry,
    CoachingReport,
    FailureReason,
    InterviewQuestionCategory,
    InterviewQuestionSet,
    LLMResultEnvelope,
)

_SETTINGS = get_settings()
_MAX_QUESTIONS = _SETTINGS.llm_max_questions
_MAX_BULLETS = _SETTINGS.llm_max_bullets
_MAX_BULLET_CHARS = _SETTINGS.llm_max_bullet_chars

# CoachingReport improvement bounds (Req 5.2) — schema constants mirrored
# in coach.py; asserted against the revalidated fallback below.
_MIN_IMPROVEMENTS = 3
_MAX_IMPROVEMENTS = 10
_MIN_QUESTIONS = 5


# ---------------------------------------------------------------------------
# Oracle: the documented stored-entry normalization rule.
# ---------------------------------------------------------------------------


def _clean(raw: list[str]) -> list[str]:
    """The documented fallback-content normalization of stored JSONB lists.

    Strip surrounding whitespace, drop empties, de-duplicate
    case-insensitively in first-occurrence order (coach.py / questions.py
    docstrings). Re-implemented here as the independent oracle for what
    "the stored suggestions/missing skills" means in fallback content.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw:
        text = entry.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _schema_normalized(bullet: str) -> str:
    """The value the BulletRewrite schema's ``alternatives`` field produces.

    ``alternatives`` is schema-constrained with ``strip_whitespace=True``,
    which is pydantic-core's whitespace definition — *not* Python's
    ``str.strip()``. The two disagree on some control characters (e.g.
    ``"\\x1f"`` is stripped by ``str.strip()`` but kept by pydantic-core),
    so the oracle round-trips the bullet through the schema field itself
    instead of re-implementing the stripping rule. This tests the real
    contract: the fallback carries the bullet unchanged modulo the
    schema's own normalization (Req 9.2 same-schema rule).

    Safe for every ``_valid_bullet``: pydantic-core strips a subset of
    what ``str.strip()`` strips, so a bullet that is non-empty under
    ``str.strip()`` is non-empty under the schema's stripping too.
    """
    return BulletRewriteEntry(
        original=bullet, alternatives=[bullet], rationale="rationale"
    ).alternatives[0]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Stored JSONB entries: mostly plausible short skill/suggestion text, with
# whitespace-only entries, blanks, case-variant duplicates, and the odd
# oversized string (exercising the question generator's skip-not-truncate
# rule) mixed in.
_stored_entry = st.one_of(
    st.text(min_size=1, max_size=40),
    st.sampled_from(["python", "Python", "PYTHON ", "docker", "  ", "", "\t"]),
    st.just("x" * 400),
)

_stored_list = st.lists(_stored_entry, max_size=8)

# Valid submitted bullets per Requirement 6.3: 1..llm_max_bullets entries,
# none empty/whitespace-only, each within llm_max_bullet_chars. Arbitrary
# text (including surrounding whitespace) otherwise.
_valid_bullet = st.text(min_size=1, max_size=min(_MAX_BULLET_CHARS, 60)).filter(
    lambda s: bool(s.strip())
)
_valid_bullets = st.lists(_valid_bullet, min_size=1, max_size=min(_MAX_BULLETS, 6))

_failure_reason = st.sampled_from(FailureReason)


def _match(*, matched: list[str], missing: list[str], suggestions: list[str]) -> MatchResult:
    return MatchResult(
        id=uuid7(),
        user_id=uuid7(),
        job_description_text="We need a senior backend engineer.",
        matched_keywords=matched,
        missing_keywords=missing,
        suggestions=suggestions,
    )


# ---------------------------------------------------------------------------
# Resume_Coach fallback (Req 5.5, 9.2, 9.3).
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    matched=_stored_list,
    missing=_stored_list,
    suggestions=_stored_list,
    reason=_failure_reason,
)
def test_coach_fallback_is_schema_conformant_and_locally_derived(
    matched: list[str],
    missing: list[str],
    suggestions: list[str],
    reason: FailureReason,
) -> None:
    """The coach fallback validates against CoachingReport, wraps into a
    fallback-marked envelope, mirrors the stored missing skills as gaps
    (empty carried as empty), and carries the stored suggestions verbatim
    in stored order topped up with generics to the schema floor."""
    match = _match(matched=matched, missing=missing, suggestions=suggestions)
    fallback = build_coach_fallback(match, ResumeCoachInput(resume_text="resume"), reason)

    # Schema conformance (Req 9.2): same CoachingReport schema as the LLM
    # path, full round trip through every bound and the ordering validator.
    revalidated = CoachingReport.model_validate(fallback.model_dump())
    assert _MIN_IMPROVEMENTS <= len(revalidated.improvements) <= _MAX_IMPROVEMENTS

    # Envelope marking (Req 9.2): fallback marker + closed failure reason.
    envelope = LLMResultEnvelope[CoachingReport](
        is_fallback=True, fallback_reason=reason, result=fallback
    )
    assert envelope.is_fallback is True
    assert envelope.fallback_reason is reason
    assert envelope.id is None and envelope.created_at is None

    # Gaps mirror the stored missing skills one-for-one, in stored order;
    # an empty stored list is carried as an empty gaps list (Req 5.5).
    cleaned_missing = _clean(missing)
    assert len(fallback.gaps) == len(cleaned_missing)
    for gap, skill in zip(fallback.gaps, cleaned_missing, strict=True):
        assert skill in gap
    if not cleaned_missing:
        assert fallback.gaps == []

    # Improvements carry the stored suggestions verbatim in stored order,
    # capped at the 10-action ceiling and topped up with the committed
    # generic actions to the 3-action floor (documented interpretation of
    # Req 5.5 under the Req 9.2 same-schema rule).
    stored = _clean(suggestions)[:_MAX_IMPROVEMENTS]
    expected = stored + list(
        coach_module._GENERIC_FALLBACK_ACTIONS[: max(0, _MIN_IMPROVEMENTS - len(stored))]
    )
    assert [item.action for item in fallback.improvements] == expected
    assert [item.priority for item in fallback.improvements] == list(range(1, len(expected) + 1))

    # Local derivation (Req 9.3): nothing beyond stored fields + the fixed
    # summary/generics — strengths are never fabricated.
    assert fallback.strengths == []
    assert fallback.summary == coach_module._FALLBACK_SUMMARY


# ---------------------------------------------------------------------------
# Bullet_Rewriter fallback (Req 6.6, 9.2, 9.3).
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    bullets=_valid_bullets,
    missing=_stored_list,
    suggestions=_stored_list,
    reason=_failure_reason,
)
def test_bullet_fallback_carries_every_submitted_bullet_unchanged(
    bullets: list[str],
    missing: list[str],
    suggestions: list[str],
    reason: FailureReason,
) -> None:
    """The rewriter fallback validates against BulletRewrite, wraps into a
    fallback-marked envelope, carries every submitted bullet unchanged
    (one entry per bullet, submission order, ``original`` byte-for-byte),
    and pairs it with guidance naming only stored fields."""
    match = _match(matched=[], missing=missing, suggestions=suggestions)
    feature_input = BulletRewriteInput(bullets=tuple(bullets))
    fallback = build_bullet_fallback(match, feature_input, reason)

    # Schema conformance (Req 9.2): same BulletRewrite schema as the LLM path.
    revalidated = BulletRewrite.model_validate(fallback.model_dump())
    assert len(revalidated.entries) >= 1

    # Envelope marking (Req 9.2).
    envelope = LLMResultEnvelope[BulletRewrite](
        is_fallback=True, fallback_reason=reason, result=fallback
    )
    assert envelope.is_fallback is True
    assert envelope.fallback_reason is reason

    # Every submitted bullet unchanged (Req 6.6): exactly one entry per
    # bullet, in submission order, ``original`` byte-for-byte (the Req 6.7
    # alignment contract). The single alternative is the same bullet modulo
    # the schema's own surrounding-whitespace normalization on
    # ``alternatives`` (strip_whitespace=True — Req 9.2 same-schema rule),
    # computed by round-tripping through the schema field rather than
    # re-implementing pydantic-core's stripping (see _schema_normalized).
    assert len(fallback.entries) == len(bullets)
    for entry, bullet in zip(fallback.entries, bullets, strict=True):
        assert entry.original == bullet
        assert entry.alternatives == [_schema_normalized(bullet)]

    # Guidance derived exclusively from stored fields (Req 6.6, 9.3): a
    # single rationale shared by every entry, naming the stored missing
    # skills and suggestions the builder enumerates (first 5 / first 3
    # under the documented normalization) — non-empty even when both
    # stored lists are empty.
    rationales = {entry.rationale for entry in fallback.entries}
    assert len(rationales) == 1
    rationale = fallback.entries[0].rationale
    assert rationale.strip()
    for skill in _clean(missing)[:5]:
        assert skill in rationale
    for suggestion in _clean(suggestions)[:3]:
        assert suggestion in rationale


# ---------------------------------------------------------------------------
# Interview_Question_Generator fallback (Req 7.5, 9.2, 9.3).
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    matched=_stored_list,
    missing=_stored_list,
    reason=_failure_reason,
)
def test_question_fallback_meets_floor_and_derives_from_stored_skills(
    matched: list[str],
    missing: list[str],
    reason: FailureReason,
) -> None:
    """The question fallback validates against InterviewQuestionSet, wraps
    into a fallback-marked envelope, always carries at least 5 questions
    (generics fill in when both stored lists are empty), stays within the
    configured ceiling, and every question embeds a stored skill or is a
    committed generic."""
    match = _match(matched=matched, missing=missing, suggestions=[])
    fallback = build_questions_fallback(
        match, InterviewQuestionsInput(resume_text="resume"), reason
    )

    # Schema conformance (Req 9.2, 7.5): same InterviewQuestionSet schema as
    # the LLM path, including the settings-read ceiling validator.
    revalidated = InterviewQuestionSet.model_validate(fallback.model_dump())
    assert _MIN_QUESTIONS <= len(revalidated.questions) <= _MAX_QUESTIONS

    # Envelope marking (Req 9.2).
    envelope = LLMResultEnvelope[InterviewQuestionSet](
        is_fallback=True, fallback_reason=reason, result=fallback
    )
    assert envelope.is_fallback is True
    assert envelope.fallback_reason is reason

    # Floor even with both stored lists empty (Req 7.5).
    assert len(fallback.questions) >= _MIN_QUESTIONS

    # Local derivation (Req 9.3): every question is a gap question embedding
    # a stored missing skill, a technical depth question embedding a stored
    # matched skill, or one of the committed generic questions.
    cleaned_missing = _clean(missing)
    cleaned_matched = _clean(matched)
    generics = questions_module._GENERIC_FALLBACK_QUESTIONS
    for question in fallback.questions:
        if question in generics:
            continue
        if question.category is InterviewQuestionCategory.EXPERIENCE_GAP:
            assert any(skill in question.question for skill in cleaned_missing)
        elif question.category is InterviewQuestionCategory.TECHNICAL:
            assert any(skill in question.question for skill in cleaned_matched)
        else:  # pragma: no cover - would indicate non-local content
            raise AssertionError(
                "fallback question is neither generic nor derived from a stored skill"
            )
