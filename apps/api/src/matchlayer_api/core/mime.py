"""Server-side MIME validation by magic bytes (the ``Mime_Validator``).

Determines a file's *true* media type from its leading bytes, independent
of the client-supplied ``Content-Type`` header and the filename
extension. This is the security control behind Requirement 2.3 and the
``security.md`` file-upload rule "Server-side MIME validation via magic
bytes ... not just the ``Content-Type`` header" — a spoofed header or a
``.pdf`` extension on a ``.exe`` must never let a non-PDF/DOCX file
through.

Detection uses the pure-Python :mod:`filetype` library (chosen over
``python-magic`` to avoid the ``libmagic`` system dependency; ``security.md``
permits either). For the two accepted types:

* **PDF** is recognised by its ``%PDF`` header.
* **DOCX** is an OOXML ZIP container; :mod:`filetype` inspects the
  archive's ``[Content_Types].xml`` and distinguishes a real
  ``.docx`` from a plain ``.zip``. A bare ZIP therefore detects as
  ``zip`` (rejected here), not ``docx`` — which is exactly what we want,
  since only genuine word-processing documents are accepted.

Anything else — a plain ZIP, an image, an executable, free text, or an
empty buffer — yields :data:`None`, which the ``Resume_Service`` maps to
HTTP 415 ``unsupported_media_type``.

This module is import-bounded to its single concern: it reads raw bytes
and returns a verdict. It performs no I/O, no logging (the bytes are
Restricted PII per ``security.md`` and must never be logged), and never
trusts caller-supplied metadata.

Design reference: §"Mime_Validator".
Requirements covered: 2.3.
"""

from __future__ import annotations

from typing import Literal

# ``filetype`` ships no ``py.typed`` marker, so mypy cannot see its types
# and treats the import as untyped. We confine the resulting ``Any`` to
# this module and never return it directly (see ``detect``).
import filetype  # type: ignore[import-untyped]

# The two media types this application accepts, keyed by the extension
# string :mod:`filetype` reports for each. Magic-byte detection is the
# single source of truth — the client ``Content-Type`` and filename
# extension are deliberately ignored.
_ACCEPTED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "docx"})


def detect(data: bytes) -> Literal["pdf", "docx"] | None:
    """Return the true media type of ``data`` from its magic bytes.

    Args:
        data: The raw uploaded file bytes. Treated as Restricted PII —
            never logged or echoed.

    Returns:
        ``"pdf"`` if the leading bytes identify a PDF document,
        ``"docx"`` if they identify an OOXML word-processing document,
        or :data:`None` for any other content (including a plain ZIP,
        an unrecognised type, or an empty buffer). The caller turns
        :data:`None` into an HTTP 415 ``unsupported_media_type``.
    """
    # ``filetype.guess`` returns a match object (with ``.extension`` /
    # ``.mime``) or ``None`` when it recognises nothing. It reads only a
    # small header slice, so passing the full buffer is cheap.
    match = filetype.guess(data)
    if match is None:
        return None

    # Pull the extension into a narrowed local rather than returning the
    # library's ``Any``-typed attribute, so the function's return value is
    # one of our own string literals (keeps mypy --strict happy under
    # ``warn_return_any``).
    extension = str(match.extension)
    if extension not in _ACCEPTED_EXTENSIONS:
        return None
    if extension == "pdf":
        return "pdf"
    return "docx"
