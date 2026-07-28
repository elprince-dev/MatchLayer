"""Feature: phase-2-nlp-embeddings — Property 2.

Property 2: Chunking covers the full document and aggregation follows the
documented formula.

    *For any* text whose token count exceeds ``max_tokens``, the chunker
    produces consecutive chunks that each contain at most ``max_tokens``
    tokens and whose concatenation covers the full text, and the returned
    embedding equals the documented aggregation (token-count-weighted mean of
    chunk vectors, L2-normalized) recomputed independently from the same
    chunks.

**Validates: Requirements 2.3**

Requirement 2.3 demands a *documented, deterministic* chunking-and-aggregation
strategy so a long document's embedding represents the whole document rather
than its truncated prefix. The :mod:`matchlayer_api.scoring.embedding` module
docstring documents that strategy precisely:

1. ``split_tokens(text, max_tokens)`` → consecutive, non-overlapping chunks of
   at most ``max_tokens`` tokens whose concatenation covers the full text;
2. one ``encode`` call over all chunks;
3. token-count-weighted mean ``sum_i(count_i * v_i) / total_tokens`` with each
   component summed via ``math.fsum``;
4. L2-normalization of the mean.

Both halves of the property are asserted here against the deterministic
hash-based :class:`Stub_Text_Encoder` (design D6 — no model artifact in
property tests):

* **Coverage.** The stub tokenizes by whitespace, so token identity is
  directly observable: concatenating the token lists of the chunks must
  reproduce ``text.split()`` exactly — same tokens, same order, no token
  dropped or repeated (non-overlapping + consecutive + full coverage in one
  equality), with every chunk within the ``max_tokens`` budget.
* **Aggregation formula.** The expected vector is recomputed *independently*
  in the test from the same chunks and the encoder's own outputs, following
  only the documented formula. Equality with ``Embedding_Service.embed`` is
  exact (``==`` on the float lists) on purpose: ``math.fsum`` is
  exactly-rounded, so any implementation that follows the documented formula
  must produce these precise bits — drift would mean the implementation and
  its documentation disagree, which is exactly what Requirement 2.3 forbids.

The strategies generate the encoder configuration (``dimension``,
``max_tokens``) and then a document guaranteed to exceed ``max_tokens``
whitespace tokens, so every example takes the chunk-and-aggregate path (texts
that fit are Property 1's territory).
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service

# A whitespace-token word: no whitespace characters, so token counts under the
# stub's whitespace tokenizer are exactly the list lengths the strategy picks.
_word = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),  # printable, no whitespace
    min_size=1,
    max_size=10,
)


@st.composite
def _chunking_case(draw: st.DrawFn) -> tuple[str, int, int]:
    """A ``(text, dimension, max_tokens)`` triple where the text must chunk.

    ``max_tokens`` is drawn first and the word count is drawn strictly above
    it, so ``count_tokens(text) > max_tokens`` holds for every example and the
    chunk-and-aggregate path is always exercised.
    """
    dimension = draw(st.integers(min_value=1, max_value=16))
    max_tokens = draw(st.integers(min_value=1, max_value=8))
    words = draw(st.lists(_word, min_size=max_tokens + 1, max_size=max_tokens + 32))
    return " ".join(words), dimension, max_tokens


@settings(max_examples=200, deadline=None)
@given(case=_chunking_case())
def test_chunks_cover_the_full_document(case: tuple[str, int, int]) -> None:
    """Chunks are consecutive, non-overlapping, bounded, and jointly complete.

    The coverage half of Property 2: each chunk holds at most ``max_tokens``
    tokens, and concatenating the chunks' token sequences reproduces the
    document's token sequence exactly — every token appears exactly once, in
    the original order.
    """
    text, dimension, max_tokens = case
    encoder = Stub_Text_Encoder(dimension=dimension, max_tokens=max_tokens)
    assert encoder.count_tokens(text) > encoder.max_tokens  # the chunking premise

    chunks = encoder.split_tokens(text, encoder.max_tokens)

    assert len(chunks) >= 2  # more tokens than max_tokens ⇒ more than one chunk
    for chunk in chunks:
        count = encoder.count_tokens(chunk)
        assert 1 <= count <= max_tokens

    # One equality captures completeness, order, and non-overlap: the chunks'
    # concatenated token streams must be the document's token stream.
    covered_tokens = [token for chunk in chunks for token in chunk.split()]
    assert covered_tokens == text.split()


@settings(max_examples=200, deadline=None)
@given(case=_chunking_case())
def test_embedding_equals_documented_aggregation(case: tuple[str, int, int]) -> None:
    """``embed`` returns exactly the documented aggregation of the chunks.

    The formula half of Property 2: recomputing
    ``L2-normalize(sum_i(count_i * v_i) / total_tokens)`` independently from
    the encoder's own chunk vectors reproduces ``Embedding_Service.embed``
    bit-for-bit.
    """
    text, dimension, max_tokens = case
    encoder = Stub_Text_Encoder(dimension=dimension, max_tokens=max_tokens)
    service = Embedding_Service(encoder)

    actual = service.embed(text)

    # Independent recomputation from the same chunks, per the documented
    # strategy: chunk → encode → token-count-weighted mean → L2-normalize.
    chunks = encoder.split_tokens(text, encoder.max_tokens)
    vectors = encoder.encode(chunks)
    counts = [encoder.count_tokens(chunk) for chunk in chunks]
    total_tokens = sum(counts)
    assert total_tokens == encoder.count_tokens(text)  # nothing dropped, nothing repeated

    weighted_mean = [
        math.fsum(count * vector[index] for count, vector in zip(counts, vectors, strict=True))
        / total_tokens
        for index in range(dimension)
    ]
    norm = math.sqrt(math.fsum(component * component for component in weighted_mean))
    expected = weighted_mean if norm == 0.0 else [component / norm for component in weighted_mean]

    assert actual == expected
    assert len(actual) == dimension
    # The aggregate is unit-magnitude like a single-chunk embedding (barring
    # the degenerate zero-vector case the module docstring defers to the
    # Semantic_Scorer).
    if norm != 0.0:
        magnitude = math.sqrt(math.fsum(component * component for component in actual))
        assert math.isclose(magnitude, 1.0, rel_tol=1e-9)
