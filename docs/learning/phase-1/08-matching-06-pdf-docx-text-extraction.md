# Bounded PDF and DOCX text extraction

## Introduction

This document explains how a server can pull the readable words out of an
uploaded document file and turn them into plain text, while keeping that work
from harming the machine that performs it. The two file kinds in view are the
Portable Document Format (PDF), a fixed-layout document format whose bytes
describe pages, fonts, and glyph positions, and the Word Document Format (DOCX),
Microsoft Word's format, which stores a document as a small zip
archive of text and styling files. Pulling words out of either of these is
called text extraction — a one-way transformation that produces a stream of
characters and discards the original layout, images, and styling. This topic
sits in the matching track because a scorer can only compare words it can read,
so a resume has to become plain text before any comparison can run.

**Learning outcomes** — after reading this document you will be able to:

- Explain why turning a document into text is a one-way transformation rather than a reversible parser that can rebuild the file.
- Describe the resource bounds — a wall-clock timeout, a retained-character cap, and a decompression-bomb guard — that stop a single upload from exhausting a server.
- Distinguish fail-soft extraction (record the failure, keep serving) from fail-fast rejection (refuse the upload outright) and say when each one applies.
- Recognise the common mistakes made when extracting text from uploaded files and recover from them.

Prerequisites: this document builds on
[File-upload safety](08-matching-05-file-upload-safety.md), which explains how an upload's true
type is confirmed from its leading bytes before extraction runs, and on
[Async Python and the asyncio model](03-backend-03-async-python-and-asyncio.md), which
explains how blocking work is moved off the main loop so one slow document does
not freeze every other request.

## Problem it solves

A resume arrives as a binary file — a PDF or a DOCX — but a text-matching
system can only work with characters. The concrete problem is getting a clean,
plain-text version of the words in that file so they can be compared against a
job description, and doing it inside a web request without letting a single
upload take the whole service down with it.

The prior approach most projects reach for first is to hand the raw file to a
document library and read whatever text it returns, on the request thread, with
no limits. That state of affairs carries three real hazards. A very large or
deeply nested document can make the library run for a long time, holding the
request open and burning processor time. A maliciously crafted file — for
example, a small archive that expands to gigabytes once decompressed — can
exhaust memory or disk before any text is even produced. And a corrupt or
encrypted file can make the library raise an error that, left unhandled,
becomes a server error and a failed upload for the user.

Bounded extraction solves this by treating the document library as untrusted
machinery wrapped in guards: a hard ceiling on how long extraction may run, a
ceiling on how many characters are kept, and a cheap pre-check that rejects a
decompression bomb (a small file engineered to balloon to an enormous size when
expanded) before any heavy work begins. The words come out; the file never gets
to dictate how much of the server it consumes.

## Mental model

Picture a librarian transcribing a bound book onto a single index card. The
librarian copies the readable words and ignores the cover, the typeface, the
page images, and the binding. When the card is full, transcription stops. And
once the card is written, you cannot reconstruct the original book from it — the
card holds the words, not the artifact. Text extraction works the same way: it
keeps the characters and throws away everything that made the file a file, and
the result is a copy in one direction only.

Two more details complete the picture. The librarian works against a kitchen
timer and a fixed-size card, so a sprawling book cannot consume the whole
afternoon or an endless ribbon of cards. And before transcribing a sealed
parcel of pages, the librarian first reads the packing slip to check the
declared total size — if the slip claims the parcel unpacks to a truckload, it
is refused at the door rather than opened.

Here is the flow for one document:

1. Confirm the file's real kind from its leading bytes, and for an archive-based
   format, read its directory of contents to check the declared uncompressed
   size and entry count against fixed ceilings — refuse it outright if either is
   exceeded.
2. Hand the bytes to the matching document reader and walk the file one page or
   one paragraph at a time, appending the text of each.
3. After each page or paragraph, check a clock against a precomputed deadline and
   stop early if it has passed; stop also once the accumulated text reaches the
   character ceiling.
4. Trim the result to the exact character cap. If what remains has no
   non-whitespace content, treat that as a failure to read the document.
5. Hand back either the text plus its length, or a small record naming why
   extraction failed.

## How it works

Text extraction is the act of reading the human-readable characters out of a
document file and emitting them as a plain character stream. It is a one-way
transformation: the output is a lossy projection of the input that drops layout,
fonts, images, and metadata, so there is no contract to rebuild the original
file from the text. This is the opposite of a reversible parser, which builds a
structured representation precisely so the original can be regenerated. Keeping
this distinction clear matters, because it tells you what guarantees you may and
may not expect — you get words, not a faithful round-trip.

Different file kinds expose their text differently. A fixed-layout document is a
sequence of pages, and a reader yields text page by page, sometimes imperfectly,
because the format stores glyph positions rather than tidy sentences. A
modern word-processor document is a zip archive whose entries hold the body text
as structured data, and a reader yields text paragraph by paragraph. In both
cases the natural unit of progress is small — a page or a paragraph — which is
what makes bounded iteration possible.

The first bound is a wall-clock timeout. Document libraries can be slow on
pathological input, and the surrounding request cannot be allowed to hang. Two
mechanisms cooperate. The blocking read runs in a separate worker thread so it
does not freeze the main event loop — the single-threaded scheduler that
interleaves concurrent tasks. On top of that, the extractor compares a
monotonic clock (a clock that only ever moves forward and is immune to
system-time adjustments) against a precomputed deadline after each page or
paragraph, and aborts the moment the deadline passes. The reason both are
needed is subtle: a running thread cannot be forcibly killed from outside, so
the outer timeout alone cannot stop a thread that is mid-computation. The
cooperative in-thread check is what actually halts the work; the outer timeout
is the backstop.

The second bound is a character cap. Extracted text is truncated to a maximum
number of characters, and the iteration stops accumulating once it reaches that
size, so a document with millions of words cannot produce an unbounded string in
memory. The retained length is reported alongside the text.

The third bound applies before extraction even begins, and only to the
archive-based format. Because that format is a zip container, its central
directory lists every entry's declared uncompressed size without unpacking
anything. Reading that directory is cheap, so a guard can sum the declared sizes
and count the entries and refuse the file if either crosses a ceiling. This is
the defense against a decompression bomb — a tiny file that expands to an
enormous one — and it is deliberately strict.

That strictness points at a design split worth naming. Extraction itself is
fail-soft: a timeout, a parser error on a corrupt or encrypted file, or an empty
result all produce a small outcome record describing the failure, never a crash
into the request. The upload still succeeds; the document is merely marked
unreadable. The decompression-bomb guard, by contrast, is fail-fast: a violating
file is rejected before anything is stored, because the correct response to a
weaponized upload is to refuse it, not to keep it as unparseable. One contract
forgives a document that cannot be read; the other one refuses a document that
should never have been accepted.

A final discipline cuts across all of this: the file bytes and the extracted
text are sensitive content, so this layer never writes them to a log. A failure
is recorded by category and identifier only, never by content.

## MatchLayer Phase 1 usage

In MatchLayer the extractor lives in
`apps/api/src/matchlayer_api/services/extraction.py`, which exposes two
functions with deliberately different safety contracts: `extract`, which is
fail-soft, and `guard_docx_archive`, which is fail-fast. The upload
orchestration in `apps/api/src/matchlayer_api/services/resumes.py` calls the
guard first (for a DOCX), stores the bytes, then calls `extract`, mapping its
result onto the resume's extraction columns.

The PDF path walks the document one page at a time under a cooperative deadline
and a character cap. The `monotonic` clock is checked between pages so the
worker thread stops promptly, and the loop breaks early once enough characters
have been gathered:

Source: `apps/api/src/matchlayer_api/services/extraction.py`

```python
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    total = 0
    for page in reader.pages:
        if monotonic() > deadline:
            raise _ExtractionTimeoutError
        page_text = page.extract_text() or ""
        parts.append(page_text)
        total += len(page_text)
        if total >= max_chars:
            break
    return "\n".join(parts)
```

The decompression-bomb guard runs before storage and before extraction. It
opens the DOCX as a zip archive and inspects the central directory — the
declared entry count and each entry's declared uncompressed size — without
decompressing anything, raising a fail-fast error the moment a ceiling is
crossed:

Source: `apps/api/src/matchlayer_api/services/extraction.py`

```python
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > max_archive_entries:
                raise MalformedUploadError(
                    f"Uploaded DOCX has too many archive entries (limit {max_archive_entries})."
                )
            total_uncompressed = 0
            for info in infos:
                total_uncompressed += info.file_size
                if total_uncompressed > max_decompressed_bytes:
                    raise MalformedUploadError(
                        "Uploaded DOCX exceeds the maximum uncompressed size "
                        f"of {max_decompressed_bytes} bytes."
                    )
```

The four bounds are not hard-coded in the extractor; they are passed in by the
caller from typed settings in `apps/api/src/matchlayer_api/config.py`
(`resume_extraction_timeout_seconds`, `resume_max_extracted_chars`,
`resume_max_decompressed_bytes`, and `resume_max_archive_entries`), which keeps
the extractor a pure, directly testable unit. On success `extract` returns an
`ExtractionOutcome` whose text is truncated to the cap and whose `char_count`
matches; on a timeout, a parser error, or whitespace-only output it returns a
failed outcome whose `failure_category` names the reason, and the caller records
that category and the resume identifier only — never the bytes or the text.

## Common pitfalls

- **Mistake:** Running the document library on the request thread with no time limit.
  **Symptom:** A single crafted or oversized upload makes the request hang and pins a processor core, and other requests slow down or time out.
  **Recovery:** Move the blocking read into a worker thread and enforce a wall-clock deadline, checking a monotonic clock between pages or paragraphs so the work stops cooperatively rather than running to completion.

- **Mistake:** Treating a corrupt, encrypted, or empty document as a server error.
  **Symptom:** Uploading a damaged file returns a 500-style error and the upload is lost, even though the file was received intact.
  **Recovery:** Make extraction fail-soft — catch the parser error, return a result that marks the document unreadable with a failure category, and let the upload succeed so the user can re-upload a clean file.

- **Mistake:** Extracting first and only then checking how big the document is.
  **Symptom:** Memory or disk usage spikes from a tiny upload, because a decompression bomb expanded before any size check ran.
  **Recovery:** For an archive-based format, read the central directory and sum the declared uncompressed sizes and entry count first, refusing the file fail-fast before storing or extracting anything.

- **Mistake:** Logging the file bytes or the extracted text while debugging an extraction problem.
  **Symptom:** Resume content — restricted personal data — appears in log lines or error traces where it can be read or exported.
  **Recovery:** Log only a failure category and a record identifier; never include the bytes or the text, and keep the logging in the caller that owns the identifier.

## External reading

- [Python documentation: the zipfile module](https://docs.python.org/3/library/zipfile.html)
- [Python documentation: asyncio task functions including wait_for](https://docs.python.org/3/library/asyncio-task.html)
- [Python documentation: time.monotonic and the time module](https://docs.python.org/3/library/time.html)
- [FastAPI documentation: concurrency and run_in_threadpool](https://fastapi.tiangolo.com/async/)
- [pypdf documentation: extracting text from a PDF](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [python-docx documentation: working with text](https://python-docx.readthedocs.io/en/latest/user/text.html)
