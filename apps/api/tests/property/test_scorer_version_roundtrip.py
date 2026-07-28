"""Feature: phase-2-nlp-embeddings — Property 13.

Property 13: Scorer_Version round-trips and is injective.

    *For any* valid component tuple (algorithm version, lexicon version,
    embedding model name and revision, spaCy pipeline name and version),
    parsing the composed Scorer_Version string recovers exactly the original
    components from the string alone; and *for any* two distinct component
    tuples, the composed strings differ.

**Validates: Requirements 6.1, 6.3, 5.3**

Where the unit examples in ``tests/unit/test_versioning.py`` pin down the
documented format for hand-picked values, this module asserts the round-trip
and injectivity guarantees across a generated space of valid component tuples
using Hypothesis (>=100 examples per property).

The composition under test is :func:`semantic_scorer_version`, which prefixes
the fixed :data:`SEMANTIC_ALGORITHM_VERSION` and joins five caller-supplied
components. A component tuple is *valid* exactly when no component contains
``+`` (the segment separator) and none of the four ``@``-joined pair halves
(model name/revision, spaCy name/version) contains ``@`` (the pair separator)
— :func:`semantic_scorer_version` rejects anything else with
``ScorerVersionError``, so the generators below constrain to that space.

Three properties are asserted:

* **Round-trip (Requirement 6.1)** — ``parse_scorer_version`` applied to the
  composed string recovers every original component exactly, from the string
  alone.
* **Injectivity (Requirement 6.3)** — tuples differing in exactly *one*
  component compose to different strings. Single-field perturbation is the
  hard case for injectivity (two independently drawn tuples almost never
  nearly-collide), and injectivity for tuples differing in several fields
  follows from the round-trip property.
* **Lexicon sensitivity (Requirement 5.3)** — changing only the lexicon
  version always changes the composed Scorer_Version string.

The versioning module is framework-free Scoring_Core (Requirement 12.1):
these tests import it directly and touch no settings, FastAPI, or database.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.versioning import (
    SEMANTIC_ALGORITHM_VERSION,
    ScorerVersionParts,
    parse_scorer_version,
    semantic_scorer_version,
)

# ---------------------------------------------------------------------------
# Component generators
# ---------------------------------------------------------------------------

# Realistic anchor values so the generated space always includes the shapes
# production actually feeds in (HF model ids with '/', git SHAs, semver,
# date-stamped lexicon tags) alongside arbitrary unicode.
_REAL_LEXICON_VERSIONS = ["v1", "v2", "v2.1", "2025-01"]
_REAL_MODEL_NAMES = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-small-en-v1.5",
]
_REAL_MODEL_REVISIONS = ["c9745ed1d9f207416be6d2e6f8de32d1f16199bf", "main"]
_REAL_SPACY_NAMES = ["en_core_web_sm", "en_core_web_md"]
_REAL_SPACY_VERSIONS = ["3.8.0", "3.7.2"]

# The lexicon version must not contain the segment separator '+'; '@' is
# permitted because the lex segment is never pair-split (Requirement 6.3).
_lexicon_alphabet = st.characters(exclude_characters="+")
_arbitrary_lexicon_version = st.text(alphabet=_lexicon_alphabet, min_size=1, max_size=30)
_lexicon_version = st.one_of(
    st.sampled_from(_REAL_LEXICON_VERSIONS),
    _arbitrary_lexicon_version,
)

# The four '@'-joined pair halves must contain neither '+' nor '@'
# (Requirement 6.3) — either would make the composed value ambiguous.
_pair_alphabet = st.characters(exclude_characters="+@")
_arbitrary_pair_component = st.text(alphabet=_pair_alphabet, min_size=1, max_size=30)


def _pair_component(anchors: list[str]) -> st.SearchStrategy[str]:
    return st.one_of(st.sampled_from(anchors), _arbitrary_pair_component)


# A full valid component tuple, in semantic_scorer_version argument order:
# (lexicon_version, model_name, model_revision, spacy_name, spacy_version).
_component_tuples = st.tuples(
    _lexicon_version,
    _pair_component(_REAL_MODEL_NAMES),
    _pair_component(_REAL_MODEL_REVISIONS),
    _pair_component(_REAL_SPACY_NAMES),
    _pair_component(_REAL_SPACY_VERSIONS),
)


@st.composite
def _tuple_with_one_field_changed(
    draw: st.DrawFn,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Two valid tuples that differ in exactly one component."""
    base = draw(_component_tuples)
    index = draw(st.integers(min_value=0, max_value=4))
    field_strategy = _lexicon_version if index == 0 else _arbitrary_pair_component
    replacement = draw(field_strategy)
    assume(replacement != base[index])
    perturbed = tuple(
        replacement if position == index else component for position, component in enumerate(base)
    )
    return base, perturbed


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(components=_component_tuples)
def test_parse_recovers_exact_components_from_string_alone(
    components: tuple[str, str, str, str, str],
) -> None:
    """Round-trip (Requirement 6.1): parsing the composed string recovers
    exactly the original components, using nothing but the string."""
    lexicon_version, model_name, model_revision, spacy_name, spacy_version = components

    parts = parse_scorer_version(semantic_scorer_version(*components))

    assert parts == ScorerVersionParts(
        algorithm_version=SEMANTIC_ALGORITHM_VERSION,
        lexicon_version=lexicon_version,
        embedding_model_name=model_name,
        embedding_model_revision=model_revision,
        spacy_pipeline_name=spacy_name,
        spacy_pipeline_version=spacy_version,
    )


@settings(max_examples=200, deadline=None)
@given(pair=_tuple_with_one_field_changed())
def test_tuples_differing_in_one_component_compose_distinct_strings(
    pair: tuple[tuple[str, ...], tuple[str, ...]],
) -> None:
    """Injectivity (Requirement 6.3): changing any single component changes
    the composed string. Multi-field differences follow from the round-trip
    property, so the single-field case is the one worth generating."""
    base, perturbed = pair
    assert semantic_scorer_version(*base) != semantic_scorer_version(*perturbed)


@settings(max_examples=200, deadline=None)
@given(components=_component_tuples, other_lexicon_version=_lexicon_version)
def test_lexicon_version_change_changes_scorer_version(
    components: tuple[str, str, str, str, str],
    other_lexicon_version: str,
) -> None:
    """Lexicon sensitivity (Requirement 5.3): a Skill_Lexicon version change
    always produces a different Scorer_Version string."""
    assume(other_lexicon_version != components[0])
    swapped = (other_lexicon_version, *components[1:])
    assert semantic_scorer_version(*components) != semantic_scorer_version(*swapped)
