"""Feature: phase-2-nlp-embeddings — Property 8.

Property 8: Alias surface forms resolve to canonical terms identically for
both text roles.

    *For any* lexicon alias embedded at token boundaries in a text under
    any letter casing, the Skill_Extractor yields the alias's canonical
    term, and the resolution behaves identically whether the text is
    presented as job-description text or resume text.

**Validates: Requirements 4.3**

Requirement 4.3 says the Skill_Extractor normalizes skill mentions by
case-folding and Skill_Lexicon alias resolution, applied identically to
Job_Description text and resume text, so every surface form of a skill
(for example "py", "node js") resolves to its canonical term. This module
asserts both halves across a generated input space:

* **Alias resolution under arbitrary casing.** For any lexicon entry, any
  of its surface forms (canonical or alias), any per-character letter
  casing of that form, and any skill-free surrounding filler, the cased
  surface embedded at token boundaries makes ``extract(text)`` yield the
  entry's *canonical* term — never the raw alias.
* **Role symmetry.** For one skill expressed via one surface form in a
  "resume" text and via an independently drawn (possibly different)
  surface form in a "job description" text, ``analyze()`` classifies the
  canonical term as ``matched`` — and it stays ``matched`` when the two
  texts swap roles. "py" in the resume matches "python" in the JD and
  vice versa: alias resolution is symmetric across text roles.

The surrounding filler vocabulary is constructed to be disjoint from every
token fragment of every lexicon surface form, so the only skill mention in
a generated text is the embedded surface form — filler can neither create
a second mention nor extend the embedded form into a longer lexicon match.
The embedded form is whitespace-delimited, so matching occurs at token
boundaries regardless of the form's internal punctuation ("node.js",
"ci/cd", "c++").

Per the design's PBT strategy, the extractor is built once at module scope
over a small synthetic lexicon (constructing a spaCy ``PhraseMatcher`` per
Hypothesis example would dominate the runtime budget) and the pipeline is
``spacy.blank("en")`` — tokenizer-only, no model artifact. A blank pipeline
assigns no POS, so the POS gate passes candidates through and alias
resolution — the behavior under test — is exercised in isolation.

The Skill_Extractor is framework-free (Requirement 12.1): this test
constructs it directly from a synthetic lexicon document and an injected
``max_keywords`` cap, never touching settings, FastAPI, or the database.
"""

from __future__ import annotations

from string import ascii_lowercase, digits
from typing import Final

import spacy
from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, SkillEntry
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Synthetic lexicon and module-scoped extractor
# ---------------------------------------------------------------------------

# Every entry defines at least one alias (there is nothing to interchange
# otherwise). The set covers single-token aliases ("py", "js"), a
# multi-token alias ("node js"), punctuation-bearing canonicals ("node.js",
# "ci/cd", "c++"), a multi-token canonical ("machine learning"), and an
# alias that is a sub-token of another entry's canonical ("js" inside
# "node js") so longest-match resolution participates.
_LEXICON: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop8",
        "skills": [
            {
                "canonical": "python",
                "display": "Python",
                "category": "language",
                "weight": 1.0,
                "aliases": ["py", "py3"],
            },
            {
                "canonical": "javascript",
                "display": "JavaScript",
                "category": "language",
                "weight": 0.9,
                "aliases": ["js"],
            },
            {
                "canonical": "node.js",
                "display": "Node.js",
                "category": "runtime",
                "weight": 0.9,
                "aliases": ["node js", "nodejs"],
            },
            {
                "canonical": "machine learning",
                "display": "Machine Learning",
                "category": "domain",
                "weight": 0.7,
                "aliases": ["ml"],
            },
            {
                "canonical": "ci/cd",
                "display": "CI/CD",
                "category": "practice",
                "weight": 0.8,
                "aliases": ["cicd"],
            },
            {
                "canonical": "c++",
                "display": "C++",
                "category": "language",
                "weight": 0.8,
                "aliases": ["cpp"],
            },
            {
                "canonical": "go",
                "display": "Go",
                "category": "language",
                "weight": 0.6,
                "aliases": ["golang"],
            },
        ],
    }
)

# Built once at module scope; instances hold only immutable state (pipeline,
# lexicon, prebuilt matcher) and are safe to share across examples. The cap
# (50) exceeds the lexicon size so capping never hides a resolution result.
_EXTRACTOR: Final[Skill_Extractor] = Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50)

_ENTRIES: Final[tuple[SkillEntry, ...]] = tuple(_LEXICON.entries)


def _surface_forms(entry: SkillEntry) -> tuple[str, ...]:
    """Every surface form of ``entry``: the canonical term plus aliases."""
    return (entry.canonical, *entry.aliases)


# ---------------------------------------------------------------------------
# Filler vocabulary — provably skill-free
# ---------------------------------------------------------------------------


# Token fragments of every surface form (whitespace- and punctuation-split),
# plus the full forms. Filler tokens are excluded from this set so filler can
# neither mention a skill on its own nor sit adjacent to the embedded form
# and complete a longer lexicon surface form ("node" + embedded "js").
def _fragments() -> frozenset[str]:
    pieces: set[str] = set()
    for entry in _ENTRIES:
        for surface in _surface_forms(entry):
            pieces.add(surface)
            for token in surface.split():
                pieces.add(token)
                for part in "".join(ch if ch.isalnum() else " " for ch in token).split():
                    pieces.add(part)
    return frozenset(pieces)


_FORBIDDEN_FILLER: Final[frozenset[str]] = _fragments()

# Generic job-posting words, verified disjoint from the fragment set at
# import time so a vocabulary edit cannot silently break the construction.
_GENERIC_WORDS: Final[list[str]] = [
    "check",
    "selection",
    "experience",
    "team",
    "developer",
    "required",
    "preferred",
    "work",
    "process",
    "strong",
    "years",
    "communication",
    "responsibilities",
    "candidate",
]
assert not set(_GENERIC_WORDS) & _FORBIDDEN_FILLER

_filler_token = st.one_of(
    st.sampled_from(_GENERIC_WORDS),
    st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=10).filter(
        lambda token: token not in _FORBIDDEN_FILLER
    ),
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_entry_index = st.integers(min_value=0, max_value=len(_ENTRIES) - 1)


@st.composite
def _cased_embedding(draw: st.DrawFn, entry: SkillEntry) -> tuple[str, str]:
    """A (cased surface form, document) pair for ``entry``.

    Draws one of the entry's surface forms, applies an arbitrary
    per-character letter casing (case-less characters — punctuation,
    digits, the space in multi-token forms — pass through), and embeds the
    result at a whitespace token boundary at a random position inside
    skill-free filler.
    """
    surface = draw(st.sampled_from(_surface_forms(entry)))
    flags = draw(st.lists(st.booleans(), min_size=len(surface), max_size=len(surface)))
    cased = "".join(
        ch.upper() if flag else ch.lower() for ch, flag in zip(surface, flags, strict=True)
    )
    filler = draw(st.lists(_filler_token, min_size=0, max_size=20))
    position = draw(st.integers(min_value=0, max_value=len(filler)))
    document = " ".join([*filler[:position], cased, *filler[position:]])
    return cased, document


@st.composite
def _entry_with_two_documents(draw: st.DrawFn) -> tuple[SkillEntry, str, str]:
    """One lexicon entry and two documents each mentioning it once.

    The two documents draw their surface form, casing, filler, and embed
    position independently — so the same skill is typically expressed by
    *different* surface forms in the two texts (the alias-symmetry shape).
    """
    entry = _ENTRIES[draw(_entry_index)]
    _, document_one = draw(_cased_embedding(entry))
    _, document_two = draw(_cased_embedding(entry))
    return entry, document_one, document_two


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(data=st.data())
def test_cased_alias_resolves_to_canonical_term(data: st.DataObject) -> None:
    """Any cased surface form extracts as its canonical term (Req 4.3).

    For any lexicon entry, any of its surface forms under any letter
    casing, embedded at token boundaries in skill-free filler,
    ``extract(text)`` yields the entry's canonical term — and only
    canonical lexicon terms ever appear, so the raw alias itself is never
    emitted as a term.
    """
    entry = _ENTRIES[data.draw(_entry_index, label="entry")]
    cased, document = data.draw(_cased_embedding(entry), label="document")

    terms = {keyword.term for keyword in _EXTRACTOR.extract(document)}

    assert entry.canonical in terms, (
        "embedded surface form did not resolve to its canonical term",
        cased,
        document,
    )
    canonicals = {e.canonical for e in _ENTRIES}
    assert terms <= canonicals, ("non-canonical term emitted", terms - canonicals)


@settings(max_examples=200, deadline=None)
@given(scenario=_entry_with_two_documents())
@example(
    scenario=(
        _ENTRIES[0],  # python
        "py",  # resume says "py" ...
        "Python developer required",  # ... JD says "Python"
    )
)
@example(
    scenario=(
        _ENTRIES[0],  # python
        "python daily driver",  # resume says "python" ...
        "PY experience preferred",  # ... JD says "PY"
    )
)
@example(
    scenario=(
        _ENTRIES[2],  # node.js
        "node js services",  # multi-token alias in the resume
        "strong NODEJS candidate",  # different alias, different casing, in the JD
    )
)
def test_alias_resolution_is_symmetric_across_text_roles(
    scenario: tuple[SkillEntry, str, str],
) -> None:
    """The same skill matches through different aliases, in either role (Req 4.3).

    With one skill expressed by independently drawn surface forms in two
    texts, ``analyze()`` reports the canonical term as ``matched`` — and
    swapping which text plays the resume role and which plays the
    job-description role leaves that classification unchanged. Alias
    resolution is applied identically to both roles, so a resume saying
    "py" matches a JD saying "python" and vice versa.
    """
    entry, document_one, document_two = scenario

    for resume_text, job_description in (
        (document_one, document_two),
        (document_two, document_one),
    ):
        analysis = _EXTRACTOR.analyze(resume_text, job_description)
        analyzed = {keyword.term for keyword in analysis.analyzed}
        matched = {keyword.term for keyword in analysis.matched}
        missing = {keyword.term for keyword in analysis.missing}

        assert entry.canonical in analyzed, (
            "JD mention did not reach the analyzed set",
            resume_text,
            job_description,
        )
        assert entry.canonical in matched, (
            "resume mention did not match the JD mention of the same skill",
            resume_text,
            job_description,
        )
        assert entry.canonical not in missing
