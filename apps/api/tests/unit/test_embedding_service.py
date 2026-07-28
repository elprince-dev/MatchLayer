"""Unit tests for the Embedding_Service (phase-2-nlp-embeddings, task 2.1).

Example-based coverage of the two ``embed`` paths — single encode when the
text fits within ``max_tokens``, chunk-and-aggregate otherwise — driven by the
deterministic hash-based ``Stub_Text_Encoder`` helper (no model artifact).
Property-based coverage of dimension/determinism and chunk coverage /
aggregation lands in tasks 2.2 and 2.3.

Requirements exercised: 2.1 (fixed-dimension vector), 2.3 (documented
chunk-and-aggregate strategy), 2.4 (determinism).
"""

from __future__ import annotations

import math

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service


def _norm(vector: list[float]) -> float:
    return math.sqrt(math.fsum(c * c for c in vector))


def test_short_text_uses_single_encode_result() -> None:
    """A text within max_tokens embeds exactly as the encoder's single vector."""
    encoder = Stub_Text_Encoder(dimension=8, max_tokens=8)
    service = Embedding_Service(encoder)
    text = "python fastapi postgres"  # 3 tokens <= 8

    assert service.embed(text) == encoder.encode([text])[0]


def test_embedding_has_declared_dimension() -> None:
    """Both paths produce vectors of the encoder's declared dimension (Req 2.1)."""
    encoder = Stub_Text_Encoder(dimension=6, max_tokens=4)
    service = Embedding_Service(encoder)

    assert service.dimension == 6
    assert len(service.embed("one two three")) == 6  # single-encode path
    assert len(service.embed("a b c d e f g h i")) == 6  # chunked path


def test_long_text_matches_documented_aggregation_formula() -> None:
    """The chunked path equals L2-normalize(sum(count_i * v_i) / total) (Req 2.3)."""
    encoder = Stub_Text_Encoder(dimension=8, max_tokens=4)
    service = Embedding_Service(encoder)
    text = "a b c d e f g h i j"  # 10 tokens > 4 -> chunks of 4, 4, 2

    chunks = encoder.split_tokens(text, encoder.max_tokens)
    assert chunks == ["a b c d", "e f g h", "i j"]

    vectors = encoder.encode(chunks)
    counts = [encoder.count_tokens(chunk) for chunk in chunks]
    total = sum(counts)
    mean = [
        math.fsum(count * vector[i] for count, vector in zip(counts, vectors, strict=True)) / total
        for i in range(encoder.dimension)
    ]
    norm = _norm(mean)
    expected = [component / norm for component in mean]

    result = service.embed(text)
    assert result == expected
    assert math.isclose(_norm(result), 1.0, rel_tol=0.0, abs_tol=1e-12)


def test_embed_is_deterministic() -> None:
    """Identical text through the same encoder yields identical vectors (Req 2.4)."""
    encoder = Stub_Text_Encoder(dimension=8, max_tokens=4)
    service = Embedding_Service(encoder)
    long_text = " ".join(f"tok{i}" for i in range(20))

    assert service.embed(long_text) == service.embed(long_text)
    assert service.embed("short text") == service.embed("short text")


def test_empty_text_takes_single_encode_path() -> None:
    """Zero tokens <= max_tokens: the empty string embeds via a single encode."""
    encoder = Stub_Text_Encoder(dimension=8, max_tokens=8)
    service = Embedding_Service(encoder)

    assert service.embed("") == encoder.encode([""])[0]
