"""Feature: phase-2-nlp-embeddings — Property 9.

Property 9: Matching respects token boundaries and prefers the longest
surface form.

    *For any* text in which a skill surface form occurs only as a strict
    substring of a longer token, that skill is not extracted; and *for
    any* text containing a multi-token lexicon surface form, the longest
    matching form's canonical term is extracted rather than a canonical
    term for any of its sub-tokens.

**Validates: Requirements 4.11**

Requirement 4.11 says the Skill_Extractor matches skill surface forms only
at token boundaries, preferring the longest matching surface form at any
position, so a skill term is never recognized as a substring of a longer
token ("java" never matches inside "javascript", "node.js" resolves to its
own canonical term rather than to "js"). This module asserts both halves
across a generated input space:

* **Token boundaries.** For any lexicon entry, any of its single-token
  alphanumeric surface forms, and any alphanumeric prefix/suffix (at
  least one non-empty), the concatenation forms one longer token in which
  the surface occurs only as a strict substring. Embedding that longer
  token in skill-free filler must NOT extract the entry's canonical term.
  The constructed token is filtered against the *same entry's* surface
  forms (so "go" + "lang" → "golang", an alias of the same entry, is
  excluded); a collision with a *different* entry's surface form (e.g.
  "node" + "js" → "nodejs") is fine — the assertion targets the embedded
  entry's canonical, which still must not appear.
* **Longest match wins.** For any nested pair — a multi-token lexicon
  surface form one of whose sub-tokens is itself a lexicon surface form
  of a different entry ("js" inside "node js", "sql" inside "sql server",
  "react" inside "react native") — embedding the longer form in
  skill-free filler extracts the longer form's canonical term and never
  the inner sub-token's canonical term.

The surrounding filler vocabulary is constructed to be disjoint from every
token fragment of every lexicon surface form, so the only potential skill
mention in a generated text is the embedded token/form — filler can
neither mention a skill on its own nor sit adjacent to the embedded form
and complete a longer lexicon surface form.

Per the design's PBT strategy, the extractor is built once at module scope
over a small synthetic lexicon (constructing a spaCy ``PhraseMatcher`` per
Hypothesis example would dominate the runtime budget) and the pipeline is
``spacy.blank("en")`` — tokenizer-only, no model artifact. A blank pipeline
assigns no POS, so the POS gate passes candidates through and boundary /
longest-match resolution — the behavior under test — is exercised in
isolation.

The Skill_Extractor is framework-free (Requirement 12.1): this test
constructs it directly from a synthetic lexicon document and an injected
``max_keywords`` cap, never touching settings, FastAPI, or the database.
"""

from __future__ import annotations

from string import ascii_lowercase, digits
from typing import Final

import spacy
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import Skill_Lexicon, SkillEntry
from matchlayer_api.scoring.skills import Skill_Extractor

# ---------------------------------------------------------------------------
# Synthetic lexicon and module-scoped extractor
# ---------------------------------------------------------------------------

# The set is built for nesting: "js" (javascript) is a sub-token of the
# "node js" alias (node.js); "sql" is a sub-token of "sql server"; "react"
# is a prefix sub-token of "react native". Single-token alphanumeric forms
# ("java", "py", "js", "go", "golang", "nodejs", ...) feed the substring
# half, including the classic "java"-inside-"javascript" shape and the
# "go" + "lang" → "golang" same-entry collision the generator must avoid.
_LEXICON: Final[Skill_Lexicon] = Skill_Lexicon(
    {
        "schema_version": 1,
        "lexicon_version": "prop9",
        "skills": [
            {
                "canonical": "java",
                "display": "Java",
                "category": "language",
                "weight": 1.0,
                "aliases": [],
            },
            {
                "canonical": "python",
                "display": "Python",
                "category": "language",
                "weight": 0.95,
                "aliases": ["py"],
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
                "canonical": "sql",
                "display": "SQL",
                "category": "language",
                "weight": 0.85,
                "aliases": [],
            },
            {
                "canonical": "sql server",
                "display": "SQL Server",
                "category": "database",
                "weight": 0.8,
                "aliases": [],
            },
            {
                "canonical": "react",
                "display": "React",
                "category": "framework",
                "weight": 0.75,
                "aliases": [],
            },
            {
                "canonical": "react native",
                "display": "React Native",
                "category": "framework",
                "weight": 0.7,
                "aliases": [],
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
# (50) exceeds the lexicon size so capping never hides a match result.
_EXTRACTOR: Final[Skill_Extractor] = Skill_Extractor(spacy.blank("en"), _LEXICON, max_keywords=50)

_ENTRIES: Final[tuple[SkillEntry, ...]] = tuple(_LEXICON.entries)

_CANONICALS: Final[frozenset[str]] = frozenset(entry.canonical for entry in _ENTRIES)


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

_filler_tokens = st.lists(_filler_token, min_size=0, max_size=20)

# ---------------------------------------------------------------------------
# Token-boundary strategy: surface embedded as a strict substring of a token
# ---------------------------------------------------------------------------

# (entry, surface) pairs eligible for substring embedding: single-token,
# purely alphanumeric surface forms. Concatenating alphanumeric affixes to
# these yields exactly one spaCy token (the blank English tokenizer splits
# only on whitespace and punctuation), so the surface occurs strictly
# inside a longer token — the shape Requirement 4.11 forbids matching.
_ALNUM_SURFACES: Final[tuple[tuple[SkillEntry, str], ...]] = tuple(
    (entry, surface) for entry in _ENTRIES for surface in _surface_forms(entry) if surface.isalnum()
)
assert _ALNUM_SURFACES  # the lexicon must feed the substring half

_affix = st.text(alphabet=ascii_lowercase + digits, min_size=0, max_size=6)


@st.composite
def _boundary_case(draw: st.DrawFn) -> tuple[str, str]:
    """A (canonical-that-must-not-appear, document) pair.

    Draws a single-token alphanumeric surface form, wraps it in
    alphanumeric affixes (at least one non-empty, so the surface is a
    *strict* substring of the resulting token), and embeds the longer
    token at a random position inside skill-free filler. The constructed
    token is rejected when it collides with a surface form of the *same*
    entry ("go" + "lang" → "golang"), because such a collision legitimately
    resolves to the entry's canonical term.
    """
    entry, surface = draw(st.sampled_from(_ALNUM_SURFACES))
    prefix = draw(_affix)
    suffix = draw(_affix)
    if not prefix and not suffix:
        suffix = draw(st.text(alphabet=ascii_lowercase + digits, min_size=1, max_size=6))
    token = prefix + surface + suffix
    # Same-entry collision ("go" + "lang" → "golang") legitimately resolves
    # to the entry's canonical term — discard the example.
    assume(token not in _surface_forms(entry))
    filler = draw(_filler_tokens)
    position = draw(st.integers(min_value=0, max_value=len(filler)))
    document = " ".join([*filler[:position], token, *filler[position:]])
    return entry.canonical, document


@settings(max_examples=200, deadline=None)
@given(case=_boundary_case())
@example(case=("java", "javascript is required"))  # the requirement's own example
@example(case=("python", "py3 experience preferred"))  # alias + digit suffix
@example(case=("javascript", "nodejs candidate"))  # "js" inside another entry's alias
@example(case=("sql", "mysql and postgresql experience"))  # suffix-embedded twice
def test_surface_inside_longer_token_never_matches(case: tuple[str, str]) -> None:
    """A skill occurring only inside a longer token is not extracted (Req 4.11).

    The document's only occurrence of the entry's surface form lies
    strictly inside a longer alphanumeric token; the PhraseMatcher
    compares whole tokens, so the entry's canonical term must not appear
    in the extraction output.
    """
    canonical, document = case

    terms = {keyword.term for keyword in _EXTRACTOR.extract(document)}

    assert canonical not in terms, (
        "surface form matched inside a longer token",
        canonical,
        document,
        terms,
    )
    # Skill-only guarantee still holds for whatever else was extracted.
    assert terms <= _CANONICALS


# ---------------------------------------------------------------------------
# Longest-match strategy: nested multi-token surface forms
# ---------------------------------------------------------------------------

# (longer surface form, its canonical, inner sub-token canonical that must
# NOT appear). Each inner form is a strict sub-span of the longer form at
# the same position, resolving to a *different* entry's canonical term.
_NESTED_PAIRS: Final[tuple[tuple[str, str, str], ...]] = (
    ("node js", "node.js", "javascript"),  # alias "js" inside alias "node js"
    ("sql server", "sql server", "sql"),  # canonical inside longer canonical
    ("react native", "react native", "react"),
)


@st.composite
def _longest_match_case(draw: st.DrawFn) -> tuple[str, str, str]:
    """A (longer canonical, inner canonical, document) triple.

    Embeds the longer multi-token surface form at a random position inside
    skill-free filler; the inner surface form occurs only as a sub-span of
    the longer form.
    """
    longer_surface, longer_canonical, inner_canonical = draw(st.sampled_from(_NESTED_PAIRS))
    filler = draw(_filler_tokens)
    position = draw(st.integers(min_value=0, max_value=len(filler)))
    document = " ".join([*filler[:position], longer_surface, *filler[position:]])
    return longer_canonical, inner_canonical, document


@settings(max_examples=200, deadline=None)
@given(case=_longest_match_case())
@example(case=("node.js", "javascript", "node js services"))  # the requirement's own example
@example(case=("sql server", "sql", "sql server administration"))
@example(case=("react native", "react", "strong react native candidate"))
def test_longest_surface_form_wins_over_inner_sub_token(case: tuple[str, str, str]) -> None:
    """The longest surface form's canonical is extracted, never the inner's (Req 4.11).

    With a multi-token lexicon surface form embedded in skill-free filler,
    overlap resolution keeps the longest span: the longer form's canonical
    term appears in the output, and the canonical term of the inner
    sub-token surface form never does.
    """
    longer_canonical, inner_canonical, document = case

    terms = {keyword.term for keyword in _EXTRACTOR.extract(document)}

    assert longer_canonical in terms, (
        "longest surface form was not extracted",
        longer_canonical,
        document,
        terms,
    )
    assert inner_canonical not in terms, (
        "inner sub-token surface form leaked past longest-match resolution",
        inner_canonical,
        document,
        terms,
    )
