"""Feature: phase-2-nlp-embeddings — Property 3.

Property 3: Cosine mapping is bounded, monotone, and deterministic.

    *For any* two non-zero vectors of equal dimension,
    ``Semantic_Scorer.similarity_component`` returns a value in [0, 1] equal
    to ``(cosine + 1) / 2`` (within clamping), repeated invocation on
    identical vectors returns identical values, and *for any* two vector
    pairs whose cosines satisfy ``cos_a <= cos_b``, the mapped components
    satisfy ``map(cos_a) <= map(cos_b)``.

**Validates: Requirements 3.1, 3.4**

The :class:`~matchlayer_api.scoring.semantic.Semantic_Scorer` is the design's
D3 decision made executable: the affine map ``(cosine + 1) / 2`` clamped to
[0, 1] against float drift. Requirement 3.1 demands that the transformation
is documented and independently reproducible — asserted here by recomputing
cosine similarity from scratch in the test and comparing. Requirement 3.4
demands determinism — identical embedding pairs always produce the identical
component, across calls and across independently constructed scorer
instances.

Three clauses, three tests, all over generated non-zero same-dimension float
vectors:

* **Bounded and formula-faithful.** The component lies in [0, 1] and equals
  the independently recomputed ``(cosine + 1) / 2`` (clamped) — any tester
  applying the documented formula obtains the same value.
* **Deterministic.** ``similarity_component(a, b)`` twice on one scorer, and
  once on a fresh :class:`Semantic_Scorer`, all yield the same float — the
  scorer is a pure function with no hidden state.
* **Monotone.** For two generated vector pairs, the pair with the (weakly)
  larger independently computed cosine never maps to a smaller component —
  a higher cosine never ranks lower after the transformation.

The vector strategy constrains the input space intelligently: component
magnitudes are bounded away from over/underflow (so squares neither vanish to
0.0 nor overflow), and zero-magnitude vectors are excluded with exactly the
norm computation the scorer itself uses — degenerate geometry is Property 12
(task 2.6), not this property.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.scoring.semantic import Semantic_Scorer

# Component values are kept in a range where x*x can neither underflow to 0.0
# (which could zero a norm and turn a mathematically non-zero vector into
# undefined geometry) nor overflow: |x| in [1e-3, 1e6] or exactly 0.0.
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


def _norm(vector: Sequence[float]) -> float:
    """The scorer's own norm reduction, reused to filter degenerate vectors."""
    return math.sqrt(math.fsum(x * x for x in vector))


def _vector_pairs(dimension: int, count: int) -> st.SearchStrategy[list[list[float]]]:
    """`count` non-zero vectors sharing one generated dimension."""
    non_zero_vector = st.lists(_component, min_size=dimension, max_size=dimension).filter(
        lambda v: _norm(v) > 0.0
    )
    return st.lists(non_zero_vector, min_size=count, max_size=count)


# One shared dimension per example so every generated pair is comparable.
_dimension = st.integers(min_value=1, max_value=16)


def _expected_component(a: Sequence[float], b: Sequence[float]) -> float:
    """The documented transformation, recomputed independently of the scorer.

    ``(cosine(a, b) + 1) / 2`` clamped to [0, 1] — the exact formula the
    module docstring publishes for testers (Requirement 3.1, design D3).
    """
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    cosine = dot / (_norm(a) * _norm(b))
    return min(1.0, max(0.0, (cosine + 1.0) / 2.0))


@settings(max_examples=200, deadline=None)
@given(data=st.data(), dimension=_dimension)
def test_component_is_bounded_and_matches_documented_formula(
    data: st.DataObject, dimension: int
) -> None:
    """The component lies in [0, 1] and equals ``(cosine + 1) / 2`` (clamped).

    Requirement 3.1: the transformation is documented and reproducible — a
    tester recomputing cosine similarity and applying the affine map obtains
    the scorer's value.
    """
    a, b = data.draw(_vector_pairs(dimension, count=2))
    scorer = Semantic_Scorer()

    component = scorer.similarity_component(a, b)

    assert 0.0 <= component <= 1.0
    assert component == _expected_component(a, b)


@settings(max_examples=200, deadline=None)
@given(data=st.data(), dimension=_dimension)
def test_component_is_deterministic(data: st.DataObject, dimension: int) -> None:
    """Identical vectors produce identical components, across calls and instances.

    Requirement 3.4: the mapping is a pure function of the two input vectors —
    no randomness, no per-instance state. Equality is exact (``==`` on floats)
    on purpose: identical inputs through identical code must yield identical
    bits, and any drift is precisely the non-determinism the requirement
    forbids.
    """
    a, b = data.draw(_vector_pairs(dimension, count=2))
    scorer = Semantic_Scorer()

    first = scorer.similarity_component(a, b)
    second = scorer.similarity_component(list(a), list(b))
    assert first == second

    # A separately constructed scorer agrees — determinism does not depend on
    # object identity, only on the input vectors.
    assert Semantic_Scorer().similarity_component(a, b) == first


@settings(max_examples=200, deadline=None)
@given(data=st.data(), dimension=_dimension)
def test_mapping_is_monotone_in_cosine(data: st.DataObject, dimension: int) -> None:
    """A weakly larger cosine never maps to a smaller component.

    Requirement 3.1's ranking-preservation clause: for two vector pairs whose
    independently computed cosines satisfy ``cos_a <= cos_b``, the scorer's
    components satisfy ``map(cos_a) <= map(cos_b)``.
    """
    a1, b1, a2, b2 = data.draw(_vector_pairs(dimension, count=4))
    scorer = Semantic_Scorer()

    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        return math.fsum(x * y for x, y in zip(a, b, strict=True)) / (_norm(a) * _norm(b))

    # Order the two pairs by their independently computed cosine.
    if cosine(a1, b1) <= cosine(a2, b2):
        low, high = (a1, b1), (a2, b2)
    else:
        low, high = (a2, b2), (a1, b1)

    assert scorer.similarity_component(*low) <= scorer.similarity_component(*high)
