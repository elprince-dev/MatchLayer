"""Feature: phase-1-matching — Property 16.

Property 16: Extraction fails soft

    *For any* file bytes that cannot yield at least one non-whitespace
    character (corrupt, empty, whitespace-only, or timing out), the resume's
    ``extraction_status`` is set to ``failed``, ``extracted_text`` is left
    null, and the originating ``POST /api/v1/resumes`` request does not return
    a 5xx solely because of the extraction outcome.

**Validates: Requirements 3.5**

The fail-soft guarantee lives in
:func:`matchlayer_api.services.extraction.extract`: it converts *any* input —
garbage, truncated, empty, whitespace-only, slow, or a genuinely valid
document — into a value (:class:`ExtractionOutcome`) rather than an exception.
A failed extraction surfaces as ``status="failed"`` with ``text``/``char_count``
null and a ``failure_category`` set, which the ``Resume_Service`` maps onto
``extraction_status='failed'`` while still returning the upload's ``201`` (no
5xx). This module asserts that contract across a wide, generated input space
using Hypothesis (>=100 examples).

The property has two halves, both encoded below:

* **No-raise (the core of the property).** Calling ``extract(...)`` for *any*
  bytes and either ``kind`` returns normally — it yields an
  :class:`ExtractionOutcome` rather than propagating a parser or timeout
  error. We deliberately do *not* wrap the call in a swallowing ``try``: if
  any input makes ``extract`` raise, that is a real fail-soft defect and the
  test must surface the offending counterexample, not hide it. (``extract``
  may still propagate :class:`asyncio.CancelledError` on task cancellation —
  we never cancel here, so that branch is out of scope.)

* **Well-formedness.** The returned outcome is internally consistent:
  ``status`` is one of ``{"succeeded", "failed"}``; a failed outcome carries
  ``text is None``, ``char_count is None``, and a ``failure_category`` from the
  defined vocabulary; a succeeded outcome carries a ``str`` ``text`` with
  ``char_count == len(text)`` and no ``failure_category``.

Most random byte payloads parse to ``corrupt_document`` (or ``empty_text`` for
empty/whitespace-only draws) — that domination is *expected* and is itself the
demonstration of fail-soft. A dedicated valid-DOCX case keeps the ``succeeded``
half of the well-formedness clause non-vacuous, and a small-timeout case
exercises the ``extraction_timeout`` branch.

``extract`` is ``async``; following the suite's established pattern
(``tests/property/test_extraction_truncation_counting.py``,
``tests/property/test_rate_limit_window.py``) the coroutine is driven inside an
:class:`asyncio.Runner` per example rather than relying on an outer event loop,
so the loop is closed deterministically and no ``ResourceWarning`` leaks under
the suite's ``filterwarnings = ["error"]``.
"""

from __future__ import annotations

import asyncio
import string
from collections.abc import Awaitable, Callable
from io import BytesIO

import docx
from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.services.extraction import ExtractionOutcome, extract

# The fail-soft failure vocabulary a failed outcome may carry (Requirement
# 3.7 / ``services/extraction.py`` ``FailureCategory``). A ``Literal`` is not
# iterable at runtime, so the allowed values are mirrored here as a set the
# property can check membership against.
_FAILURE_CATEGORIES: frozenset[str] = frozenset(
    {"extraction_timeout", "corrupt_document", "empty_text"}
)

# Both magic-byte verdicts the Mime_Validator can hand the extractor. The
# property holds for either ``kind`` regardless of what the bytes actually
# are, so ``kind`` is generated independently of ``data``: feeding ZIP bytes
# as ``"pdf"`` (or vice versa) is a perfectly valid fail-soft case.
_kinds = st.sampled_from(("pdf", "docx"))

# A generous wall-clock ceiling for the main property: large enough that
# parsing a few KiB of bytes never approaches it, so the ``extraction_timeout``
# branch does not dominate and the no-raise/well-formed invariant is observed
# across the corrupt/empty branches that arbitrary bytes naturally hit.
_GENEROUS_TIMEOUT_SECONDS = 30.0

# A curated bank of adversarial payloads covering the "garbage, truncated,
# empty" inputs the property calls out explicitly: empty buffers, magic-byte
# headers with no valid body, a truncated ZIP local-file header, a
# structurally-empty ZIP carrying no OOXML parts, a wrong-format image,
# whitespace-only text, and a full byte-value sweep. Every one must still
# resolve to a well-formed failed outcome.
_ADVERSARIAL_BYTES: list[bytes] = [
    b"",  # empty buffer
    b"%PDF",  # PDF magic only, no body
    b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 64,  # PDF-ish header, corrupt body
    b"PK\x03\x04" + b"\x00" * 32,  # ZIP local-file-header prefix, truncated
    b"PK\x05\x06" + b"\x00" * 18,  # structurally-empty ZIP (no OOXML parts)
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,  # a PNG (neither pdf nor docx)
    b"   \n\t  \r\n  ",  # whitespace-only ASCII
    bytes(range(256)),  # every byte value
]

# Arbitrary bytes (including the empty buffer) up to a few KiB, drawn freshly
# or sampled from the adversarial bank. Small payloads keep each example fast:
# pypdf / python-docx reject malformed input promptly, so the corrupt-document
# branch returns without burning wall-clock.
_arbitrary_bytes = st.one_of(
    st.binary(min_size=0, max_size=4096),
    st.sampled_from(_ADVERSARIAL_BYTES),
)

# Non-whitespace alphabet for the valid-DOCX fixture: excluding spaces and
# newlines guarantees every generated paragraph contributes at least one
# non-whitespace character, so a small document under a generous timeout always
# reaches the ``succeeded`` branch (keeping that clause non-vacuous). A few
# non-ASCII letters exercise multi-byte-but-single-codepoint counting.
_DOCX_TEXT_CHARS = string.ascii_letters + string.digits + "éñüçßΩ你日本語"
_docx_paragraphs = st.lists(
    st.text(alphabet=_DOCX_TEXT_CHARS, min_size=1, max_size=200),
    min_size=1,
    max_size=20,
)


def _build_docx(paragraphs: list[str]) -> bytes:
    """Serialize ``paragraphs`` into a real in-memory ``.docx`` byte payload.

    Uses ``python-docx`` so the fixture is structurally identical to what the
    extractor reads in production — no hand-rolled OOXML, no mocks.
    """
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _run_sync(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Drive an async test body via :class:`asyncio.Runner`.

    Mirrors ``tests/property/test_extraction_truncation_counting.py``:
    ``Runner`` closes the event loop deterministically on ``__exit__``,
    avoiding the ``ResourceWarning("unclosed event loop")`` that bare
    ``asyncio.run`` can leak in teardown (the suite runs under
    ``filterwarnings = ["error"]``).
    """
    with asyncio.Runner() as runner:
        runner.run(coro_factory())


def _assert_well_formed(outcome: ExtractionOutcome) -> None:
    """Assert an outcome satisfies the Property 16 well-formedness clause.

    A failed outcome carries no text and a defined failure category; a
    succeeded outcome carries ``str`` text whose length equals ``char_count``
    and no failure category. ``status`` is always one of the two literals.
    """
    assert isinstance(outcome, ExtractionOutcome)
    assert outcome.status in {"succeeded", "failed"}
    if outcome.status == "failed":
        assert outcome.text is None
        assert outcome.char_count is None
        assert outcome.failure_category in _FAILURE_CATEGORIES
    else:
        assert outcome.failure_category is None
        assert isinstance(outcome.text, str)
        assert outcome.char_count == len(outcome.text)


@settings(max_examples=200, deadline=None)
@given(data=_arbitrary_bytes, kind=_kinds)
def test_extract_never_raises_and_is_well_formed(data: bytes, kind: str) -> None:
    """extract() returns a well-formed outcome for any bytes — it never raises.

    The core of Property 16: for arbitrary bytes (any size, including empty)
    and either ``kind``, ``extract`` resolves to an :class:`ExtractionOutcome`
    rather than propagating a parser or timeout error. The call is intentionally
    not guarded by a swallowing ``try`` — an input that makes ``extract`` raise
    is a genuine fail-soft defect and must surface as a Hypothesis
    counterexample. Most arbitrary payloads land on ``corrupt_document`` (or
    ``empty_text`` for empty/whitespace-only draws); that is the expected proof
    of fail-soft, and every outcome is checked for internal consistency.
    """

    async def _run() -> None:
        # No try/except: if this line raises, the property is violated and the
        # offending (data, kind) example is reported verbatim.
        outcome = await extract(
            data,
            kind,  # type: ignore[arg-type]  # st.sampled_from yields the two ResumeKind literals
            timeout_seconds=_GENEROUS_TIMEOUT_SECONDS,
            max_extracted_chars=200_000,
        )
        _assert_well_formed(outcome)

    _run_sync(_run)


@settings(max_examples=150, deadline=None)
@given(paragraphs=_docx_paragraphs)
def test_extract_succeeds_well_formed_on_valid_docx(paragraphs: list[str]) -> None:
    """A valid, non-whitespace DOCX extracts to a well-formed succeeded outcome.

    Keeps the ``succeeded`` half of the well-formedness clause non-vacuous: a
    real ``python-docx``-authored document of non-whitespace paragraphs, under
    a generous timeout, must yield ``status="succeeded"`` with ``str`` text and
    ``char_count == len(text)`` — and, like every case, must not raise.
    """
    data = _build_docx(paragraphs)

    async def _run() -> None:
        outcome = await extract(
            data,
            "docx",
            timeout_seconds=_GENEROUS_TIMEOUT_SECONDS,
            max_extracted_chars=200_000,
        )
        assert outcome.status == "succeeded"
        _assert_well_formed(outcome)

    _run_sync(_run)


@settings(max_examples=100, deadline=None)
@given(
    paragraphs=_docx_paragraphs,
    timeout_seconds=st.floats(min_value=0.0001, max_value=0.05),
)
def test_extract_fails_soft_under_small_timeout(
    paragraphs: list[str], timeout_seconds: float
) -> None:
    """A tiny wall-clock budget still produces a well-formed outcome, never a raise.

    Exercises the ``extraction_timeout`` branch: a valid DOCX parsed under a
    sub-100ms budget may abort cooperatively (``status="failed"``,
    ``failure_category="extraction_timeout"``) or, if it finishes first,
    succeed — either way the outcome is well-formed and ``extract`` does not
    raise the timeout into the request path (Requirement 3.5).
    """
    data = _build_docx(paragraphs)

    async def _run() -> None:
        outcome = await extract(
            data,
            "docx",
            timeout_seconds=timeout_seconds,
            max_extracted_chars=200_000,
        )
        _assert_well_formed(outcome)

    _run_sync(_run)
