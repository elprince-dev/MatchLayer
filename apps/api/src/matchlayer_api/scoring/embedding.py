"""Text_Encoder protocol and Embedding_Service (phase-2-nlp-embeddings, task 2.1).

The :class:`Embedding_Service` is the framework-free Scoring_Core component
that turns one input text into one fixed-dimension embedding vector using an
injected :class:`Text_Encoder` (Requirement 2.1). The encoder abstracts the
concrete sentence-embedding model: in production the ``ml/`` adapter wraps the
loaded ``SentenceTransformer`` (with ``normalize_embeddings=True``) and its
tokenizer in this protocol; in property tests a deterministic hash-based stub
encoder stands in so the chunk-and-aggregate logic is exercised without any
model artifact (design D6).

Chunk-and-aggregate strategy (design D4, Requirement 2.3)
---------------------------------------------------------

Sentence-embedding models have a maximum input sequence length
(``max_tokens``, 256 word-piece tokens for ``all-MiniLM-L6-v2``). A resume or
job description can exceed it, and silently truncating would embed only the
document's prefix. The documented, deterministic strategy is:

1. **Fits?** If ``count_tokens(text) <= max_tokens``, embed with a single
   ``encode`` call — no chunking.
2. **Chunk.** Otherwise split the text with ``split_tokens(text, max_tokens)``
   into consecutive, non-overlapping chunks of at most ``max_tokens`` tokens
   whose concatenation covers the full document (the model's own tokenizer
   decides the boundaries, so no text is silently dropped).
3. **Encode.** Embed every chunk in one ``encode`` call. Each returned vector
   is L2-normalized by the encoder (protocol contract).
4. **Aggregate.** Combine the chunk vectors into a token-count-weighted mean::

       result = sum_i(token_count_i * v_i) / total_tokens

   Weighting by token count makes longer chunks contribute proportionally to
   how much of the document they carry, so the aggregate represents the whole
   document rather than giving a short trailing chunk equal say.
5. **Normalize.** L2-normalize the mean so the final embedding has unit
   magnitude like a single-chunk embedding, keeping downstream cosine
   similarity well-behaved regardless of which path produced the vector.

Every step is a pure in-memory computation over the injected encoder's
outputs: no chunk text and no intermediate chunk vector is written to a log
line, a temporary file, or any telemetry signal (Requirement 2.3 — the input
is Restricted PII per ``security.md``). Determinism (Requirement 2.4) follows
from the encoder protocol: a deterministic tokenizer and encoder make every
step above a deterministic function of the input text.

Import boundary (Requirement 12.1): this module imports only the Python
standard library. It never imports FastAPI, SQLAlchemy,
``matchlayer_api.config``, or any storage/web module; the encoder and its
configuration are injected through the constructor by the ``ml/`` adapter.

Design reference: "Text_Encoder protocol + Embedding_Service" (D4, D6).
Requirements covered: 2.1, 2.3, 2.4, 12.1.
"""

from __future__ import annotations

import math
from typing import Final, Protocol


class Text_Encoder(Protocol):  # noqa: N801 -- design uses the underscored component name.
    """What the :class:`Embedding_Service` needs from a sentence-embedding model.

    Implementations must be deterministic: identical inputs produce identical
    outputs (Requirement 2.4). The production implementation lives in the
    ``ml/`` adapter and wraps the loaded ``SentenceTransformer`` plus its
    tokenizer; tests use a deterministic hash-based stub.
    """

    @property
    def dimension(self) -> int:
        """Output vector length; every ``encode`` result has exactly this length."""
        ...

    @property
    def max_tokens(self) -> int:
        """The model's maximum input sequence length in tokens (word-piece for MiniLM)."""
        ...

    def count_tokens(self, text: str) -> int:
        """Number of tokens the model's tokenizer produces for ``text``."""
        ...

    def split_tokens(self, text: str, max_tokens: int) -> list[str]:
        """Deterministically split ``text`` into consecutive, non-overlapping chunks.

        Each chunk tokenizes to at most ``max_tokens`` tokens, and the chunks'
        concatenation covers the full text — no token is dropped or repeated.
        """
        ...

    def encode(self, texts: list[str]) -> list[list[float]]:
        """L2-normalized embeddings, one per input, each of length :attr:`dimension`."""
        ...


class Embedding_Service:  # noqa: N801 -- design uses the underscored component name.
    """Deterministic full-document embedding over an injected :class:`Text_Encoder`.

    Construct once with the encoder and reuse across requests; instances are
    immutable and hold only the encoder. Pure and synchronous by design:
    concurrency and wall-clock timeouts are the caller's concern (design D7 —
    the Scoring_Service wraps :meth:`embed` in a worker thread under
    ``asyncio.wait_for``).
    """

    def __init__(self, encoder: Text_Encoder) -> None:
        self._encoder: Final[Text_Encoder] = encoder

    @property
    def dimension(self) -> int:
        """The dimension of every embedding this service produces."""
        return self._encoder.dimension

    def embed(self, text: str) -> list[float]:
        """Embed ``text`` as one fixed-dimension vector (Requirements 2.1, 2.3, 2.4).

        Applies the module-docstring chunk-and-aggregate strategy: a single
        ``encode`` call when the text fits within ``max_tokens``, otherwise
        non-overlapping tokenizer chunks combined by a token-count-weighted
        mean and L2-normalized. Entirely in memory; nothing is written
        anywhere (Requirement 2.3).
        """
        encoder = self._encoder
        if encoder.count_tokens(text) <= encoder.max_tokens:
            # Fits in one model pass; the encoder already L2-normalizes.
            return list(encoder.encode([text])[0])

        chunks = encoder.split_tokens(text, encoder.max_tokens)
        vectors = encoder.encode(chunks)
        # Per-chunk token counts weight the aggregation. The protocol
        # guarantees the chunks cover the full text, and this branch is only
        # reached when count_tokens(text) > max_tokens >= 0, so the total is
        # strictly positive.
        counts = [encoder.count_tokens(chunk) for chunk in chunks]
        total_tokens = sum(counts)

        # Each component summed with math.fsum in chunk order: deterministic,
        # exactly reproducible from the documented formula, and free of the
        # rounding drift a naive running sum can accumulate.
        weighted_mean = [
            math.fsum(count * vector[index] for count, vector in zip(counts, vectors, strict=True))
            / total_tokens
            for index in range(encoder.dimension)
        ]

        return _l2_normalize(weighted_mean)


def _l2_normalize(vector: list[float]) -> list[float]:
    """Scale ``vector`` to unit L2 magnitude.

    A zero-magnitude vector (chunk embeddings exactly cancelling in the
    weighted mean — a degenerate case) is returned unchanged: normalization is
    undefined there, and downstream the ``Semantic_Scorer`` treats a
    zero-magnitude embedding as an :class:`EmbeddingGeometryError` per
    Requirement 3.10 rather than this module inventing a direction.
    """
    norm = math.sqrt(math.fsum(component * component for component in vector))
    if norm == 0.0:
        return list(vector)
    return [component / norm for component in vector]
