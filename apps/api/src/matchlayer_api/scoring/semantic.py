"""Semantic_Scorer — cosine similarity component (phase-2-nlp-embeddings, task 2.4).

The :class:`Semantic_Scorer` turns two embedding vectors into the semantic
similarity component of the match score (Requirement 3.1). This module is the
repository documentation of the transformation that Requirement 3.1 requires:
any tester applying the formula below to the same cosine value obtains the
same component value.

Cosine → component transformation (design decision D3)
-------------------------------------------------------

Cosine similarity of two non-zero vectors ``a`` and ``b`` is::

    cosine(a, b) = dot(a, b) / (|a| * |b|)

which lies in the inclusive range [-1, 1]. The similarity component maps it
onto [0, 1] with the affine transformation::

    component = (cosine(a, b) + 1) / 2

The transformation is:

* **deterministic** — a pure function of the two input vectors, no randomness
  and no external state (Requirement 3.4);
* **monotonically non-decreasing** over the full cosine range [-1, 1] — a
  higher cosine never yields a lower component;
* **exactly onto [0, 1]** — cosine -1 maps to 0, cosine 0 maps to 0.5, and
  cosine 1 maps to 1.

The result is additionally clamped to [0, 1] to guard against floating-point
drift (a computed cosine of ``1.0000000000000002`` must not leak a component
above 1). Clamping never changes a mathematically in-range value.

Undefined geometry (Requirement 3.10)
-------------------------------------

Cosine similarity is undefined when the vectors have mismatched dimensions or
when either has zero magnitude (the angle to a zero vector does not exist).
Rather than returning NaN or inventing a value, those cases raise
:class:`EmbeddingGeometryError`; the Scoring_Service catches it and completes
the request via the per-request Phase 1 fallback (Requirement 7.3), stamped
with the Phase 1 ``scorer_version`` (Requirement 6.2).

Import boundary (Requirement 12.1): this module is part of the framework-free
Scoring_Core — Python standard library only, no FastAPI / SQLAlchemy /
``matchlayer_api.config`` / storage / web imports, and no environment reads.
Embedding vectors arrive as plain in-memory sequences injected by the caller.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


class EmbeddingGeometryError(ValueError):
    """Raised when cosine similarity is undefined (Requirement 3.10).

    Signals mismatched embedding dimensions or a zero-magnitude embedding.
    A subclass of :class:`ValueError` because the inputs are structurally
    invalid for the operation; the Scoring_Service maps it to the per-request
    Phase 1 fallback rather than surfacing an error to the user.
    """


class Semantic_Scorer:  # noqa: N801 -- design uses the underscored component name.
    """Deterministic cosine-based similarity component (Requirements 3.1, 3.4).

    Stateless and pure: construct once and reuse across requests. All inputs
    arrive as method arguments; nothing is read from configuration or the
    environment (Requirement 12.1).
    """

    def similarity_component(self, a: Sequence[float], b: Sequence[float]) -> float:
        """``(cosine(a, b) + 1) / 2``, clamped to [0, 1] (Requirement 3.1, D3).

        Applies the module-docstring transformation: cosine similarity of the
        two embeddings mapped onto [0, 1] by the documented affine formula,
        then clamped against floating-point drift. Deterministic — identical
        embeddings always produce the identical component value
        (Requirement 3.4).

        Raises :class:`EmbeddingGeometryError` if ``len(a) != len(b)`` or if
        either vector has zero magnitude, both of which make cosine
        similarity undefined (Requirement 3.10).
        """
        if len(a) != len(b):
            msg = (
                f"cosine similarity is undefined for mismatched embedding "
                f"dimensions: {len(a)} != {len(b)}"
            )
            raise EmbeddingGeometryError(msg)

        # math.fsum keeps every reduction exactly reproducible from the
        # documented formula, mirroring the Embedding_Service aggregation.
        norm_a = math.sqrt(math.fsum(x * x for x in a))
        norm_b = math.sqrt(math.fsum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            msg = "cosine similarity is undefined for a zero-magnitude embedding"
            raise EmbeddingGeometryError(msg)

        dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
        cosine = dot / (norm_a * norm_b)
        component = (cosine + 1.0) / 2.0
        # Clamp against float drift only; an in-range value passes unchanged.
        return min(1.0, max(0.0, component))
