"""Feature: phase-4-agentic — Property 7.

Property 7: Skill_Gap_Report well-formedness.

    *For any* Candidate_Profile skill set (normal or degraded-empty),
    Match_Result skill snapshot, and JD skill list, the Skill_Gap_Report has
    every entry classified exactly ``missing`` or ``weak`` per the documented
    rule, ranks sequential from 1 with no duplicates, entries ordered by
    ascending rank, and each skill name appearing at most once.

**Validates: Requirements 5.2, 5.4**

The rules under test are the pure free functions in
:mod:`matchlayer_api.ml.agents.gap_rules` — ``classify_skill``,
``prioritize_gaps``, and their composition ``build_skill_gap_entries``.
Requirement 5.2 demands the entry shape (skill name, exactly
``missing``/``weak``, integer ranks starting at 1, sequential and unique,
each skill at most once) and the prioritization order (``missing`` before
``weak``, descending JD occurrence count, case-insensitive alphabetical
tie-break). Under the amended classification rule (Requirement 5.1),
profile-presence counts as coverage, so the classification rule emits only
``missing`` entries; ``weak`` remains schema-valid and the ordering rule is
retained for stability. Requirement 5.4 demands that the same
well-formedness bar holds
when the report is derived from the Match_Result's persisted skill analysis
alone (the degraded-profile path) — exercised here by generating runs where
the profile skill set is empty.

The skill generator is collision-aware: it mixes a fixed pool of names —
including case variants like ``python``/``Python``/``PYTHON`` — with short
low-alphabet text, so duplicate JD occurrences, profile/matched overlaps, and
the case-insensitive tie-break are all exercised on every run. The gap_rules
module is framework-free: this test imports it directly and touches no
settings, FastAPI, or database.
"""

# Feature: phase-4-agentic, Property 7: Skill_Gap_Report well-formedness

from __future__ import annotations

from collections import Counter

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.ml.agents.gap_rules import (
    build_skill_gap_entries,
    classify_skill,
)
from matchlayer_api.ml.agents.state import SkillGapReport

_VALID_CLASSIFICATIONS = frozenset({"missing", "weak"})

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# A small, collision-prone universe of skill names. Case variants force the
# case-insensitive alphabetical tie-break (and its exact-string total-order
# fallback); the low-alphabet text arm adds arbitrary-but-overlapping names.
_SKILL_POOL = [
    "python",
    "Python",
    "PYTHON",
    "aws",
    "AWS",
    "sql",
    "SQL",
    "react",
    "docker",
    "kubernetes",
    "go",
    "Go",
    "c++",
    "typescript",
]

_skill = st.one_of(
    st.sampled_from(_SKILL_POOL),
    st.text(alphabet="abcABC+# ", min_size=1, max_size=8),
)


@st.composite
def _gap_inputs(draw: st.DrawFn) -> tuple[list[str], list[str], list[str]]:
    """Generate ``(jd_skills, profile_skills, matched_skills)``.

    Profile and matched skills are biased toward overlapping the JD list (so
    covered classifications actually occur) with extra out-of-JD names mixed
    in. An empty profile list models the degraded
    derive-from-Match_Result-alone path of Requirement 5.4.
    """
    jd_skills = draw(st.lists(_skill, max_size=25))
    from_jd = st.lists(st.sampled_from(jd_skills), max_size=25) if jd_skills else st.just([])
    profile_skills = draw(from_jd) + draw(st.lists(_skill, max_size=8))
    matched_skills = draw(from_jd) + draw(st.lists(_skill, max_size=8))
    return jd_skills, profile_skills, matched_skills


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


# Feature: phase-4-agentic, Property 7: Skill_Gap_Report well-formedness
@settings(max_examples=200, deadline=None)
@example(inputs=([], [], []))
@example(inputs=(["python", "sql"], [], []))  # degraded path: empty profile (Req 5.4)
@example(inputs=(["python", "Python", "sql"], ["sql"], ["Python"]))
@given(inputs=_gap_inputs())
def test_entries_are_wellformed(
    inputs: tuple[list[str], list[str], list[str]],
) -> None:
    """Entry shape (Requirement 5.2): every gap carries a classification of
    exactly ``missing`` or ``weak`` agreeing with the documented
    classification rule, ranks are 1..n sequential/unique/ascending, each
    skill name appears at most once, and the entries assemble into a
    schema-valid Skill_Gap_Report."""
    jd_skills, profile_skills, matched_skills = inputs

    entries = build_skill_gap_entries(jd_skills, profile_skills, matched_skills)
    report = SkillGapReport(gaps=entries)  # schema-validated container

    # Ranks: 1..n, sequential, unique, ascending — entries in rank order.
    assert [entry.rank for entry in report.gaps] == list(range(1, len(report.gaps) + 1))

    # Each skill name appears at most once.
    skills = [entry.skill for entry in report.gaps]
    assert len(skills) == len(set(skills))

    # Every entry is a JD skill classified per the documented rule; covered
    # skills produce no entry.
    profile_set = frozenset(profile_skills)
    matched_set = frozenset(matched_skills)
    for entry in report.gaps:
        assert entry.classification in _VALID_CLASSIFICATIONS
        assert entry.skill in jd_skills
        assert entry.classification == classify_skill(entry.skill, profile_set, matched_set)
    for skill in jd_skills:
        if classify_skill(skill, profile_set, matched_set) is not None:
            assert skill in set(skills)
        else:
            assert skill not in set(skills)


# Feature: phase-4-agentic, Property 7: Skill_Gap_Report well-formedness
@settings(max_examples=200, deadline=None)
@example(inputs=(["python", "Python", "sql", "sql"], ["sql"], []))
@example(inputs=(["b", "a", "B", "A"], [], []))
@given(inputs=_gap_inputs())
def test_ordering_follows_prioritization_rule(
    inputs: tuple[list[str], list[str], list[str]],
) -> None:
    """Prioritization order (Requirements 5.2, 5.4): entries are ordered
    ``missing`` before ``weak``, then descending JD occurrence count, then
    case-insensitive alphabetical (exact-string final tie-break) — i.e. the
    documented sort key is strictly increasing across the ranked entries,
    including on the degraded empty-profile path."""
    jd_skills, profile_skills, matched_skills = inputs

    entries = build_skill_gap_entries(jd_skills, profile_skills, matched_skills)
    occurrences = Counter(jd_skills)

    keys = [
        (
            0 if entry.classification == "missing" else 1,
            -occurrences[entry.skill],
            entry.skill.casefold(),
            entry.skill,
        )
        for entry in entries
    ]
    assert keys == sorted(keys)
    # Skill uniqueness makes the key's final component distinct, so the
    # order is strict — no two entries share a key.
    assert len(keys) == len(set(keys))
