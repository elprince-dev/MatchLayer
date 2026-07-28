"""Feature: phase-2-nlp-embeddings — Property 12.

Property 12: Undefined cosine geometry signals an error.

    *For any* vector pair with mismatched dimensions, and *for any* pair in
    which either vector has zero magnitude,
    ``Semantic_Scorer.similarity_component`` raises
    :class:`EmbeddingGeometryError` rather than returning a value.

**Validates: Requirements 3.10**

Cosine similarity is undefined when the two vectors do not inhabit the same
space (mismatched dimensions) or when either vector has zero magnitude (the
angle to a zero vector does not exist). Requirement 3.10 forbids the scorer
from papering over that with NaN or an invented number: the undefined
geometry must surface as an error so the Scoring_Service can complete the
request via the Phase 1 per-request fallback.

Two clauses, two tests:

* **Mismatched dimensions.** Two generated vectors of deliberately different
  lengths — zero-magnitude or not, the dimension check fires first — always
  raise :class:`EmbeddingGeometryError`. ``pytest.raises`` guarantees no
  value (NaN or otherwise) is ever returned.
* **Zero magnitude.** An all-zero vector of a generated dimension, paired
  with an arbitrary same-dimension vector on a randomly generated side (or
  with a second all-zero vector), always raises
  :class:`EmbeddingGeometryError`.

The vector strategy mirrors Property 3 (``test_cosine_mapping.py``): finite
float components bounded away from over/underflow so a mathematically
non-zero vector can never collapse into degenerate geometry by accident —
the *only* zero-magnitude vectors in play are the ones this test constructs
on purpose.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.semantic import EmbeddingGeometryError, Semantic_Scorer

# Same bounded-component strategy as Property 3: |x| in [1e-3, 1e6] or exactly
# 0.0, so x*x neither underflows to 0.0 (silently zeroing a norm) nor
# overflows. Zero magnitude is introduced only deliberately, via _zero_vector.
_component = st.one_of(
    st.just(0.0),
    st.floats(
        min_value=1e-3,
        max_value=1e6,
        allow_nan=False,
        allow_infinity=False,
    ),
    st.floats(
        min_value=-1e6,
        max_value=-1e-3,
        allow_nan=False,
        allow_infinity=False,
    ),
)

_dimension = st.integers(min_value=1, max_value=16)


def _vector(dimension: int) -> st.SearchStrategy[list[float]]:
    """An arbitrary vector of the given dimension (zero magnitude allowed)."""
    return st.lists(_component, min_size=dimension, max_size=dimension)


def _zero_vector(dimension: int) -> st.SearchStrategy[list[float]]:
    """The all-zero vector of the given dimension — zero magnitude by construction."""
    return st.lists(st.just(0.0), min_size=dimension, max_size=dimension)


@settings(max_examples=200, deadline=None)
@given(data=st.data())
def test_mismatched_dimensions_raise_geometry_error(data: st.DataObject) -> None:
    """Vectors of different dimensions always raise, never return a value.

    Requirement 3.10: mismatched dimensions make cosine similarity undefined,
    so the scorer signals ``EmbeddingGeometryError`` to its caller. The
    vectors themselves are arbitrary — including zero-magnitude ones — since
    the dimension mismatch alone is sufficient to make the geometry
    undefined.
    """
    dim_a, dim_b = data.draw(
        st.tuples(_dimension, _dimension).filter(lambda pair: pair[0] != pair[1])
    )
    a = data.draw(_vector(dim_a))
    b = data.draw(_vector(dim_b))
    scorer = Semantic_Scorer()

    with pytest.raises(EmbeddingGeometryError):
        scorer.similarity_component(a, b)


@settings(max_examples=200, deadline=None)
@given(data=st.data(), dimension=_dimension)
def test_zero_magnitude_vector_raises_geometry_error(data: st.DataObject, dimension: int) -> None:
    """A zero-magnitude vector on either (or both) sides always raises.

    Requirement 3.10: the angle to a zero vector does not exist, so cosine
    similarity is undefined and the scorer must raise rather than return NaN
    or an invented component. The zero vector is placed on a generated side —
    left, right, or both — and paired with an arbitrary same-dimension
    vector, covering every way zero magnitude can enter the pair.
    """
    zero = data.draw(_zero_vector(dimension))
    other = data.draw(_vector(dimension))
    placement = data.draw(st.sampled_from(["left", "right", "both"]))
    if placement == "left":
        a, b = zero, other
    elif placement == "right":
        a, b = other, zero
    else:
        a, b = zero, list(zero)
    scorer = Semantic_Scorer()

    with pytest.raises(EmbeddingGeometryError):
        scorer.similarity_component(a, b)
