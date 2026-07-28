"""Feature: phase-2-nlp-embeddings — Property 1.

Property 1: Embeddings have the declared dimension and are deterministic.

    *For any* input text (including texts longer than the encoder's
    ``max_tokens``), ``Embedding_Service.embed(text)`` returns a vector whose
    length equals the encoder's declared dimension, and calling it twice with
    identical text under the same encoder returns identical vectors.

**Validates: Requirements 2.1, 2.4**

The :class:`~matchlayer_api.scoring.embedding.Embedding_Service` has exactly
two code paths: a single ``encode`` call when the text fits within
``max_tokens``, and the documented chunk-and-aggregate strategy (non-
overlapping tokenizer chunks -> token-count-weighted mean -> L2-normalize)
otherwise. Requirement 2.1 demands a *fixed-dimension* float vector from
either path; Requirement 2.4 demands determinism — identical input text under
an identical encoder produces an identical Embedding.

Both clauses are asserted here across a generated input space that
deliberately exercises both paths: the deterministic hash-based
:class:`Stub_Text_Encoder` (design D6 — no model artifact in property tests)
is constructed with small, *generated* ``dimension`` and ``max_tokens``
values, and the text strategy mixes fully arbitrary text (including empty and
whitespace-only strings, which take the single-encode path) with synthetic
many-token documents guaranteed to exceed ``max_tokens`` (forcing the
chunk-and-aggregate path).

Determinism is asserted in two escalating forms:

* **Repeated calls on one service.** ``embed(text) == embed(text)`` on the
  same :class:`Embedding_Service` instance — the literal "calling it twice"
  clause of Property 1.
* **Independently constructed instances.** A second service over a second,
  separately-constructed encoder with identical parameters produces the same
  vector. This is Requirement 2.4's real-world shape — "identical input text
  under an identical Embedding_Model name and revision" — where two processes
  (or a restart) each build their own encoder from the same pinned model.

Vector equality is exact (``==`` on the float lists) on purpose: the service
is a deterministic function running the identical code path on identical
inputs, so the bits must match — any drift is precisely the non-determinism
Requirement 2.4 forbids.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from _stub_encoder import Stub_Text_Encoder
from matchlayer_api.scoring.embedding import Embedding_Service

# Encoder parameters are generated, not fixed, so the property holds for any
# declared dimension and any chunking threshold — small ranges keep 100+
# Hypothesis examples in the millisecond budget the design calls for.
_dimension = st.integers(min_value=1, max_value=16)
_max_tokens = st.integers(min_value=1, max_value=8)

# A whitespace-token word: no spaces, so token counts under the stub's
# whitespace tokenizer are exactly the list lengths the strategies choose.
_word = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),  # printable, no whitespace
    min_size=1,
    max_size=10,
)

# Fully arbitrary text — covers the single-encode path, including the empty
# string and whitespace-only inputs, plus whatever token counts arise.
_arbitrary_text = st.text(min_size=0, max_size=200)

# A many-token document: 9..40 words always exceeds the largest generated
# max_tokens (8), guaranteeing the chunk-and-aggregate path is exercised
# ("including texts longer than the encoder's max_tokens").
_long_text = st.lists(_word, min_size=9, max_size=40).map(" ".join)

_document = st.one_of(_arbitrary_text, _long_text)


@settings(max_examples=200, deadline=None)
@given(text=_document, dimension=_dimension, max_tokens=_max_tokens)
def test_embedding_has_declared_dimension(text: str, dimension: int, max_tokens: int) -> None:
    """``embed`` returns a vector of exactly the encoder's declared dimension.

    Requirement 2.1's fixed-dimension clause, for any text and any encoder
    configuration — whether the text takes the single-encode path or the
    chunk-and-aggregate path.
    """
    encoder = Stub_Text_Encoder(dimension=dimension, max_tokens=max_tokens)
    service = Embedding_Service(encoder)

    vector = service.embed(text)

    assert len(vector) == encoder.dimension
    assert service.dimension == encoder.dimension
    assert all(isinstance(component, float) for component in vector)


@settings(max_examples=200, deadline=None)
@given(text=_document, dimension=_dimension, max_tokens=_max_tokens)
def test_embedding_is_deterministic(text: str, dimension: int, max_tokens: int) -> None:
    """Identical text under an identical encoder yields identical vectors.

    Requirement 2.4, asserted both for repeated calls on one service and for
    independently constructed service/encoder instances built with identical
    parameters (the "identical model name and revision" shape).
    """
    encoder = Stub_Text_Encoder(dimension=dimension, max_tokens=max_tokens)
    service = Embedding_Service(encoder)

    first = service.embed(text)
    second = service.embed(text)
    # Repeated calls on the same service: exact, bit-for-bit equality.
    assert first == second

    # A separately-constructed encoder + service with identical parameters
    # must reproduce the same vector — determinism does not depend on object
    # identity, only on the input text and the encoder configuration.
    sibling_service = Embedding_Service(
        Stub_Text_Encoder(dimension=dimension, max_tokens=max_tokens)
    )
    assert sibling_service.embed(text) == first
