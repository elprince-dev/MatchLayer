"""PBT-1: Argon2 hash/verify is a sound roundtrip.

Validates: Requirements 1.7, 1.10, 5.10.
"""

from __future__ import annotations

import unicodedata

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.core.security.passwords import hash_password, verify_password

# Strategy: valid passwords (>= 12 chars, printable unicode).
_valid_password = st.text(
    alphabet=st.characters(categories=("L", "M", "N", "P", "S", "Z")),
    min_size=12,
    max_size=64,
)


@settings(deadline=None, max_examples=50)
@given(password=_valid_password)
@example(password="a" * 12)
@example(password="café" * 3)  # NFKC edge: precomposed form
@example(password="caf\u0065\u0301" * 3)  # NFKC edge: decomposed form
def test_hash_verify_roundtrip(password: str) -> None:
    """For any p with len(p) >= 12, verify(hash(p), p) is True."""
    hashed = hash_password(password)
    matches, _ = verify_password(hashed, password)
    assert matches, f"verify_password failed for password of length {len(password)}"


@settings(deadline=None, max_examples=50)
@given(p=_valid_password, q=_valid_password)
def test_hash_verify_distinct(p: str, q: str) -> None:
    """For any pair p != q, verify(hash(p), q) is False."""
    # Normalize both to NFKC to check logical distinctness.
    if unicodedata.normalize("NFKC", p) == unicodedata.normalize("NFKC", q):
        return  # Same logical password after normalization — skip.
    hashed = hash_password(p)
    matches, _ = verify_password(hashed, q)
    assert not matches, "verify_password should reject a different password"
