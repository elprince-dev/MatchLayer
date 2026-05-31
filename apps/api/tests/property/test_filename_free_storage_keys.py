"""Feature: phase-1-matching — Property 14.

Property 14: Storage keys never incorporate the client filename.

    *For any* client-supplied filename (including path-traversal, Unicode, or
    overlong names) accepted as a PDF or DOCX, the derived storage key matches
    ``^[0-9a-f-]{36}\\.(pdf|docx)$`` and contains no portion of the filename
    stem.

**Validates: Requirements 2.5**

This module is the universal companion to the storage-layer coverage of the
upload path (tasks 7.1 / 10.7). Where those tests drive concrete uploads, this
file asserts the *key-derivation* invariant holds across a wide, generated
input space using Hypothesis (>=100 examples), exercising the single sanctioned
key source :func:`matchlayer_api.core.storage.build_object_key` directly. No
FastAPI app, database, network, or object store is touched.

The structural fact this property rests on (Requirement 2.5, ``security.md``
"Filename sanitization — never use the user-supplied filename in any path"):
``build_object_key`` takes **only** the validated file ``extension`` —
``"pdf"`` or ``"docx"``, the magic-byte verdict from the Mime_Validator — and
returns ``f"{uuid7()}.{extension}"``. There is *no parameter* through which a
client filename could reach the key; the original filename lives only in the
display-only ``resumes.original_filename`` column. So the strongest, non-flaky
formulation of "the key never incorporates the filename" is to generate an
arbitrary client filename, derive the key *without any channel to pass it*
(because none exists), and prove two complementary things, mirroring the
two-assertion shape of ``test_mime_magic_byte_rejection.py``:

* **Shape & version (fully universal).** For *any* filename and *either*
  accepted extension, the key matches the strict UUIDv7 key regex, its stem
  parses as a :class:`uuid.UUID` with ``version == 7``, and the suffix is the
  requested extension. Because the shape is fixed regardless of the filename,
  any implementation that spliced filename content into the key (a prefix,
  suffix, sanitized stem, or path component) would break this regex — so this
  is the primary, coincidence-proof catch.

* **No distinctive filename token leaks.** The key stem is drawn from the hex
  alphabet ``[0-9a-f-]`` only, so the *only* substrings a filename could share
  with a key by chance are short hex-like runs — the "astronomically unlikely
  UUID coincidence" the design calls out. We therefore restrict the no-leak
  assertion to a filename's **distinctive tokens**: contiguous runs of length
  ``>= 3`` that contain at least one character outside the key's legal alphabet
  (hex digits, ``-``, ``.``, and the extension's own letters). Such a run can
  *never* appear in a legitimately-derived key, so its presence would be a real
  leak rather than a coincidence. Checking every length-3 window is sufficient:
  if any longer distinctive run leaked into the key, a length-3 window straddling
  its non-legal character would leak too (a substring of a substring). This keeps
  the check O(n) per source and provably non-flaky.

The filename generators deliberately include the adversarial shapes the
requirement enumerates — path-traversal (``../``), Windows separators, embedded
UUIDs, the literal ``.pdf`` / ``.docx``, Unicode (accents, CJK, an RTL
override), and overlong names — both via a curated bank and via free unicode
``st.text``. A final test asserts uniqueness across many calls (each key's stem
is a fresh time-ordered UUIDv7), so two uploads of the same file never collide
on a key.
"""

from __future__ import annotations

import re
from uuid import UUID

from hypothesis import example, given, settings
from hypothesis import strategies as st

from matchlayer_api.core.storage import ResumeKind, build_object_key

# ---------------------------------------------------------------------------
# Key-shape regexes.
# ---------------------------------------------------------------------------

# The design's documented shape for Property 14 (kept verbatim for
# traceability): a 36-char hex/hyphen stem, a dot, then the extension.
_DESIGN_KEY_RE = re.compile(r"^[0-9a-f-]{36}\.(pdf|docx)$")

# The stricter, version-aware form: the stem must be a canonical UUIDv7 string
# — eight groups with the literal ``7`` version nibble and an ``8|9|a|b``
# variant nibble — so a non-v7 (or filename-derived) stem is rejected outright.
_UUID7_KEY_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(pdf|docx)$"
)

# The exhaustive set of accepted extensions (mirrors ``ResumeKind``).
_EXTENSIONS: tuple[ResumeKind, ...] = ("pdf", "docx")

# The window length at and above which a shared run is considered a token
# rather than an incidental character coincidence.
_MIN_TOKEN_LEN = 3

# ---------------------------------------------------------------------------
# A curated bank of adversarial filenames covering every shape Requirement 2.5
# calls out: traversal, separators, embedded UUIDs, the literal accepted
# extensions, Unicode, control/RTL characters, and an overlong name.
# ---------------------------------------------------------------------------
_ADVERSARIAL_FILENAMES: list[str] = [
    "../../../../etc/passwd",
    "..\\..\\..\\Windows\\System32\\config\\SAM",
    "C:\\Users\\me\\Documents\\My Résumé.docx",
    "/var/www/uploads/resume.pdf",
    "0190aaaa-0000-7000-8000-000000000001.pdf",  # embedded UUIDv7 + .pdf
    "deadbeef-cafe-babe.docx",  # all-hex-ish stem
    ".pdf",
    ".docx",
    "résumé final (v2).PDF",
    "履歴書_2024.docx",
    "Lebenslauf\u202egpj.pdf",  # RTL-override spoof
    "name with 0123456789abcdef hex run.docx",
    "a" * 300 + ".pdf",  # overlong
    "résumé\x00.pdf",  # embedded NUL
    "..%2f..%2fsecret.pdf",  # URL-encoded traversal
    "",  # no name at all
    "noextensionatall",
]

# Free-form filenames across the full Unicode space, plus the curated bank.
_filename = st.one_of(
    st.text(min_size=0, max_size=300),
    st.sampled_from(_ADVERSARIAL_FILENAMES),
)

_extension = st.sampled_from(_EXTENSIONS)


def _path_components(filename: str) -> list[str]:
    """Split ``filename`` on both POSIX and Windows separators into parts."""
    return [part for part in filename.replace("\\", "/").split("/") if part]


def _stem_of(name: str) -> str:
    """Return ``name`` with a single trailing ``.<ext>`` removed, if present."""
    head, dot, _tail = name.rpartition(".")
    return head if dot else name


def _legal_key_chars(extension: str) -> frozenset[str]:
    """Return every character that can legitimately occur in a key.

    The UUIDv7 stem contributes ``0-9a-f`` and ``-``; the separator dot and the
    requested extension's own letters complete the set. Any filename run that
    contains a character outside this set cannot appear in a correctly-derived
    key, so such a run is a reliable leak detector immune to hex coincidence.
    """
    return frozenset("0123456789abcdef-.") | frozenset(extension)


def _leaking_token(key: str, source: str, legal: frozenset[str]) -> str | None:
    """Return the first distinctive token of ``source`` found in ``key``, else None.

    A *distinctive token* is a length-``_MIN_TOKEN_LEN`` (case-folded) window of
    ``source`` that contains at least one character outside ``legal``. Such a
    window can never be part of a legitimate key, so finding it inside ``key``
    proves a portion of the filename leaked. Scanning only length-3 windows is
    sufficient (see the module docstring) and keeps the check linear.
    """
    folded = source.casefold()
    for start in range(len(folded) - _MIN_TOKEN_LEN + 1):
        chunk = folded[start : start + _MIN_TOKEN_LEN]
        if any(ch not in legal for ch in chunk) and chunk in key:
            return chunk
    return None


@settings(max_examples=200, deadline=None)
@given(filename=_filename, extension=_extension)
@example(filename="../../../../etc/passwd", extension="pdf")
@example(filename="0190aaaa-0000-7000-8000-000000000001.pdf", extension="pdf")
@example(filename="C:\\Users\\me\\My Résumé.docx", extension="docx")
@example(filename="a" * 300 + ".pdf", extension="docx")
@example(filename="", extension="pdf")
def test_key_is_a_filename_free_uuid7_key(filename: str, extension: ResumeKind) -> None:
    """The derived key is a strict UUIDv7 ``<uuid>.<ext>`` regardless of filename.

    Property 14 (shape half): for any client-supplied filename — traversal,
    Windows path, embedded UUID, Unicode, overlong, or empty — and either
    accepted extension, ``build_object_key`` returns a key whose stem is a
    canonical UUIDv7 and whose suffix is exactly the requested extension. The
    filename has no channel into the function, so the key shape is invariant; a
    splice of any filename content would break the strict regex below.
    """
    # The filename is generated but, faithfully to the API, never passed: the
    # function exposes no parameter for it. Deriving the key here is exactly
    # what the Resume_Service does at upload time.
    key = build_object_key(extension)

    # Strict, version-aware shape: stem is a canonical UUIDv7, suffix is the ext.
    assert _UUID7_KEY_RE.match(key), key
    # The design's documented (looser) shape also holds.
    assert _DESIGN_KEY_RE.match(key), key

    stem, dot, suffix = key.rpartition(".")
    assert dot == "."
    assert suffix == extension

    # The stem parses as a real UUID whose version is exactly 7.
    parsed = UUID(stem)
    assert parsed.version == 7


@settings(max_examples=200, deadline=None)
@given(filename=_filename, extension=_extension)
@example(filename="../../../../etc/passwd", extension="pdf")
@example(filename="C:\\Users\\me\\Documents\\My Résumé.docx", extension="docx")
@example(filename="Lebenslauf\u202egpj.pdf", extension="pdf")
@example(filename="履歴書_2024.docx", extension="docx")
@example(filename="résumé final (v2).PDF", extension="pdf")
def test_key_shares_no_distinctive_token_with_filename(
    filename: str, extension: ResumeKind
) -> None:
    """No distinctive run of the filename (or its path parts) appears in the key.

    Property 14 (no-leak half): the key contains no portion of the filename
    stem. Restricting the claim to *distinctive tokens* — length-3 runs holding
    a character outside the key's legal alphabet — makes it immune to the
    incidental hex-run coincidence the design acknowledges, while still proving
    that no recognizable fragment of the filename, its stem, or any of its
    path components reached the key.
    """
    key = build_object_key(extension)
    legal = _legal_key_chars(extension)

    # Check the full filename, its stem, and every path component plus each
    # component's own stem — every surface a naive sanitizer might draw from.
    sources: set[str] = {filename, _stem_of(filename)}
    for component in _path_components(filename):
        sources.add(component)
        sources.add(_stem_of(component))

    for source in sources:
        leaked = _leaking_token(key, source, legal)
        assert leaked is None, f"filename token {leaked!r} leaked into key {key!r}"


@settings(max_examples=50, deadline=None)
@given(extension=_extension, count=st.integers(min_value=2, max_value=64))
def test_keys_are_unique_across_calls(extension: ResumeKind, count: int) -> None:
    """Repeated derivations yield distinct keys (a fresh UUIDv7 stem each time).

    Two uploads of the very same file must never collide on a storage key:
    each key's stem is a freshly generated, time-ordered UUIDv7, so a batch of
    ``count`` derivations for the same extension contains ``count`` distinct
    keys.
    """
    keys = [build_object_key(extension) for _ in range(count)]
    assert len(set(keys)) == count
