"""Feature: phase-1-matching — Property 8.

Property 8: Lexicon aliases are treated as the same keyword.

    *For any* Skill_Lexicon entry that defines at least one alias,
    substituting any alias for its canonical term (or vice-versa) in the
    job description and/or the resume — embedded in otherwise identical
    surrounding text — does not change the analysis outcome for that
    skill. A JD/resume that mentions a skill by an alias yields the same
    matched/missing classification for that skill's canonical term as one
    that mentions it by the canonical form.

**Validates: Requirements 6.2**

Requirement 6.2 says the Keyword_Analyzer normalizes terms by case-folding
and by applying the Skill_Lexicon's alias rules "so that a term and its
lexicon-defined alias are treated as the same keyword." The analyzer
realizes this by rewriting every surface form (canonical or alias) to the
canonical term, longest-match-first, before deriving the analyzed set and
partitioning it. This module asserts the observable consequence across a
wide, generated input space using Hypothesis (>= 100 examples).

The space is driven by sampling lexicon entries that *have* aliases
(entries with none are skipped — there is nothing to interchange) and, for
each, drawing an independent surface form for the JD mention and for the
resume mention, plus whether the resume mentions the skill at all. Each
generated encoding is compared against the all-canonical baseline encoding
of the same scenario.

Two complementary assertions encode the property:

* **Interchangeable classification.** The canonical term's
  matched/missing membership is identical whether the skill is written by
  its canonical form or by any alias, in the JD and/or the resume — and it
  is ``matched`` exactly when the resume mentions the skill (by any form)
  and ``missing`` otherwise. This is the direct reading of the property.

* **Indistinguishable analysis.** Because alias rewriting happens during
  normalization, an alias-encoded input and the canonical-encoded input
  produce byte-identical normalized text and therefore an identical whole
  ``KeywordAnalysis`` (same analyzed/matched/missing terms, same weights,
  same order). This is the strongest statement of "treated as the same
  keyword" and would catch any alias the normalizer fails to collapse.

The surrounding text is fixed, skill-free filler so the only semantic
difference between the baseline and the variant is the surface form of the
one skill under test; the skill token is always whitespace-delimited so it
is matched as a standalone term regardless of the alias's internal
punctuation (``c++``, ``ci/cd``, ``node.js``, ``problem-solving``).
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.keyword_analyzer import Keyword, Keyword_Analyzer, KeywordAnalysis
from matchlayer_api.scoring.lexicon import SkillEntry, load_lexicon

# A single shared, immutable analyzer over the committed lexicon. The cap
# mirrors the production default (``MATCHLAYER_MATCH_MAX_KEYWORDS``); the
# generated documents are short, so the analyzed set is always far below it
# and the canonical term under test is never evicted by the cap.
_MAX_KEYWORDS = 50
_LEXICON = load_lexicon()
ANALYZER = Keyword_Analyzer(_LEXICON, max_keywords=_MAX_KEYWORDS)

# Only entries that actually define aliases are interchangeable; an entry
# with no aliases has a single surface form and nothing to substitute.
ENTRIES_WITH_ALIASES: list[SkillEntry] = [e for e in _LEXICON.entries if e.aliases]

# Skill-free filler. None of these words is a canonical term or alias in the
# lexicon, so the only lexicon skill present in either document is the one
# injected at ``{skill}`` — making the matched/missing verdict unambiguous.
# ``{skill}`` is flanked by spaces so the injected term is a standalone,
# boundary-delimited token whatever punctuation it carries.
_JD_TEMPLATE = "We are hiring. The role needs {skill} among other duties."
_RESUME_PRESENT_TEMPLATE = "I have used {skill} across several past projects."
_RESUME_ABSENT = "I enjoy building reliable products with curious colleagues."


def _job_description(form: str) -> str:
    """A job description mentioning the skill by surface ``form``."""
    return _JD_TEMPLATE.format(skill=form)


def _resume(form: str | None) -> str:
    """A resume mentioning the skill by ``form``, or skill-free when ``None``."""
    if form is None:
        return _RESUME_ABSENT
    return _RESUME_PRESENT_TEMPLATE.format(skill=form)


def _classify(analysis: KeywordAnalysis, canonical: str) -> str:
    """Report the canonical term's membership: matched | missing | absent.

    ``absent`` means the term is in neither partition (i.e. not in the
    analyzed set at all). For these scenarios the JD always mentions the
    skill, so a correct analyzer never returns ``absent``; asserting against
    it guards the property's precondition as well as its conclusion.
    """
    if any(k.term == canonical for k in analysis.matched):
        return "matched"
    if any(k.term == canonical for k in analysis.missing):
        return "missing"
    return "absent"


def _terms(keywords: list[Keyword]) -> list[tuple[str, float]]:
    """The ordered ``(term, weight)`` view of a keyword list for comparison."""
    return [(k.term, k.weight) for k in keywords]


@st.composite
def _alias_scenarios(draw: st.DrawFn) -> tuple[SkillEntry, str, bool, str | None]:
    """Draw an entry-with-aliases plus independent JD/resume surface forms.

    Returns ``(entry, jd_form, present, resume_form)`` where ``jd_form`` and
    ``resume_form`` are each either the canonical term or one of its
    aliases, and ``present`` decides whether the resume mentions the skill
    at all (``resume_form`` is ``None`` when it does not). Drawing the two
    forms independently covers alias-in-JD-only, alias-in-resume-only,
    alias-in-both, and canonical-in-both.
    """
    entry = draw(st.sampled_from(ENTRIES_WITH_ALIASES))
    forms = (entry.canonical, *entry.aliases)
    jd_form = draw(st.sampled_from(forms))
    present = draw(st.booleans())
    resume_form = draw(st.sampled_from(forms)) if present else None
    return entry, jd_form, present, resume_form


@settings(max_examples=200, deadline=None)
@given(scenario=_alias_scenarios())
def test_alias_is_interchangeable_with_canonical(
    scenario: tuple[SkillEntry, str, bool, str | None],
) -> None:
    """An alias yields the same matched/missing verdict as the canonical form.

    Property 8 (Requirement 6.2): for the skill under test, the canonical
    term's classification under an arbitrary alias encoding equals its
    classification under the all-canonical baseline encoding, and equals the
    semantically correct verdict — ``matched`` when the resume mentions the
    skill by any form, ``missing`` when it does not.
    """
    entry, jd_form, present, resume_form = scenario
    canonical = entry.canonical

    baseline = _classify(
        ANALYZER.analyze(_resume(canonical if present else None), _job_description(canonical)),
        canonical,
    )
    variant = _classify(
        ANALYZER.analyze(_resume(resume_form), _job_description(jd_form)),
        canonical,
    )

    expected = "matched" if present else "missing"
    assert baseline == expected, (canonical, present, baseline)
    assert variant == baseline, (canonical, jd_form, resume_form, variant, baseline)


@settings(max_examples=200, deadline=None)
@given(scenario=_alias_scenarios())
def test_alias_and_canonical_yield_identical_analysis(
    scenario: tuple[SkillEntry, str, bool, str | None],
) -> None:
    """An alias encoding and the canonical encoding analyze identically.

    The strongest reading of Property 8: because alias rewriting happens
    during normalization, swapping a canonical term for one of its aliases
    (in the JD, the resume, or both) leaves the entire ``KeywordAnalysis``
    unchanged — identical analyzed/matched/missing terms, weights, and
    ordering — so the two surface forms are genuinely the same keyword.
    """
    entry, jd_form, present, resume_form = scenario
    canonical = entry.canonical

    baseline = ANALYZER.analyze(
        _resume(canonical if present else None), _job_description(canonical)
    )
    variant = ANALYZER.analyze(_resume(resume_form), _job_description(jd_form))

    assert _terms(variant.analyzed) == _terms(baseline.analyzed)
    assert _terms(variant.matched) == _terms(baseline.matched)
    assert _terms(variant.missing) == _terms(baseline.missing)
