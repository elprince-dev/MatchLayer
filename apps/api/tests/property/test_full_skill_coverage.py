"""Feature: phase-4-agentic — Property 8.

Property 8: Full skill coverage yields an empty, non-degraded report.

    *For any* inputs in which every JD skill is present in the
    Candidate_Profile skills or the Match_Result matched skills, the
    Skill_Gap_Agent produces a schema-valid report with an empty gap list
    and ``degraded=False``.

**Validates: Requirements 5.7**

The rules under test are the pure free functions in
:mod:`matchlayer_api.ml.agents.gap_rules` (design section ``SkillGapAgent``),
composed by :func:`build_skill_gap_entries`. Requirement 5.7 demands that
when every Job_Description skill is covered, the Skill_Gap_Agent produces a
schema-valid ``SkillGapReport`` with an empty gap list — and that the empty
list is a valid outcome, never a failure or a Degraded_Output trigger.

The generator constructs the covered precondition directly: it draws a pool
of unique skill names, assigns each to the profile set, the matched set, or
both (so every pool skill satisfies the property's union condition), and
then draws the JD skill list — duplicates included — exclusively from that
pool. Extra profile/matched skills that never appear in the JD are also
drawn, since coverage of the JD must not depend on the resume having *only*
JD skills. The gap-rules module is framework-free: this test imports it
directly and touches no settings, FastAPI, or database.
"""

# Feature: phase-4-agentic, Property 8: Full skill coverage yields an empty, non-degraded report  # noqa: E501

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.ml.agents.gap_rules import build_skill_gap_entries
from matchlayer_api.ml.agents.state import SkillGapReport

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Canonical-looking skill names: membership in the rules is exact string
# membership over Phase 2 Skill_Lexicon names, so a compact ASCII alphabet
# (with the separators real lexicon entries use) exercises the logic fully.
_skill = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+#.- ",
    min_size=1,
    max_size=20,
)

# (jd_skills, profile_skills, matched_skills) triples in which every JD
# skill is present in profile_skills or matched_skills — the property's
# "full coverage" precondition, built by construction.
CoveredInputs = tuple[list[str], list[str], list[str]]


@st.composite
def _covered_inputs(draw: st.DrawFn) -> CoveredInputs:
    pool = draw(st.lists(_skill, min_size=0, max_size=12, unique=True))

    profile_skills: list[str] = []
    matched_skills: list[str] = []
    for skill in pool:
        membership = draw(st.sampled_from(("profile", "matched", "both")))
        if membership in ("profile", "both"):
            profile_skills.append(skill)
        if membership in ("matched", "both"):
            matched_skills.append(skill)

    # JD skills come only from the covered pool; duplicates model repeated
    # mentions in the Job_Description (they feed prioritization counts).
    jd_skills: list[str] = (
        draw(st.lists(st.sampled_from(pool), min_size=0, max_size=25)) if pool else []
    )

    # Skills the candidate has beyond the JD must not affect coverage.
    extras = draw(
        st.lists(_skill, max_size=4, unique=True).map(
            lambda names: [n for n in names if n not in pool]
        )
    )
    for skill in extras:
        if draw(st.booleans()):
            profile_skills.append(skill)
        else:
            matched_skills.append(skill)

    return jd_skills, profile_skills, matched_skills


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 8: Full skill coverage yields an empty, non-degraded report  # noqa: E501
@settings(max_examples=200, deadline=None)
@example(inputs=([], [], []))
@example(inputs=(["python"], [], ["python"]))
@example(inputs=(["python", "python", "sql"], ["sql"], ["python", "sql"]))
@example(inputs=(["python"], ["python"], []))
@given(inputs=_covered_inputs())
def test_full_coverage_yields_empty_gap_list(inputs: CoveredInputs) -> None:
    """Full coverage → empty gap list (Requirement 5.7): when every JD skill
    is present in the Candidate_Profile skills or the Match_Result matched
    skills, the rules identify no gap."""
    jd_skills, profile_skills, matched_skills = inputs

    entries = build_skill_gap_entries(jd_skills, profile_skills, matched_skills)

    assert entries == []


# Feature: phase-4-agentic, Property 8: Full skill coverage yields an empty, non-degraded report  # noqa: E501
@settings(max_examples=200, deadline=None)
@example(inputs=([], [], []))
@example(inputs=(["python"], [], ["python"]))
@given(inputs=_covered_inputs())
def test_empty_report_is_schema_valid_and_non_degraded(inputs: CoveredInputs) -> None:
    """Empty is valid, never a degradation trigger (Requirement 5.7): the
    report built from a fully covered input is a schema-valid
    ``SkillGapReport`` that round-trips validation with ``degraded=False``
    and no derived-from-degraded-input marker."""
    jd_skills, profile_skills, matched_skills = inputs

    report = SkillGapReport(gaps=build_skill_gap_entries(jd_skills, profile_skills, matched_skills))
    revalidated = SkillGapReport.model_validate(report.model_dump())

    assert revalidated == report
    assert report.degraded is False
    assert report.derived_from_degraded_input is False
