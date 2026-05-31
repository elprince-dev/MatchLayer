"""Unit tests for ``core/mime.py`` (the ``Mime_Validator``).

Concrete-example coverage for :func:`matchlayer_api.core.mime.detect`,
the magic-byte MIME validator behind Requirement 2.3. These tests pin
down the specific accept/reject verdicts; the universal "non-PDF/DOCX
bytes are rejected" property is owned separately by the task 7.3 property
test.

The key security assertions here:

* A real PDF (``%PDF`` header) detects as ``"pdf"``.
* A genuine OOXML ``.docx`` (a ZIP whose ``[Content_Types].xml`` marks it
  a word-processing document) detects as ``"docx"`` — *not* as a bare
  archive.
* A plain ZIP, an image, free text, and an empty buffer all return
  :data:`None`, so the service maps them to HTTP 415.
* The verdict is driven purely by content: a PDF whose bytes are handed
  over still detects as ``"pdf"`` regardless of any (absent) header or
  extension, because :func:`detect` only ever sees raw bytes.

References:
* Requirement 2.3 (magic-byte MIME validation, ignore Content-Type).
* Design §"Mime_Validator".
"""

from __future__ import annotations

import io
import zipfile

from matchlayer_api.core import mime


def _minimal_pdf() -> bytes:
    """Return a tiny but structurally valid PDF (starts with ``%PDF``)."""
    return (
        b"%PDF-1.4\n"
        b"%\xe2\xe3\xcf\xd3\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n"
        b"%%EOF\n"
    )


def _minimal_docx() -> bytes:
    """Return a minimal real ``.docx`` (an OOXML ZIP container).

    The ``[Content_Types].xml`` override marking ``/word/document.xml`` as
    the wordprocessingml main document is what lets :mod:`filetype`
    distinguish this from a plain ZIP.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def _plain_zip() -> bytes:
    """Return a plain ZIP archive that is *not* a DOCX."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("hello.txt", "world")
    return buf.getvalue()


def test_detects_pdf_by_magic_bytes() -> None:
    assert mime.detect(_minimal_pdf()) == "pdf"


def test_detects_docx_as_docx_not_zip() -> None:
    # A genuine .docx must be detected as "docx", not rejected as a bare
    # archive — the design calls this out explicitly.
    assert mime.detect(_minimal_docx()) == "docx"


def test_plain_zip_is_rejected() -> None:
    # A ZIP that is not an OOXML document is not an accepted type.
    assert mime.detect(_plain_zip()) is None


def test_png_image_is_rejected() -> None:
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert mime.detect(png_header) is None


def test_plain_text_is_rejected() -> None:
    assert mime.detect(b"this is just some resume-looking text, not a file") is None


def test_empty_buffer_is_rejected() -> None:
    assert mime.detect(b"") is None


def test_pdf_extension_spoof_is_ignored() -> None:
    # Bytes that are really a PNG must be rejected even though a caller
    # might have labelled the upload ".pdf" / "application/pdf" — detect()
    # only ever sees the bytes, never the (spoofable) metadata.
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    assert mime.detect(fake) is None
