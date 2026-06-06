# Zip-bomb defense: bounding decompressed size

## Introduction

This document explains how a small uploaded file can be weaponised to exhaust a
server's memory or disk, and the cheap, deterministic check that stops it. The
attack is called a decompression bomb, or zip bomb — a tiny compressed file
crafted so that unpacking it produces an enormous amount of data, far more than
the machine can hold. The defense does not try to unpack the file safely;
instead it reads the small bookkeeping section a compressed archive keeps about
itself, adds up how large the file _claims_ it will become once unpacked, and
refuses the upload before any unpacking happens. This topic sits in the Matching
and scoring track because resume uploads are the one place a stranger hands the
server an arbitrary file, so the upload path is where this defense has to live.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a decompression bomb is and why a few kilobytes on disk can turn into gigabytes in memory.
- Describe how a compressed archive's directory of contents lets a server measure the unpacked size without unpacking anything.
- Explain why bounding both the total declared size and the number of entries defends against two different bomb shapes.
- Recognise the symptoms of a missing or misplaced decompression bound and recover from each.

Prerequisites:

- [File-upload safety](08-matching-05-file-upload-safety.md) introduces how an uploaded file's true type is verified and why the upload path is treated as hostile input; this document builds on that by bounding the decompressed size of one accepted file type.

## Problem it solves

Compression works by replacing repetition with short instructions. A file that
is one byte repeated a billion times compresses to almost nothing, because the
compressed form is closer to "repeat this byte a billion times" than to the
billion bytes themselves. That ratio is normally a convenience. It becomes a
weapon when an attacker uploads a file that is small enough to slip past an
upload size limit yet expands, on unpacking, into something that fills memory or
disk. A few kilobytes can claim to expand into many gigabytes. The concrete
problem is that the server cannot afford to find out the hard way — by unpacking
first and measuring afterwards — because the act of measuring is the act of
being attacked.

The prior approach that fails is to enforce only a limit on the _uploaded_ byte
size and then hand the accepted file straight to a parser. That feels safe: the
upload is capped, so surely the work is bounded. It is not. The cap bounds the
compressed size, and the compressed size tells you almost nothing about the
unpacked size. A naive parser opens the archive and streams every entry into
memory, so the bomb detonates inside a component that was never written to
defend itself. Worse, this often happens after the file has already been written
to storage, so the attack also leaves junk behind.

A defense that reads the archive's own declared sizes and rejects oversized
inputs _before_ unpacking or storing solves this: the work the server does to
decide is tiny and fixed, regardless of how large the attacker claims the
unpacked file will be.

## Mental model

Picture a receiving dock at a warehouse with a strict rule: nothing gets
unpacked until the paperwork has been checked against the size of the room.

1. A courier drops off a sealed crate together with a manifest — a printed list of every item inside and the size each item will take up once removed from the crate.
2. Before prying the crate open, the clerk reads the manifest and adds up the declared sizes of all the items.
3. If that running total climbs past the size of the receiving room, the clerk stops adding and refuses the crate immediately, without ever opening it.
4. The clerk also refuses any crate whose manifest lists an absurd number of items, because thousands of tiny entries are their own kind of overflow.
5. Only a crate whose manifest both fits the room and lists a sane number of items is opened and unpacked.

The crucial move is step 2: the clerk trusts the manifest enough to _reject_ on
it, but never trusts it enough to _accept and unpack_ a crate the room cannot
hold. Reading the manifest costs the same whether the crate is honest or a bomb,
so the attacker gains nothing by lying big.

## How it works

A compressed archive is not an opaque blob. It keeps an internal table of
contents — a central directory — that lists every entry it holds, and for each
entry records both its compressed size and its declared uncompressed size. That
directory exists so that tools can list an archive's contents quickly without
decompressing it. The same property is what makes a safe bound possible: a
reader can open the archive, walk the central directory, and learn the claimed
unpacked size of every entry while decompressing none of them. Reading the
directory is cheap and its cost does not grow with the declared sizes inside.

A decompression bomb exploits the gap between compressed size and declared
uncompressed size. The attacker assembles an archive whose entries are highly
repetitive, so they compress to almost nothing, yet whose directory declares a
gigantic uncompressed total. There are two common shapes. The first is one
entry that declares an enormous size. The second is a very large number of
small entries whose sizes add up, and which also burden the reader by their
count alone. A robust bound therefore checks two independent ceilings: the sum
of all declared uncompressed sizes, and the number of entries.

The defense is a guard that runs before any decompression and before the file
is written to storage:

1. Open the archive far enough to read its central directory. If the bytes are not a readable archive at all, reject the upload as malformed.
2. Count the entries. If the count exceeds the entry ceiling, reject.
3. Walk the entries, accumulating each one's declared uncompressed size. The moment the running total crosses the size ceiling, stop and reject — there is no need to finish summing a total that is already too large.
4. Only if both ceilings hold does the file proceed to be stored and, later, unpacked under its own separate limits.

Because the guard relies on declared sizes rather than actual decompression, an
attacker cannot make it do more work by claiming a larger payload: a bigger lie
is rejected at the same low cost as a smaller one. The bound is fail-fast — it
raises an error and refuses the request — which is deliberately different from
the fail-soft handling that a merely corrupt-but-honest file receives later in
the pipeline.

## MatchLayer Phase 1 usage

The guard lives in the extraction service at
`apps/api/src/matchlayer_api/services/extraction.py`. A modern Word document in
the `.docx` format (DOCX) is internally a set of Extensible Markup Language (XML)
files compressed together in a single compressed archive (ZIP) — the
Office Open XML (OOXML) packaging format — so an uploaded resume in that format
is exactly the kind of archive a decompression bomb hides in. The function reads
the archive's central directory through the Python standard-library `zipfile`
module and enforces two ceilings before anything is decompressed or stored:

Source: `apps/api/src/matchlayer_api/services/extraction.py`

```python
def guard_docx_archive(
    data: bytes,
    *,
    max_decompressed_bytes: int,
    max_archive_entries: int,
) -> None:
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
    except zipfile.BadZipFile as exc:
        raise MalformedUploadError("Uploaded DOCX is not a readable archive.") from exc
```

The `info.file_size` value is the entry's _declared uncompressed_ size taken
from the central directory, so the running `total_uncompressed` sum measures the
claimed unpacked payload without decompressing a single entry. A
`MalformedUploadError` becomes a Hypertext Transfer Protocol (HTTP) `422`
response, and an archive that cannot even be opened is rejected the same way
rather than being stored as an unreadable object.

The two ceilings are configuration values, not magic numbers buried in the
function. They are declared in the settings at
`apps/api/src/matchlayer_api/config.py` — a total uncompressed ceiling and an
entry-count ceiling:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    resume_max_decompressed_bytes: int = 52_428_800
    resume_max_archive_entries: int = 256
```

The guard runs at a specific point in the upload flow: after the file's true
type has been confirmed, and before the bytes are written to object storage.
The upload service in `apps/api/src/matchlayer_api/services/resumes.py` calls it
only for archive-backed documents and passes the configured ceilings in:

Source: `apps/api/src/matchlayer_api/services/resumes.py`

```python
        # --- 3. DOCX zip-bomb guard (fail-fast, before storage) ------
        if kind == "docx":
            guard_docx_archive(
                data,
                max_decompressed_bytes=self._settings.resume_max_decompressed_bytes,
                max_archive_entries=self._settings.resume_max_archive_entries,
            )
```

Ordering matters: because the guard runs before the storage write, a rejected
bomb never produces a stored object, and because it runs after type
confirmation, it only ever inspects bytes already accepted as an archive. The
raw bytes are treated as restricted content throughout — the error messages name
only the numeric limits, never any file content.

## Common pitfalls

- **Mistake:** Bounding only the uploaded (compressed) byte size and assuming that caps the work, then handing the file to a parser.
  **Symptom:** A small upload sails past the size limit, but memory or disk usage spikes and the worker is killed or the host runs out of space while parsing it.
  **Recovery:** Add a guard that reads the archive's declared uncompressed sizes and rejects oversized inputs before any decompression, keeping the compressed-size cap as a separate, earlier check.

- **Mistake:** Bounding the total declared size but not the number of entries.
  **Symptom:** An archive with hundreds of thousands of tiny entries passes the size check yet stalls the reader, because the sheer entry count is itself the overflow.
  **Recovery:** Enforce an entry-count ceiling alongside the size ceiling, and reject as soon as either is exceeded.

- **Mistake:** Running the decompression bound _after_ writing the file to storage, or after starting to decompress it.
  **Symptom:** Rejected uploads still leave orphaned objects behind, or the bomb detonates inside the parser even though a limit "exists".
  **Recovery:** Move the guard ahead of the storage write and ahead of any decompression, so a rejected file is never stored and never unpacked.

- **Mistake:** Trusting actual decompression to measure the unpacked size, by decompressing each entry and counting bytes as they stream.
  **Symptom:** Measuring the size is itself the attack — the server allocates the very memory the bomb intended before its own limit can fire.
  **Recovery:** Read the declared sizes from the archive's central directory instead, which costs the same whether the input is honest or hostile.

## External reading

- [Python documentation: the `zipfile` module and `ZipInfo.file_size`](https://docs.python.org/3/library/zipfile.html)
- [Common Weakness Enumeration (CWE) 409: Improper Handling of Highly Compressed Data (Data Amplification)](https://cwe.mitre.org/data/definitions/409.html)
- [Open Worldwide Application Security Project (OWASP): Denial of Service](https://owasp.org/www-community/attacks/Denial_of_Service)
