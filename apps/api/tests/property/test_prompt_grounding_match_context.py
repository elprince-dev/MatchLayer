"""Feature: phase-3-llm-layer — Property 7.

# Feature: phase-3-llm-layer, Property 7: Prompt grounding includes stored match context

Property 7: Prompt grounding includes stored match context.

    *For any* Match_Result with arbitrary stored matched/missing skill
    lists (and, for the Bullet_Rewriter, any submitted bullets), the
    assembled prompt's user-content region contains the stored fields
    required by the feature — matched and missing skills verbatim for
    the coach and question generator, job-description context and
    missing skills for the rewriter — without recomputation.

**Validates: Requirements 5.3, 6.4, 7.6**

One property per feature service pins the grounding contract of its
``build_inputs`` and of the prompt actually assembled from it:

* **Resume_Coach** (Req 5.3) and **Interview_Question_Generator**
  (Req 7.6) — the ``matched_skills`` / ``missing_skills`` sections carry
  the Match_Result's stored lists verbatim (comma-join is the only
  transformation, ``(none)`` for an empty list), marked
  ``redaction=None`` so the orchestrator passes them through untouched;
  the resume and Job_Description sections carry the stored texts; and
  the user-role message built by :func:`build_messages` contains each
  skill region byte-for-byte inside its own delimited wrapper.
* **Bullet_Rewriter** (Req 6.4) — the ``missing_skills`` section carries
  the stored list verbatim, the ``job_description`` section carries the
  Match_Result's stored Job_Description text, and the ``bullets``
  section carries every submitted bullet verbatim, numbered, in
  submission order.

"Without recomputation" is evidenced by construction: the generated
skill lists are arbitrary strings drawn independently of the generated
resume/JD text, so no re-derivation from those texts could reproduce
them — their verbatim appearance in the prompt regions proves the
stored fields themselves were used. The expected region text is
recomputed here independently (``", ".join`` / ``(none)``), never by
calling the modules' own formatting helpers.

Skill strings are constrained to a realistic lexicon-term alphabet
(letters, digits, ``+ # . / -``, spaces — how Phase 2 stored keywords
actually look), which also keeps them free of delimiter sequences so
the assembled-message region assertion is exact under the wrapper's
neutralization step.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from uuid_utils.compat import uuid7

from matchlayer_api.db.models import MatchResult
from matchlayer_api.services.llm.bullets import BulletRewriteInput
from matchlayer_api.services.llm.bullets import build_inputs as build_bullet_inputs
from matchlayer_api.services.llm.coach import ResumeCoachInput
from matchlayer_api.services.llm.coach import build_inputs as build_coach_inputs
from matchlayer_api.services.llm.orchestrator import PromptInputs, PromptSection
from matchlayer_api.services.llm.prompting import UserContentSection, build_messages
from matchlayer_api.services.llm.questions import InterviewQuestionsInput
from matchlayer_api.services.llm.questions import build_inputs as build_question_inputs

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_SKILL_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+#./- "

_skill = st.text(alphabet=_SKILL_ALPHABET, min_size=1, max_size=30).filter(
    lambda s: bool(s.strip())
)

_skills = st.lists(_skill, max_size=6)

_free_text = st.text(max_size=200)

# Valid submitted bullets per the Requirement 6.3 bounds the request
# model enforces upstream: non-empty, not whitespace-only.
_bullet = st.text(min_size=1, max_size=80).filter(lambda b: bool(b.strip()))

_bullets = st.lists(_bullet, min_size=1, max_size=5)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match(*, matched: list[str] | None = None, missing: list[str], jd_text: str) -> MatchResult:
    """A Match_Result carrying arbitrary stored Phase 2 analysis fields."""
    return MatchResult(
        id=uuid7(),
        user_id=uuid7(),
        job_description_text=jd_text,
        matched_keywords=matched if matched is not None else [],
        missing_keywords=missing,
    )


def _expected_skill_region(skills: list[str]) -> str:
    """The verbatim comma-join grounding contract, recomputed independently."""
    return ", ".join(skills) if skills else "(none)"


def _sections_by_kind(inputs: PromptInputs) -> dict[str, PromptSection]:
    sections = {section.kind: section for section in inputs.sections}
    assert len(sections) == len(inputs.sections)  # kinds are unique per feature
    return sections


def _assembled_user_message(inputs: PromptInputs) -> str:
    """Assemble the user-role message exactly as the orchestrator does.

    Sections marked ``redaction=None`` (the stored skill lists) pass
    through unredacted in the real pipeline, so mapping every section
    text as-is preserves the skill regions this property asserts on;
    :func:`build_messages` applies the wrapper's own delimiting and
    neutralization.
    """
    messages = build_messages(
        "system instructions",
        [UserContentSection(kind=section.kind, text=section.text) for section in inputs.sections],
    )
    assert messages[1].role == "user"
    return messages[1].content


def _assert_skill_region_in_user_message(user_message: str, kind: str, skills: list[str]) -> None:
    """The stored list appears verbatim inside its own delimited region."""
    expected = _expected_skill_region(skills)
    region = f'<user_content kind="{kind}">\n{expected}\n</user_content>'
    assert region in user_message


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(matched=_skills, missing=_skills, jd_text=_free_text, resume_text=_free_text)
def test_coach_prompt_grounded_in_stored_match_context(
    matched: list[str], missing: list[str], jd_text: str, resume_text: str
) -> None:
    """Resume_Coach grounding (Requirement 5.3): the assembled prompt's
    user-content regions carry the stored matched/missing skill lists
    verbatim and the stored resume/JD texts — never a recomputation."""
    match = _match(matched=matched, missing=missing, jd_text=jd_text)
    inputs = build_coach_inputs(match, ResumeCoachInput(resume_text=resume_text))
    sections = _sections_by_kind(inputs)

    # Stored skill lists pass through verbatim and unredacted (Req 5.3).
    for kind, skills in (("matched_skills", matched), ("missing_skills", missing)):
        section = sections[kind]
        assert section.redaction is None
        assert section.text == _expected_skill_region(skills)
        for skill in skills:
            assert skill in section.text

    # The stored resume and Job_Description texts are the PII-bearing regions.
    assert sections["resume"].text == resume_text
    assert sections["job_description"].text == jd_text

    # The assembled user-role message carries each skill region byte-for-byte.
    user_message = _assembled_user_message(inputs)
    _assert_skill_region_in_user_message(user_message, "matched_skills", matched)
    _assert_skill_region_in_user_message(user_message, "missing_skills", missing)


@settings(max_examples=100, deadline=None)
@given(matched=_skills, missing=_skills, jd_text=_free_text, resume_text=_free_text)
def test_question_generator_prompt_grounded_in_stored_match_context(
    matched: list[str], missing: list[str], jd_text: str, resume_text: str
) -> None:
    """Interview_Question_Generator grounding (Requirement 7.6): the
    assembled prompt's user-content regions carry the stored
    matched/missing skill lists verbatim — never a recomputation."""
    match = _match(matched=matched, missing=missing, jd_text=jd_text)
    inputs = build_question_inputs(match, InterviewQuestionsInput(resume_text=resume_text))
    sections = _sections_by_kind(inputs)

    for kind, skills in (("matched_skills", matched), ("missing_skills", missing)):
        section = sections[kind]
        assert section.redaction is None
        assert section.text == _expected_skill_region(skills)
        for skill in skills:
            assert skill in section.text

    assert sections["resume"].text == resume_text
    assert sections["job_description"].text == jd_text

    user_message = _assembled_user_message(inputs)
    _assert_skill_region_in_user_message(user_message, "matched_skills", matched)
    _assert_skill_region_in_user_message(user_message, "missing_skills", missing)


@settings(max_examples=100, deadline=None)
@given(missing=_skills, jd_text=_free_text, bullets=_bullets)
def test_bullet_rewriter_prompt_grounded_in_jd_and_missing_skills(
    missing: list[str], jd_text: str, bullets: list[str]
) -> None:
    """Bullet_Rewriter grounding (Requirement 6.4): the assembled prompt's
    user-content regions carry the stored Job_Description context and the
    stored missing-skill list verbatim, plus every submitted bullet in
    submission order — never a recomputation."""
    match = _match(missing=missing, jd_text=jd_text)
    inputs = build_bullet_inputs(match, BulletRewriteInput(bullets=tuple(bullets)))
    sections = _sections_by_kind(inputs)

    # Stored missing skills pass through verbatim and unredacted (Req 6.4).
    missing_section = sections["missing_skills"]
    assert missing_section.redaction is None
    assert missing_section.text == _expected_skill_region(missing)
    for skill in missing:
        assert skill in missing_section.text

    # The Job_Description context is the Match_Result's stored text (Req 6.4).
    assert sections["job_description"].text == jd_text

    # Every submitted bullet appears verbatim, numbered, in submission order.
    bullet_section = sections["bullets"]
    assert bullet_section.text == "\n".join(
        f"{position}. {bullet}" for position, bullet in enumerate(bullets, start=1)
    )
    for bullet in bullets:
        assert bullet in bullet_section.text

    user_message = _assembled_user_message(inputs)
    _assert_skill_region_in_user_message(user_message, "missing_skills", missing)
