"""Feature: phase-1-matching — Property 13.

Property 13: Non-PDF/DOCX bytes are rejected by magic-byte detection.

    *For any* byte payload whose true content is neither a PDF nor a
    DOCX/OOXML container, ``Mime_Validator.detect`` returns ``None``
    (driving a 415), regardless of any declared ``Content-Type`` header or
    filename extension.

**Validates: Requirements 2.3**

This is the universal companion to the concrete-example coverage in
``tests/unit/test_mime.py``. Where that file pins down specific accept/
reject verdicts, this module asserts the property holds across a wide,
generated input space using Hypothesis (≥100 examples).

Two complementary assertions encode the property robustly:

* **Totality.** For *any* bytes at all, :func:`matchlayer_api.core.mime.detect`
  returns one of ``{None, "pdf", "docx"}`` and never raises. This holds
  even for the astronomically-improbable random buffer that happens to
  carry a real PDF/DOCX signature — such a buffer would simply return its
  true accepted literal, which is still inside the allowed set. Phrasing
  the universal claim this way means it can never produce a false failure.

* **Rejection.** For bytes whose content is *definitively* neither a PDF
  (no ``%PDF`` header) nor an OOXML/ZIP container (no ``PK`` header) — plus
  a curated bank of real non-accepted formats including a genuine plain
  ZIP — :func:`detect` returns ``None``. ``filetype`` recognises PDF only
  by a leading ``%PDF`` and the DOCX/ZIP family only by a leading ``PK``,
  so excluding those two signatures guarantees the payload's true type is
  outside the accepted pair without re-implementing the detector.

The detector only ever sees raw bytes — it takes no ``Content-Type`` and
no filename — so the "regardless of declared header or extension" clause
of the property is satisfied by construction: there is no spoofable
metadata channel to ignore.
"""

from __future__ import annotations

import io
import zipfile

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.core import mime

# The literals ``detect`` is permitted to return on the accept path. Any
# other return value (or an exception) is a totality violation.
_ACCEPTED: frozenset[str | None] = frozenset({None, "pdf", "docx"})

# The two magic-byte signatures ``filetype`` keys the accepted types on:
# PDF documents begin with ``%PDF`` and every OOXML/ZIP container begins
# with the local-file-header signature ``PK\x03\x04`` (prefix ``PK``).
# Excluding both from the generated head guarantees a payload cannot be
# classified as ``pdf`` or ``docx``.
_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK"


def _plain_zip() -> bytes:
    """Return a real plain ZIP archive that is *not* an OOXML ``.docx``.

    PK-prefixed and structurally valid, so it exercises the strongest
    rejection case: a genuine archive that ``filetype`` recognises as
    ``zip`` (never ``docx``) and which :func:`detect` must therefore
    reject with ``None``.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notes.txt", "just a plain zip, not a word document")
    return buf.getvalue()


# A bank of real headers/payloads whose true type is none of PDF/DOCX.
# Includes the plain ZIP (PK-prefixed but not OOXML) as the adversarial
# near-miss for the DOCX path.
_KNOWN_NON_ACCEPTED: list[bytes] = [
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,  # PNG
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 16,  # JPEG
    b"GIF89a" + b"\x00" * 16,  # GIF
    b"\x7fELF" + b"\x00" * 16,  # ELF executable
    b"\x1f\x8b\x08" + b"\x00" * 16,  # gzip
    b"%!PS-Adobe-3.0\n" + b"\x00" * 8,  # PostScript (not PDF)
    b"This is just some resume-looking plain text, not a document file.",
    b"<html><body>not a resume file</body></html>",
    b"",  # empty buffer
    _plain_zip(),
]


def _has_no_accepted_magic(data: bytes) -> bool:
    """True when ``data`` carries neither the PDF nor the ZIP signature."""
    return not data.startswith(_PDF_MAGIC) and not data.startswith(_ZIP_MAGIC)


# Bytes guaranteed not to be PDF or DOCX: arbitrary binary with the two
# accepted signatures filtered out of the leading position. The filter
# rejects only buffers that *start* with ``%PDF`` or ``PK``, which is
# vanishingly rare in random binary, so Hypothesis is never starved.
_non_magic_bytes = st.binary(min_size=0, max_size=4096).filter(_has_no_accepted_magic)

# The rejection strategy: either freshly generated non-magic binary or a
# sample from the curated bank of real non-accepted formats.
_non_pdf_docx_bytes = st.one_of(_non_magic_bytes, st.sampled_from(_KNOWN_NON_ACCEPTED))


@settings(max_examples=200, deadline=None)
@given(data=st.binary(min_size=0, max_size=4096))
def test_detect_is_total_over_arbitrary_bytes(data: bytes) -> None:
    """detect() returns None | "pdf" | "docx" for any bytes and never raises.

    The totality half of Property 13: across the full arbitrary-byte
    input space, the verdict is always inside the accepted set. A random
    buffer that coincidentally bears a real signature is allowed to return
    its true literal — the claim still holds.
    """
    result = mime.detect(data)
    assert result in _ACCEPTED


@settings(max_examples=200, deadline=None)
@given(data=_non_pdf_docx_bytes)
def test_non_pdf_docx_bytes_are_rejected(data: bytes) -> None:
    """Bytes that are neither PDF nor OOXML/ZIP detect as None (→ 415).

    The rejection half of Property 13: for any payload whose true content
    is neither a PDF (no ``%PDF`` header) nor a DOCX/OOXML container (a
    plain ZIP that is not an OOXML word document), the magic-byte
    validator returns ``None``, which the Resume_Service maps to HTTP 415
    ``unsupported_media_type``.
    """
    assert mime.detect(data) is None
