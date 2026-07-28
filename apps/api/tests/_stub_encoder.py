"""Deterministic hash-based stub Text_Encoder (phase-2-nlp-embeddings, task 2.1).

Test helper implementing the :class:`~matchlayer_api.scoring.embedding.Text_Encoder`
protocol without any model artifact, so the ``Embedding_Service`` chunking and
aggregation logic is drivable by fast property tests (design D6: "a test stub
encoder drives property tests without the model").

Importable from any test module under ``apps/api/tests/`` (the root
``tests/conftest.py`` puts this directory on ``sys.path``)::

    from _stub_encoder import Stub_Text_Encoder

Behavior:

* **Tokenization** is whitespace splitting (``str.split``): deterministic,
  fast, and easy for Hypothesis strategies to reason about. ``count_tokens``
  is the whitespace-token count; ``split_tokens`` groups consecutive tokens
  into chunks of at most ``max_tokens`` tokens joined by single spaces, so the
  chunks are non-overlapping and cover every token in order.
* **Encoding** derives each vector from the SHA-256 digest of the input text:
  digest bytes map affinely onto ``[-1, 1]`` and the vector is L2-normalized,
  honoring the protocol's normalization contract. SHA-256 makes the encoder
  deterministic and (practically) injective per distinct input, and no digest
  byte can map to exactly ``0.0`` (that would require the non-integer byte
  value 127.5), so the raw vector always has non-zero magnitude and
  normalization is always defined.
* ``max_tokens`` defaults small (8) so property tests hit the chunking path
  with short generated documents instead of needing 256-token inputs.
"""

from __future__ import annotations

import hashlib
import math
from typing import Final


class Stub_Text_Encoder:  # noqa: N801 -- matches the design's underscored component naming.
    """Deterministic, model-free ``Text_Encoder`` for property tests."""

    def __init__(self, *, dimension: int = 8, max_tokens: int = 8) -> None:
        if dimension < 1:
            msg = f"dimension must be >= 1, got {dimension}"
            raise ValueError(msg)
        if max_tokens < 1:
            msg = f"max_tokens must be >= 1, got {max_tokens}"
            raise ValueError(msg)
        self._dimension: Final[int] = dimension
        self._max_tokens: Final[int] = max_tokens

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    def count_tokens(self, text: str) -> int:
        """Whitespace-token count of ``text``."""
        return len(text.split())

    def split_tokens(self, text: str, max_tokens: int) -> list[str]:
        """Consecutive, non-overlapping chunks of at most ``max_tokens`` tokens.

        Every whitespace token of ``text`` appears in exactly one chunk, in
        the original order, so the chunks jointly cover the full document.
        """
        tokens = text.split()
        return [
            " ".join(tokens[start : start + max_tokens])
            for start in range(0, len(tokens), max_tokens)
        ]

    def encode(self, texts: list[str]) -> list[list[float]]:
        """One L2-normalized, SHA-256-derived vector per input text."""
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        components: list[float] = []
        data = text.encode("utf-8")
        counter = 0
        while len(components) < self._dimension:
            digest = hashlib.sha256(data + counter.to_bytes(4, "big")).digest()
            components.extend((byte / 255.0) * 2.0 - 1.0 for byte in digest)
            counter += 1
        components = components[: self._dimension]
        norm = math.sqrt(math.fsum(c * c for c in components))
        # norm > 0 always: a zero component would need the digest byte 127.5.
        return [c / norm for c in components]
