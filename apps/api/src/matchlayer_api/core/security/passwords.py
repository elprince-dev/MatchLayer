"""Password hashing, verification, and blocklist enforcement.

This is the ONLY module in the API that imports ``argon2-cffi``.
Import-boundary enforced by ``tests/unit/test_import_boundaries.py``.

Design reference: Password Handling §8.1-§8.5.

Surface
-------
* :data:`MIN_PASSWORD_LENGTH` — the policy floor (12 codepoints).
* :data:`DUMMY_HASH` — precomputed Argon2id hash used by
  ``Auth_Service.authenticate`` on the unknown-email path so its
  wall-clock latency matches the known-email-wrong-password path
  (§8.3, Requirement 2.4).
* :class:`PasswordTooShortError` — raised by :func:`hash_password`
  when the submitted plaintext has fewer than
  :data:`MIN_PASSWORD_LENGTH` codepoints. Per §8.5 the count is
  taken **before** NFKC normalization so a single ligature glyph
  that NFKC-expands cannot bypass the floor.
* :func:`hash_password` — Argon2id with the §8.1 parameters,
  applied to the NFKC-normalized plaintext, returning a PHC string
  for direct storage in ``users.password_hash``.
* :func:`verify_password` — verifies a plaintext against a stored
  PHC hash and reports whether the stored hash is still under
  current parameters (so ``Auth_Service`` can transparently re-hash
  on a successful login per §8.2).
* :func:`is_blocked` — top-1000 common-password gate, NFKC-aware,
  ``O(log n)`` via :func:`bisect.bisect_left` over the sorted
  ``password_blocklist.txt`` file (§8.4).
"""

from __future__ import annotations

import bisect
import unicodedata
from pathlib import Path

from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import VerifyMismatchError

# ---------------------------------------------------------------------------
# Argon2id parameters per Password Handling §8.1.
# These match argon2-cffi v23+'s :class:`PasswordHasher` defaults; pinning
# them explicitly makes the contract auditable and survives an upstream
# default change without silently shifting the cost on us.
# ---------------------------------------------------------------------------
_hasher = _Argon2Hasher(
    time_cost=1,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# Precomputed dummy hash for the unknown-email timing-defense path (§8.3).
# Computing this once at import time means the unknown-email branch in
# Auth_Service.authenticate performs the same Argon2id work the
# known-email-wrong-password branch does, without paying the hash cost on
# every unknown-email request.
DUMMY_HASH: str = _hasher.hash("dummy-password-never-used-in-production")

# Load the sorted blocklist once at import (§8.4). The on-disk file is
# already lower-cased, NFKC-normalized, deduplicated, and lexicographically
# sorted so we can use ``bisect`` directly without sorting in Python.
_BLOCKLIST_PATH = Path(__file__).parent / "password_blocklist.txt"
_BLOCKLIST: list[str] = [
    line
    for line in _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
]

#: Minimum plaintext password length, in **pre-NFKC** codepoints
#: (Requirement 1.4 + §8.5).
MIN_PASSWORD_LENGTH: int = 12


class PasswordTooShortError(ValueError):
    """Raised by :func:`hash_password` when the submitted plaintext has
    fewer than :data:`MIN_PASSWORD_LENGTH` codepoints (counted before
    NFKC normalization per §8.5).

    Auth_Service maps this to HTTP 422 with a ``detail`` that names the
    minimum length per Requirement 1.4. The submitted value is never
    echoed in the error envelope.
    """


def _normalize(plaintext: str) -> str:
    """NFKC-normalize a password for hashing, verification, or blocklist
    membership (§8.5).

    NFKC (Normalization Form Compatibility Composition) ensures that the
    same logical password — e.g., ``é`` typed as U+00E9 vs. as
    ``e`` + U+0301 — collapses to the same byte sequence. Without
    normalization a user who registers with one input method and logs in
    with another could be locked out of their own account.
    """
    return unicodedata.normalize("NFKC", plaintext)


def is_blocked(plaintext: str) -> bool:
    """Return ``True`` if the password is in the top-1000 blocklist.

    The submitted value is NFKC-normalized and lower-cased to match the
    on-disk representation produced at file-generation time. Lookup is
    ``O(log n)`` via :func:`bisect.bisect_left` over the sorted in-memory
    list (§8.4).
    """
    normalized = _normalize(plaintext).lower()
    idx = bisect.bisect_left(_BLOCKLIST, normalized)
    return idx < len(_BLOCKLIST) and _BLOCKLIST[idx] == normalized


def hash_password(plaintext: str) -> str:
    """Hash a password with Argon2id and return a PHC-format string
    suitable for direct storage in ``users.password_hash``.

    Enforces the ≥ :data:`MIN_PASSWORD_LENGTH`-codepoint floor against
    the **pre-NFKC** plaintext (§8.5) so a single combining glyph that
    NFKC-expands cannot bypass the gate. Raises
    :class:`PasswordTooShortError` on too-short input; the caller is
    responsible for translating that into the user-visible HTTP 422
    envelope per Requirement 1.4.

    The blocklist (Requirement 1.5) is *not* enforced here — callers
    consult :func:`is_blocked` separately so the error envelope can
    distinguish "too short" from "common password" before paying the
    Argon2id cost on a request that will be rejected anyway.
    """
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(_normalize(plaintext))


def verify_password(stored: str, plaintext: str) -> tuple[bool, bool]:
    """Verify a plaintext against a stored PHC hash.

    Returns a ``(matches, needs_rehash)`` tuple:

    * ``matches`` — ``True`` iff the plaintext, NFKC-normalized,
      verifies against the stored hash.
    * ``needs_rehash`` — ``True`` when the stored hash's embedded
      parameters are below the currently-configured policy and the
      caller should re-hash with :func:`hash_password` and persist
      the new value (§8.2). Always ``False`` on a mismatch.

    Performs no length check by design: the unknown-email branch in
    ``Auth_Service.authenticate`` calls this with :data:`DUMMY_HASH`
    and a possibly-too-short submitted password, and an early return
    based on length would re-introduce the timing oracle the dummy
    hash exists to close (§8.3, Requirement 2.4).
    """
    try:
        _hasher.verify(stored, _normalize(plaintext))
    except VerifyMismatchError:
        return False, False
    needs_rehash = _hasher.check_needs_rehash(stored)
    return True, needs_rehash
