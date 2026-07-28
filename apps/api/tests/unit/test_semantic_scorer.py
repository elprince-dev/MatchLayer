"""Unit tests for the Semantic_Scorer cosine mapping (task 2.4).

Concrete examples of the documented ``(cosine + 1) / 2`` transformation
(Requirement 3.1, design D3), determinism (Requirement 3.4), and the
undefined-geometry error cases (Requirement 3.10). The exhaustive
boundedness/monotonicity and geometry-error properties are covered by the
property tests of tasks 2.5 and 2.6.
"""

from __future__ import annotations

import pytest

from matchlayer_api.scoring.semantic import EmbeddingGeometryError, Semantic_Scorer


@pytest.fixture
def scorer() -> Semantic_Scorer:
    return Semantic_Scorer()


class TestDocumentedTransformation:
    def test_identical_vectors_map_to_one(self, scorer: Semantic_Scorer) -> None:
        vector = [0.6, 0.8]
        assert scorer.similarity_component(vector, vector) == pytest.approx(1.0)

    def test_opposite_vectors_map_to_zero(self, scorer: Semantic_Scorer) -> None:
        assert scorer.similarity_component([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(0.0)

    def test_orthogonal_vectors_map_to_half(self, scorer: Semantic_Scorer) -> None:
        assert scorer.similarity_component([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.5)

    def test_known_cosine_example(self, scorer: Semantic_Scorer) -> None:
        # cosine([3, 4], [4, 3]) = 24/25 = 0.96 → component (0.96 + 1) / 2 = 0.98.
        assert scorer.similarity_component([3.0, 4.0], [4.0, 3.0]) == pytest.approx(0.98)

    def test_scale_invariance_of_cosine(self, scorer: Semantic_Scorer) -> None:
        # Cosine depends on direction only, so scaling either input is a no-op.
        base = scorer.similarity_component([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
        scaled = scorer.similarity_component([10.0, 20.0, 30.0], [4.0, 5.0, 6.0])
        assert scaled == pytest.approx(base)

    def test_result_clamped_to_unit_interval(self, scorer: Semantic_Scorer) -> None:
        # Near-parallel high-dimensional vectors can push the float cosine a
        # hair past 1; the clamp keeps the component inside [0, 1].
        a = [0.1] * 384
        component = scorer.similarity_component(a, list(a))
        assert 0.0 <= component <= 1.0


class TestDeterminism:
    def test_identical_inputs_produce_identical_output(self, scorer: Semantic_Scorer) -> None:
        a = [0.25, -0.5, 0.75, -1.0]
        b = [0.1, 0.2, -0.3, 0.4]
        first = scorer.similarity_component(a, b)
        again = scorer.similarity_component(list(a), list(b))
        assert first == again

    def test_independent_instances_agree(self, scorer: Semantic_Scorer) -> None:
        a = [1.0, 2.0, 3.0]
        b = [-3.0, 2.0, -1.0]
        assert scorer.similarity_component(a, b) == Semantic_Scorer().similarity_component(a, b)


class TestUndefinedGeometry:
    def test_mismatched_dimensions_raise(self, scorer: Semantic_Scorer) -> None:
        with pytest.raises(EmbeddingGeometryError):
            scorer.similarity_component([1.0, 0.0], [1.0, 0.0, 0.0])

    def test_zero_magnitude_first_vector_raises(self, scorer: Semantic_Scorer) -> None:
        with pytest.raises(EmbeddingGeometryError):
            scorer.similarity_component([0.0, 0.0], [1.0, 0.0])

    def test_zero_magnitude_second_vector_raises(self, scorer: Semantic_Scorer) -> None:
        with pytest.raises(EmbeddingGeometryError):
            scorer.similarity_component([1.0, 0.0], [0.0, 0.0])

    def test_error_is_a_value_error(self) -> None:
        # The Scoring_Service catches EmbeddingGeometryError specifically;
        # being a ValueError documents that inputs were structurally invalid.
        assert issubclass(EmbeddingGeometryError, ValueError)
