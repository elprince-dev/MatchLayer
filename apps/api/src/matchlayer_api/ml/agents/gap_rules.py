"""Skill-gap classification and prioritization rules (phase-4-agentic).

The Skill_Gap_Agent's rules, implemented as pure free functions so they stay
property-testable independently of the agent class (design Properties 7 and 8)
and reproducible from persisted trace data alone. The human-readable statement
of both rules lives in ``docs/agent-rules.md``. Design reference: the
``SkillGapAgent`` section of the phase-4-agentic design. Requirements covered:
5.1, 5.2, 5.3.

**Classification rule** (Requirement 5.1, as amended) — for each skill
extracted from the Job_Description:

* present in ``profile_skills`` **or** ``matched_skills`` → covered; produces
  **no** entry;
* absent from both sets → ``missing``.

Profile-presence counts as coverage, which keeps this rule consistent with
the full-coverage guarantee of Requirement 5.7 (every JD skill present in the
profile or matched set → empty gap list). The ``weak`` value remains valid in
the ``SkillGapEntry`` schema for stability, but the classification rule no
longer emits it.

**Prioritization rule** (Requirement 5.2) — order gap entries by:

1. ``missing`` before ``weak`` (retained for schema/ordering stability even
   though the classification rule no longer produces ``weak`` entries);
2. descending occurrence count of the skill in the JD skill list;
3. case-insensitive alphabetical tie-break (with a final exact-string
   comparison so ordering stays total and deterministic even for skills that
   differ only in case).

Ranks are assigned 1..n after ordering — sequential, unique, ascending — and
each skill name appears at most once in the result (Requirement 5.2).

Both functions are total and deterministic: no randomness, no clocks, no I/O
— field-for-field identical inputs always yield field-for-field identical
outputs, including ordering and ranks (Requirement 5.3). An empty result is a
valid outcome (full coverage), never an error.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal

from matchlayer_api.ml.agents.state import SkillGapEntry

__all__ = [
    "GapClassification",
    "build_skill_gap_entries",
    "classify_skill",
    "prioritize_gaps",
]

GapClassification = Literal["missing", "weak"]


def classify_skill(
    skill: str,
    profile_skills: frozenset[str] | set[str],
    matched_skills: frozenset[str] | set[str],
) -> GapClassification | None:
    """Classify one JD skill against the profile and matched skill sets.

    Pure and deterministic (Requirement 5.3). Membership is exact string
    membership — skill names come from the Phase 2 Skill_Lexicon, which is
    already canonical.

    Amended rule: presence in either set counts as coverage, so this function
    returns only ``None`` (covered) or ``"missing"``. ``"weak"`` stays in the
    :data:`GapClassification` Literal for schema stability but is never
    returned here.

    Args:
        skill: One skill extracted from the Job_Description.
        profile_skills: Skills listed in the Candidate_Profile.
        matched_skills: Skills the scorer credited in the Match_Result.

    Returns:
        ``None`` when the skill is covered (present in ``profile_skills`` or
        ``matched_skills``) and produces no gap entry, or ``"missing"`` when
        it is in neither set.
    """
    if skill in matched_skills or skill in profile_skills:
        return None
    return "missing"


def prioritize_gaps(
    classified: Iterable[tuple[str, GapClassification]],
    jd_skills: Sequence[str],
) -> list[SkillGapEntry]:
    """Order classified gaps by the prioritization rule and assign ranks.

    Ordering (Requirement 5.2): ``missing`` before ``weak``, then descending
    occurrence count of the skill in ``jd_skills``, then case-insensitive
    alphabetical, then exact string (a final total-order guarantee for names
    differing only in case). Ranks are 1-based, sequential, and unique;
    entries are returned in ascending rank order.

    Args:
        classified: ``(skill, classification)`` pairs; each skill name is
            expected to appear at most once (as produced by
            :func:`build_skill_gap_entries`).
        jd_skills: The JD skill list as extracted, duplicates included —
            occurrence counts drive the second ordering criterion.

    Returns:
        Gap entries ordered by ascending rank with ranks ``1..n``.
    """
    occurrences = Counter(jd_skills)

    def sort_key(item: tuple[str, GapClassification]) -> tuple[int, int, str, str]:
        skill, classification = item
        return (
            0 if classification == "missing" else 1,
            -occurrences[skill],
            skill.casefold(),
            skill,
        )

    ordered = sorted(classified, key=sort_key)
    return [
        SkillGapEntry(skill=skill, classification=classification, rank=rank)
        for rank, (skill, classification) in enumerate(ordered, start=1)
    ]


def build_skill_gap_entries(
    jd_skills: Sequence[str],
    profile_skills: Iterable[str],
    matched_skills: Iterable[str],
) -> list[SkillGapEntry]:
    """Classify every JD skill and return the prioritized, ranked gap list.

    The composition of :func:`classify_skill` and :func:`prioritize_gaps`:
    duplicate JD skills are classified once (each skill name appears at most
    once in the result, Requirement 5.2) while their occurrence count still
    feeds prioritization. An empty result means full coverage — a valid
    outcome, never a degradation trigger (Requirement 5.7).

    Args:
        jd_skills: Skills extracted from the Job_Description by the Phase 2
            Skill_Extractor, duplicates included.
        profile_skills: Skills listed in the Candidate_Profile.
        matched_skills: The Match_Result's persisted matched skills.

    Returns:
        Gap entries ordered by ascending rank with ranks ``1..n``; empty when
        every JD skill is covered.
    """
    profile_set = frozenset(profile_skills)
    matched_set = frozenset(matched_skills)

    classified: dict[str, GapClassification] = {}
    for skill in jd_skills:
        if skill in classified:
            continue
        classification = classify_skill(skill, profile_set, matched_set)
        if classification is not None:
            classified[skill] = classification

    return prioritize_gaps(classified.items(), jd_skills)
