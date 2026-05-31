"""Feature: phase-1-matching — Property 7.

Property 7: Every matched keyword is present in the resume.

    *For any* resume text and *any* job-description text, every term in
    ``matched_keywords`` is verifiably present in the normalized resume
    text.

**Validates: Requirements 6.5**

This is the universal companion to the concrete-example coverage of the
Keyword_Analyzer. Where unit examples pin down specific matched verdicts
for hand-picked pairs, this module asserts the *soundness* half of the
matched/missing partition across a wide, generated input space using
Hypothesis (>=100 examples): the analyzer never reports a keyword as
matched unless it is genuinely there.

Soundness, not completeness. The single claim under test is the one-way
implication ``matched ⟹ present``. Whether every present term is *also*
discovered (completeness) is a separate concern and is deliberately **not**
asserted here — over-asserting completeness would conflate this property
with Property 6's partition and Property 8's alias handling and would also
false-fail on the legitimate ``MATCHLAYER_MATCH_MAX_KEYWORDS`` cap (a
present skill can be evicted by the cap and so never appear in ``matched``
at all).

The independent presence oracle. Requirement 6.5 anchors soundness to the
*normalized* resume text — the analyzer case-folds + whitespace-collapses
the resume, rewrites every lexicon surface form (canonical *or* alias) to
its canonical term, and then tests a matched term with a boundary-delimited
regex (``[a-z0-9]`` flanks, not ``\\b``, because skill terms carry
punctuation — ``c++``, ``ci/cd``, ``node.js``, ``.net``). To cross-check
that verdict, this module reconstructs the normalized resume text and the
boundary regex **independently, from the Skill_Lexicon's public API** (its
``entries``: canonical forms and aliases), never importing or calling the
analyzer's private normalization or matching code. The oracle then asserts
that each matched term occurs boundary-delimited in that independently
reconstructed normalized resume.

Why the normalization (not just a collapsed-resume check) is required.
Consider the falsifying-looking pair ``resume="aspnet"``, ``jd=".net"``.
``aspnet`` is a lexicon alias of ``.net``, so normalization rewrites the
resume to ``.net``; the analyzed set also contains the TF-IDF tokenization
artifact ``net`` (the vectorizer strips the leading ``.``). The term ``net``
is then boundary-delimited present in the normalized resume ``.net`` (``.``
is a non-alphanumeric boundary) and is correctly matched. This is sound per
Requirement 6.5 — ``net`` *is* present in the normalized resume — even
though ``net`` never appears as a standalone surface form in the raw input.
An oracle that only inspected the collapsed resume (``aspnet``) would
wrongly flag this correct behavior, so the oracle must reproduce the same
normalization the requirement names. A term reported as matched that is
genuinely *absent* from the normalized resume would be a hallucinated match
— a real Requirement 6.5 defect — and fails the test.

The reconstruction is independent (built from the lexicon's published
entries rather than the analyzer's internals), so it still catches a
regression in the analyzer: a substring-instead-of-boundary match, a
forgotten resume normalization, or a term routed to ``matched`` without
being present would all break the assertion.

Inputs are arbitrary text drawn from a deliberately mixed vocabulary — real
Skill_Lexicon surface forms (canonical terms and every alias), generic
non-stop-word filler so scikit-learn's TF-IDF yields a non-empty vocabulary,
and free random tokens — joined into resume and job-description documents.
Seeding both documents from the shared skill vocabulary makes ``matched``
frequently non-empty, so the soundness claim is exercised under real
pressure rather than only on inputs that match nothing. Genuinely arbitrary
text (unicode, punctuation, odd whitespace) is also thrown at the analyzer so
the claim is not limited to tidy ASCII input; the empty / whitespace-only
job description (which yields an empty analysis) satisfies the property
vacuously.

The Keyword_Analyzer is framework-free (Requirement 10.1): this test
constructs it directly from the committed Skill_Lexicon and an injected
``max_keywords`` cap, never touching settings, FastAPI, or the database.
"""

from __future__ import annotations

import re
from string import ascii_lowercase, digits
from typing import Final

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.keyword_analyzer import Keyword, Keyword_Analyzer
from matchlayer_api.scoring.lexicon import Skill_Lexicon, load_lexicon

# ---------------------------------------------------------------------------
# Analyzer under test
# ---------------------------------------------------------------------------

# One shared analyzer, built from the real committed lexicon. The cap mirrors
# the ``MATCHLAYER_MATCH_MAX_KEYWORDS`` default (50); soundness is independent
# of the cap's value (the cap can only ever *remove* analyzed terms), but the
# production default keeps the analyzed set realistically sized. The analyzer
# holds only immutable state (lexicon, cap, precompiled regexes) and is safe to
# reuse across examples.
_LEXICON: Final[Skill_Lexicon] = load_lexicon()
_MAX_KEYWORDS: Final[int] = 50
_ANALYZER: Final[Keyword_Analyzer] = Keyword_Analyzer(_LEXICON, max_keywords=_MAX_KEYWORDS)

# ---------------------------------------------------------------------------
# Independent presence oracle
# ---------------------------------------------------------------------------

# Boundary semantics identical to the analyzer's: a term is "present" only when
# flanked by non-alphanumeric characters (or the string edges), so ``java``
# matches in ``"java developer"`` but not inside ``"javascript"``. ``[a-z0-9]``
# (not ``\b``) is used because skill terms themselves contain non-word
# characters (``c++``, ``ci/cd``, ``.net``, ``node.js``). The oracle's regex and
# its normalization are defined here, independently of the analyzer's internals,
# so they genuinely cross-check the analyzer's verdict rather than echo it.
_BOUNDARY_BEFORE: Final[str] = r"(?<![a-z0-9])"
_BOUNDARY_AFTER: Final[str] = r"(?![a-z0-9])"


def _collapse(text: str) -> str:
    """Case-fold ``text`` and collapse all whitespace runs to single spaces."""
    return " ".join(text.casefold().split())


# Surface-form → canonical map rebuilt from the lexicon's *public* entries
# (canonical forms map to themselves; each alias maps to its canonical). This is
# an independent reconstruction of the substitution the analyzer applies, built
# without importing or calling the analyzer's private normalization code.
_SURFACE_TO_CANONICAL: Final[dict[str, str]] = {}
for _entry in _LEXICON.entries:
    _SURFACE_TO_CANONICAL.setdefault(_entry.canonical, _entry.canonical)
    for _alias in _entry.aliases:
        _SURFACE_TO_CANONICAL.setdefault(_alias, _entry.canonical)

# Longest surface form first so a multi-word / longer canonical (``node.js``) is
# preferred over a shorter inner alias (``js``); ``-len`` then the term itself
# keeps the alternation deterministic. Mirrors the analyzer's longest-match-first
# rule, reconstructed independently from the public surface forms.
_ALIAS_PATTERN: Final[re.Pattern[str] | None] = (
    re.compile(
        _BOUNDARY_BEFORE
        + "(?:"
        + "|".join(
            re.escape(surface)
            for surface in sorted(_SURFACE_TO_CANONICAL, key=lambda s: (-len(s), s))
        )
        + ")"
        + _BOUNDARY_AFTER
    )
    if _SURFACE_TO_CANONICAL
    else None
)


def _normalized_resume(resume_text: str) -> str:
    """Reconstruct the analyzer's normalized resume text independently.

    Case-fold + whitespace-collapse, then rewrite every boundary-delimited
    lexicon surface form (canonical or alias) to its canonical term,
    longest-match-first — the same normalization Requirement 6.5 names, but
    built here from the lexicon's published ``entries`` rather than from the
    analyzer's private code, so it serves as a genuine cross-check.
    """
    collapsed = _collapse(resume_text)
    if not collapsed or _ALIAS_PATTERN is None:
        return collapsed
    return _ALIAS_PATTERN.sub(lambda m: _SURFACE_TO_CANONICAL[m.group(0)], collapsed)


def _occurs(term: str, normalized_text: str) -> bool:
    """True if ``term`` occurs boundary-delimited in ``normalized_text``."""
    pattern = re.compile(_BOUNDARY_BEFORE + re.escape(term) + _BOUNDARY_AFTER)
    return pattern.search(normalized_text) is not None


# ---------------------------------------------------------------------------
# Vocabulary for generated text
# ---------------------------------------------------------------------------

# Real surface forms the lexicon knows: every canonical term plus all of its
# aliases. Feeding these into the generated documents guarantees ``matched`` is
# frequently non-empty so the soundness claim is exercised with real pressure.
_LEXICON_SURFACES: Final[list[str]] = sorted(
    {surface for entry in _LEXICON.entries for surface in (entry.canonical, *entry.aliases)}
)

# Generic, non-stop-word filler. scikit-learn's TfidfVectorizer strips English
# stop words, so this pool ensures a JD made of filler still yields TF-IDF terms
# (source (b) of the analyzed set) rather than an empty vocabulary.
_GENERIC_WORDS: Final[list[str]] = [
    "developer",
    "engineer",
    "experience",
    "senior",
    "team",
    "build",
    "design",
    "systems",
    "cloud",
    "data",
    "platform",
    "scalable",
    "production",
    "knowledge",
    "required",
    "preferred",
    "years",
    "strong",
    "pipeline",
    "services",
]

# A single generated token: a known lexicon surface form, a generic filler
# word, or a free random alphanumeric token (which may coincide with neither).
_token = st.one_of(
    st.sampled_from(_LEXICON_SURFACES),
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10),
)

# A document built from tokens joined by single spaces. ``min_size=0`` admits
# the empty document, exercising the empty-analysis branch which must satisfy
# the property vacuously.
_token_text = st.lists(_token, min_size=0, max_size=40).map(" ".join)

# Also throw genuinely arbitrary text (unicode, punctuation, odd whitespace) at
# the analyzer so the soundness claim is not limited to tidy ASCII input.
_arbitrary_text = st.text(min_size=0, max_size=200)

_document = st.one_of(_token_text, _arbitrary_text)


@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
@example(resume_text="", job_description="")
@example(
    # Alias in the resume, canonical in the JD: ``python`` must still be
    # reported present (the normalized resume rewrites ``py`` → ``python``).
    resume_text="I write py every day.",
    job_description="We need python and sql experience.",
)
@example(
    # Substring trap: a resume that says ``javascript`` must NOT let ``java``
    # be reported as matched. If it were, the oracle (``java`` is not
    # boundary-delimited inside ``javascript``) would catch the unsound match.
    resume_text="expert in javascript and typescript",
    job_description="java javascript typescript developer",
)
@example(
    # Punctuation-bearing canonicals exercised against a resume that contains
    # them verbatim.
    resume_text="strong c++, ci/cd and node.js background",
    job_description="c++ ci/cd node.js engineer",
)
@example(
    # Regression for the oracle: ``aspnet`` is an alias of ``.net`` so the
    # normalized resume becomes ``.net``; the TF-IDF artifact term ``net`` is
    # then boundary-delimited present in ``.net`` and is soundly matched. The
    # oracle must normalize the resume (not just collapse it) to agree.
    resume_text="aspnet",
    job_description=".net",
)
def test_every_matched_keyword_is_present_in_the_resume(
    resume_text: str, job_description: str
) -> None:
    """matched ⟹ present: no matched keyword is invented.

    Property 7 (Requirement 6.5): for arbitrary resume and job-description
    text, every term the Keyword_Analyzer reports in ``matched`` is
    boundary-delimited present in the *normalized* resume text — the resume
    case-folded, whitespace-collapsed, and alias-rewritten to canonical
    forms. Completeness (whether every present term is found) is intentionally
    not asserted.
    """
    normalized_resume = _normalized_resume(resume_text)

    analysis = _ANALYZER.analyze(resume_text, job_description)

    for keyword in analysis.matched:
        assert isinstance(keyword, Keyword)
        assert _occurs(keyword.term, normalized_resume), (
            "matched keyword not present in normalized resume",
            keyword.term,
            normalized_resume,
        )
