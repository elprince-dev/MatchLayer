"""Feature: phase-2-nlp-embeddings — Property 14.

Property 14: Fallback Scorer_Version values are distinct from every semantic
value.

    *For any* lexicon version and *any* valid Phase 2 component tuple, the
    Phase 1 fallback Scorer_Version string differs from the composed Phase 2
    string, identifies the Phase 1 algorithm version, and the lexicon version
    remains recoverable from the fallback string.

**Validates: Requirements 6.2**

Fallback results (startup Degraded_Mode, per-request embedding fallback, and
the empty-analyzed-skill-set fallback) are stamped with the unchanged Phase 1
format ``{ALGORITHM_VERSION}+lex.{lexicon_version}`` composed by
:func:`matchlayer_api.scoring.lexicon.scorer_version`. Requirement 6.2 demands
that such a value (a) identifies the Phase 1 deterministic algorithm, (b) is
distinct from *every* value the Phase 2 semantic pipeline can produce, and
(c) still carries the Skill_Lexicon version in the documented format.

Two properties assert exactly that across generated inputs:

* **Distinctness** — the fallback string never equals a composed Phase 2
  string, even when the two are built from *independently drawn* lexicon
  versions (the hard case: a Phase 2 lexicon version could otherwise be
  crafted to make the tails coincide). Distinctness is structural — the
  algorithm segment differs (``1.0.0`` vs ``2.0.0``) and Phase 1 values carry
  no ``emb.``/``spacy.`` segments — so no generated pair may collide.
* **Fallback parse** — ``parse_scorer_version`` applied to the fallback string
  alone recovers the Phase 1 algorithm version and the exact lexicon version,
  with every Phase 2-only field ``None``.

A Phase 1 lexicon version is *valid* when it contains no ``+`` (the segment
separator) — the same constraint the Phase 2 ``lex.`` segment carries — so the
generator below constrains to that space, mirroring the Property 13 module
(``test_scorer_version_roundtrip.py``).

The versioning and lexicon modules are framework-free Scoring_Core
(Requirement 12.1): these tests import them directly and touch no settings,
FastAPI, or database.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.lexicon import ALGORITHM_VERSION, scorer_version
from matchlayer_api.scoring.versioning import (
    ScorerVersionParts,
    parse_scorer_version,
    semantic_scorer_version,
)

# ---------------------------------------------------------------------------
# Component generators (mirroring test_scorer_version_roundtrip.py)
# ---------------------------------------------------------------------------

# Realistic anchor values so the generated space always includes the shapes
# production actually feeds in, alongside arbitrary unicode.
_REAL_LEXICON_VERSIONS = ["v1", "v2", "v2.1", "2025-01"]
_REAL_MODEL_NAMES = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-small-en-v1.5",
]
_REAL_MODEL_REVISIONS = ["c9745ed1d9f207416be6d2e6f8de32d1f16199bf", "main"]
_REAL_SPACY_NAMES = ["en_core_web_sm", "en_core_web_md"]
_REAL_SPACY_VERSIONS = ["3.8.0", "3.7.2"]

# A lexicon version is valid for both the Phase 1 fallback format and the
# Phase 2 ``lex.`` segment when it contains no segment separator '+'.
_lexicon_alphabet = st.characters(exclude_characters="+")
_arbitrary_lexicon_version = st.text(alphabet=_lexicon_alphabet, min_size=1, max_size=30)
_lexicon_version = st.one_of(
    st.sampled_from(_REAL_LEXICON_VERSIONS),
    _arbitrary_lexicon_version,
)

# The four '@'-joined pair halves must contain neither '+' nor '@'
# (Requirement 6.3) — semantic_scorer_version rejects anything else.
_pair_alphabet = st.characters(exclude_characters="+@")
_arbitrary_pair_component = st.text(alphabet=_pair_alphabet, min_size=1, max_size=30)


def _pair_component(anchors: list[str]) -> st.SearchStrategy[str]:
    return st.one_of(st.sampled_from(anchors), _arbitrary_pair_component)


# A full valid Phase 2 component tuple, in semantic_scorer_version argument
# order: (lexicon_version, model_name, model_revision, spacy_name,
# spacy_version).
_semantic_component_tuples = st.tuples(
    _lexicon_version,
    _pair_component(_REAL_MODEL_NAMES),
    _pair_component(_REAL_MODEL_REVISIONS),
    _pair_component(_REAL_SPACY_NAMES),
    _pair_component(_REAL_SPACY_VERSIONS),
)

# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    fallback_lexicon_version=_lexicon_version,
    semantic_components=_semantic_component_tuples,
)
def test_fallback_version_is_distinct_from_every_semantic_version(
    fallback_lexicon_version: str,
    semantic_components: tuple[str, str, str, str, str],
) -> None:
    """Distinctness (Requirement 6.2): the Phase 1 fallback Scorer_Version
    never equals a value the Phase 2 semantic pipeline can produce.

    The fallback lexicon version and the semantic tuple's lexicon version are
    drawn independently, so the assertion covers both same-lexicon and
    cross-lexicon pairs — a fallback value must be distinct from *every*
    semantic value, not merely the one sharing its lexicon version.
    """
    fallback = scorer_version(fallback_lexicon_version)
    semantic = semantic_scorer_version(*semantic_components)

    assert fallback != semantic


@settings(max_examples=200, deadline=None)
@given(lexicon_version=_lexicon_version)
def test_fallback_version_identifies_phase1_and_carries_lexicon_version(
    lexicon_version: str,
) -> None:
    """Fallback parse (Requirement 6.2): the fallback string alone identifies
    the Phase 1 deterministic algorithm and still carries the Skill_Lexicon
    version in the documented format, with no Phase 2 components present."""
    parts = parse_scorer_version(scorer_version(lexicon_version))

    assert parts == ScorerVersionParts(
        algorithm_version=ALGORITHM_VERSION,
        lexicon_version=lexicon_version,
    )
