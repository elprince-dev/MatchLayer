#!/usr/bin/env python3
"""Compliance validator for the MatchLayer Learning_Docs_Library.

This script is the Validation Layer described in the ``phase-1-learning-docs``
design (Design §"Compliance Validator", rules ``LDC001`` through ``LDC020``). It walks
the documentation library at ``docs/learning/``, parses each Markdown file into
a structured shape, applies a set of structural rules, and reports violations
as ``Finding`` records. Like ``check_env_drift.py`` and ``check_lexicon_drift.py``
it is intentionally **stdlib-only** at runtime (``pathlib``, ``re``, ``json``,
``dataclasses``, ``argparse``) so it can run before any project dependencies are
installed and from any CI image that ships a recent Python interpreter.

What this module provides (task 2.2)
------------------------------------
This file currently implements the *foundation* the rule checks and CLI build
on top of:

* **Data models** — the frozen dataclasses ``TopicDoc``, ``Section``,
  ``FencedBlock``, ``Link``, ``CoverageRow``, and ``Finding`` that the rest of
  the validator operates over.
* **Library-as-data constants** — ``DOC_TEMPLATE_REQUIRED``,
  ``DOC_TEMPLATE_OPTIONAL``, ``BANNED_PHRASES``, ``ALLOWED_LANGUAGES``, and the
  ``AUTHORITATIVE_HOSTS`` fallback tuple.
* **A Markdown parser** — ``parse_topic_doc`` turns a Topic_Doc into a
  ``TopicDoc`` (H1 title, ordered H2 sections, fenced code blocks with their
  ``Source:`` citations, internal vs external links, and the prerequisite links
  declared in the ``Introduction``).
* **Library-reading helpers** — ``walk_library`` (enumerate the library's
  Markdown files), ``parse_phase_1_index`` (read the Phase_1_Index
  ``Topic coverage`` table into ``CoverageRow`` records), and
  ``parse_authoritative_hosts`` (hand-parse the YAML host registry out of
  ``CONVENTIONS.md`` without a third-party YAML dependency).

The compliance rules ``LDC001`` and ``LDC002`` (filename and H1 conformance) are
implemented below as of task 2.4, ``LDC003`` (Doc_Template H2-sequence equality)
as of task 2.7, and the per-section content rules as of task 2.9 — ``LDC004``
(``How it works`` is implementation-agnostic) plus ``LDC101`` through ``LDC105``
(the ``Introduction`` learning-outcomes, ``Mental model`` handhold, ``MatchLayer
Phase 1 usage`` anchored-content, ``Common pitfalls`` labelled-entry, and
``External reading`` size-bound checks). The file-reference and code-snippet
rules ``LDC005`` through ``LDC008`` are implemented as of task 2.16; the
link-integrity rules ``LDC009`` through ``LDC011`` (plus the advisory
authoritative-source check ``LDC106``) as of task 2.21. The
beginner-accessibility rules ``LDC012`` through ``LDC015`` are implemented as of
task 2.26; the library-level coverage and index rules ``LDC016`` through
``LDC020`` as of task 2.32. The ``argparse`` command-line entry point is added
in task 2.46. This module is import-safe and has no side effects.

Usage
-----
::

    # (the CLI is added in task 2.46)
    python3 tools/learning_docs_check.py --root <repo-root>

The parser and helpers in this module never make network requests and never
mutate the repository.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One H2 section of a Topic_Doc.

    ``body`` is the raw Markdown between this H2 heading and the next H2 (or the
    end of the file), excluding the heading line itself.
    """

    heading: str  # exact H2 text
    line: int  # 1-based line of the heading
    body: str  # raw markdown of the section body


@dataclass(frozen=True)
class FencedBlock:
    """A fenced code block (triple-backtick) inside a Topic_Doc."""

    language: str  # tag on opening fence, '' if none
    body: str  # block contents, no fences
    line: int  # 1-based line of the opening fence
    source_path: Path | None  # parsed from a 'Source: `<path>`' line, None if absent
    is_simplified: bool  # True if preceded by 'simplified for illustration'


@dataclass(frozen=True)
class Link:
    """A Markdown inline link ``[text](target)`` found in a Topic_Doc."""

    text: str
    target: str  # raw href
    line: int  # 1-based line the link appears on
    is_internal: bool
    fragment: str | None  # heading anchor, if any


@dataclass(frozen=True)
class TopicDoc:
    """A parsed Topic_Doc Markdown file."""

    path: Path  # e.g. docs/learning/phase-1/fastapi-application-factory.md
    filename: str  # 'fastapi-application-factory.md'
    title: str  # H1 text
    sections: list[Section]  # ordered, by H2 in source order
    fenced_blocks: list[FencedBlock]
    internal_links: list[Link]
    external_links: list[Link]
    prerequisites: list[Link]  # parsed from Introduction
    raw_lines: list[str]


@dataclass(frozen=True)
class CoverageRow:
    """One row in the Phase_1_Index ``Topic coverage`` table."""

    entry_text: str  # verbatim from the Phase_1_Topic_Coverage_List ('Coverage entry')
    requirement_clause: str  # e.g. '4.4'
    topic_doc_filename: str  # the Topic_Doc that covers it ('' until authored)
    thematic_section: str  # one of the 12 thematic sections


@dataclass(frozen=True)
class Finding:
    """A validator output record."""

    file: Path
    line: int  # 0 when the rule is file-level
    rule_id: str  # e.g. 'LDC003'
    requirement: str  # e.g. 'Req 8.4'
    message: str  # human-readable explanation


# ---------------------------------------------------------------------------
# Library-as-data constants
# ---------------------------------------------------------------------------

# The canonical Doc_Template (Design §"Doc_Template as data"). Compliance for a
# Topic_Doc reduces to: extract H2 headings in source order, drop any optional
# heading after verifying its sandwich position, then assert the residual tuple
# equals DOC_TEMPLATE_REQUIRED.
DOC_TEMPLATE_REQUIRED: tuple[str, ...] = (
    "Introduction",
    "Problem it solves",
    "Mental model",
    "How it works",
    "MatchLayer Phase 1 usage",
    "Common pitfalls",
    "External reading",
)

# Optional heading -> (must come after, must come before).
DOC_TEMPLATE_OPTIONAL: dict[str, tuple[str, str]] = {
    "Hands-on checkpoint": ("Common pitfalls", "External reading"),
}

# Knowledge-presuming phrases banned outside fenced code blocks. Matched
# case-insensitively as a whole-word substring. 'just' is advisory (high
# false-positive rate); the rules layer downgrades it to a warning.
BANNED_PHRASES: tuple[str, ...] = (
    "as you know",
    "obviously",
    "clearly",
    "simply",
    "just",
    "of course",
    "everyone knows",
    "it should be clear",
)

# Allowed fenced-code-block language identifiers.
ALLOWED_LANGUAGES: frozenset[str] = frozenset(
    {
        "python",
        "typescript",
        "tsx",
        "javascript",
        "jsx",
        "yaml",
        "json",
        "dockerfile",
        "sql",
        "bash",
        "sh",
        "text",
    }
)

# Fallback authoritative-source registry. The runtime list is read from the
# fenced YAML block in CONVENTIONS.md via ``parse_authoritative_hosts``; this
# tuple is the minimum the Conventions_Doc is required to name and is used only
# when the YAML block cannot be read.
AUTHORITATIVE_HOSTS: tuple[str, ...] = (
    "developer.mozilla.org",  # MDN
    "docs.python.org",
    "nextjs.org",
)

# Fallback project-glossary term list (Conventions_Doc "Project glossary
# (define-on-first-use list)"; Req 5.2). The runtime list is read from the
# fenced ``text`` block under that heading in ``CONVENTIONS.md`` via
# ``parse_glossary_terms``; this tuple mirrors that block verbatim and is used
# only when the block cannot be read. ``LDC012`` checks the first in-prose use
# of each term carries a same-paragraph definition (it is advisory/heuristic).
GLOSSARY_TERMS: tuple[str, ...] = (
    "monorepo",
    "workspace",
    "lockfile",
    "pre-commit hook",
    "Server Component",
    "Client Component",
    "strict mode",
    "design token",
    "application factory",
    "ASGI",
    "async / await",
    "event loop",
    "session factory",
    "connection pool",
    "migration",
    "structured logging",
    "middleware",
    "request id",
    "JSON Web Token (JWT)",
    "access token",
    "refresh token",
    "token rotation",
    "Argon2id",
    "CSRF",
    "rate limiting",
    "audit log",
    "account enumeration",
    "TF-IDF",
    "cosine similarity",
    "skill lexicon",
    "magic-byte validation",
    "zip bomb",
    "storage abstraction",
    "UUIDv7",
    "soft delete",
    "cursor pagination",
    "idempotency key",
    "container",
    "image layer",
    "multi-stage build",
    "healthcheck",
    "distroless",
    "OpenAPI",
    "codegen",
    "property-based testing",
    "fixture",
    "accessibility (axe-core)",
    "import boundary",
    "CI job",
    "branch protection",
)


# ---------------------------------------------------------------------------
# Parsing primitives
# ---------------------------------------------------------------------------

# A fenced-code-block delimiter: optional indent, three-or-more backticks, then
# an info string (the language tag on the opening fence; empty on the closing
# fence).
_FENCE_RE: re.Pattern[str] = re.compile(r"^[ \t]*(`{3,})(.*)$")

# H1 / H2 ATX headings. A single ``#`` followed by whitespace is an H1; exactly
# two ``#`` an H2. ``### foo`` matches neither (the char after ``##`` is ``#``,
# not whitespace).
_H1_RE: re.Pattern[str] = re.compile(r"^#[ \t]+(.+?)[ \t]*$")
_H2_RE: re.Pattern[str] = re.compile(r"^##[ \t]+(.+?)[ \t]*$")

# Markdown inline link ``[text](target)``. ``target`` may carry a trailing
# ``"title"`` which the caller strips before classifying.
_LINK_RE: re.Pattern[str] = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

# A target is external when it carries a URI scheme (``https:``, ``mailto:`` …)
# or is protocol-relative (``//host/...``); everything else is internal.
_EXTERNAL_TARGET_RE: re.Pattern[str] = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.\-]*:|//)")

# A ``Source: `<path>` `` citation accompanying a fenced code block.
_SOURCE_CITATION_RE: re.Pattern[str] = re.compile(r"Source:\s*`([^`]+)`")

# The label that must precede an illustrative (non-verbatim) fenced block.
_SIMPLIFIED_PHRASE: str = "simplified for illustration"

# A Markdown table row: begins (after optional whitespace) with a pipe.
_TABLE_ROW_RE: re.Pattern[str] = re.compile(r"^\s*\|")

# A YAML list item ``- value`` (value is the first token, before any comment).
_YAML_LIST_ITEM_RE: re.Pattern[str] = re.compile(r"^\s*-\s*([^\s#]+)")


# ---------------------------------------------------------------------------
# Internal parsing helpers
# ---------------------------------------------------------------------------


def _nearest_nonblank_before(lines: list[str], idx: int) -> str | None:
    """Return the nearest non-blank line strictly before ``idx`` (0-based)."""
    for j in range(idx - 1, -1, -1):
        if lines[j].strip():
            return lines[j]
    return None


def _nearest_nonblank_after(lines: list[str], idx: int) -> str | None:
    """Return the nearest non-blank line strictly after ``idx`` (0-based)."""
    for j in range(idx + 1, len(lines)):
        if lines[j].strip():
            return lines[j]
    return None


def _detect_source_path(lines: list[str], open_idx: int, close_idx: int) -> Path | None:
    """Return the ``Source: `<path>` `` citation for a fenced block, if present.

    A citation may sit on the nearest non-blank line immediately before the
    opening fence or immediately after the closing fence (Conventions_Doc,
    "Code-snippet rules").
    """
    for candidate in (
        _nearest_nonblank_before(lines, open_idx),
        _nearest_nonblank_after(lines, close_idx),
    ):
        if candidate is None:
            continue
        match = _SOURCE_CITATION_RE.search(candidate)
        if match:
            return Path(match.group(1).strip())
    return None


def _detect_simplified(lines: list[str], open_idx: int) -> bool:
    """Return True if the block at ``open_idx`` is labelled ``simplified ...``."""
    before = _nearest_nonblank_before(lines, open_idx)
    return before is not None and _SIMPLIFIED_PHRASE in before.casefold()


def _parse_fenced_blocks(lines: list[str]) -> tuple[list[FencedBlock], frozenset[int]]:
    """Parse fenced code blocks and report which 0-based line indices are fenced.

    Returns the ordered list of ``FencedBlock`` records plus the set of line
    indices that lie inside a fenced region (including the opening and closing
    fence lines). The index set lets prose-level scans (headings, links, banned
    phrases) ignore content that lives inside code blocks.

    A fence is closed by a delimiter of at least as many backticks as the one
    that opened it (CommonMark). An unterminated fence runs to end-of-file.
    """
    blocks: list[FencedBlock] = []
    fenced_indices: set[int] = set()

    idx = 0
    n = len(lines)
    while idx < n:
        open_match = _FENCE_RE.match(lines[idx])
        if open_match is None:
            idx += 1
            continue

        open_ticks = open_match.group(1)
        language = open_match.group(2).strip()
        open_idx = idx

        # Find the matching closing fence (>= as many backticks, no info string).
        close_idx = n  # default: runs to EOF if never closed
        body_lines: list[str] = []
        scan = idx + 1
        while scan < n:
            close_match = _FENCE_RE.match(lines[scan])
            if (
                close_match is not None
                and len(close_match.group(1)) >= len(open_ticks)
                and close_match.group(2).strip() == ""
            ):
                close_idx = scan
                break
            body_lines.append(lines[scan])
            scan += 1

        last_idx = close_idx if close_idx < n else n - 1
        for line_idx in range(open_idx, last_idx + 1):
            fenced_indices.add(line_idx)

        blocks.append(
            FencedBlock(
                language=language,
                body="\n".join(body_lines),
                line=open_idx + 1,
                source_path=_detect_source_path(lines, open_idx, close_idx),
                is_simplified=_detect_simplified(lines, open_idx),
            )
        )
        idx = close_idx + 1

    return blocks, frozenset(fenced_indices)


def _strip_link_title(target: str) -> str:
    """Strip a trailing ``"title"`` (or ``'title'``) from a Markdown link target."""
    stripped = target.strip()
    for quote in ('"', "'"):
        pos = stripped.find(f" {quote}")
        if pos != -1 and stripped.endswith(quote):
            return stripped[:pos].strip()
    return stripped


def _make_link(text: str, raw_target: str, line: int) -> Link:
    """Build a ``Link`` from raw Markdown link parts, classifying internal/external.

    A target carrying a URI scheme (``https:``, ``mailto:`` …) or that is
    protocol-relative (``//host``) is external. A pure ``#fragment`` target is an
    internal same-file anchor. Everything else is an internal relative path; its
    ``#fragment`` suffix, if any, is captured separately.
    """
    target = _strip_link_title(raw_target)
    is_external = bool(_EXTERNAL_TARGET_RE.match(target))

    fragment: str | None = None
    if not is_external and "#" in target:
        _, _, frag = target.partition("#")
        fragment = frag or None

    return Link(
        text=text,
        target=target,
        line=line,
        is_internal=not is_external,
        fragment=fragment,
    )


def _extract_links(lines: list[str], fenced_indices: frozenset[int]) -> list[Link]:
    """Extract every Markdown inline link outside fenced code blocks, in order."""
    links: list[Link] = []
    for line_idx, raw in enumerate(lines):
        if line_idx in fenced_indices:
            continue
        for match in _LINK_RE.finditer(raw):
            links.append(_make_link(match.group(1), match.group(2), line_idx + 1))
    return links


def _parse_title(lines: list[str], fenced_indices: frozenset[int]) -> str:
    """Return the H1 title text, or '' when no H1 is present.

    The first ATX H1 found outside a fenced block wins; per the Doc_Template the
    H1 is expected on line 1, but the parser does not enforce placement (that is
    rule ``LDC002``'s job).
    """
    for line_idx, raw in enumerate(lines):
        if line_idx in fenced_indices:
            continue
        match = _H1_RE.match(raw)
        if match:
            return match.group(1).strip()
    return ""


def _parse_sections(lines: list[str], fenced_indices: frozenset[int]) -> list[Section]:
    """Split the file into ordered H2 sections.

    Each ``Section`` spans from its H2 heading to the line before the next H2
    (or end-of-file). The ``body`` excludes the heading line. H2 headings inside
    fenced code blocks are ignored.
    """
    heading_positions: list[tuple[int, str]] = []
    for line_idx, raw in enumerate(lines):
        if line_idx in fenced_indices:
            continue
        match = _H2_RE.match(raw)
        if match:
            heading_positions.append((line_idx, match.group(1).strip()))

    sections: list[Section] = []
    for pos, (line_idx, heading) in enumerate(heading_positions):
        body_start = line_idx + 1
        body_end = heading_positions[pos + 1][0] if pos + 1 < len(heading_positions) else len(lines)
        body = "\n".join(lines[body_start:body_end])
        sections.append(Section(heading=heading, line=line_idx + 1, body=body))
    return sections


def _parse_prerequisites(sections: list[Section], all_links: list[Link]) -> list[Link]:
    """Return the internal links declared inside the ``Introduction`` section.

    Prerequisites are the hyperlinked Topic_Doc list the Doc_Template requires in
    the ``Introduction`` (Conventions_Doc, "Declare prerequisites in the
    Introduction"). The parser captures every internal link whose line falls
    within the ``Introduction`` section's span; the precise prerequisite-list
    rule (``LDC015``) is enforced in a later task.
    """
    intro = next((s for s in sections if s.heading == "Introduction"), None)
    if intro is None:
        return []

    intro_idx = sections.index(intro)
    start_line = intro.line
    end_line = sections[intro_idx + 1].line if intro_idx + 1 < len(sections) else None

    prerequisites: list[Link] = []
    for link in all_links:
        if not link.is_internal:
            continue
        if link.line <= start_line:
            continue
        if end_line is not None and link.line >= end_line:
            continue
        prerequisites.append(link)
    return prerequisites


# ---------------------------------------------------------------------------
# Public parser
# ---------------------------------------------------------------------------


def parse_topic_doc(path: Path) -> TopicDoc:
    """Parse a Topic_Doc Markdown file into a structured ``TopicDoc``.

    Splits the file into its H1 title and ordered H2 ``Section`` records, parses
    every fenced code block (capturing its language, body, opening line, any
    ``Source: `<path>` `` citation, and whether it is labelled ``simplified for
    illustration``), classifies inline links as internal vs external, and pulls
    the prerequisite links out of the ``Introduction`` section.

    The parser is read-only and tolerant: a malformed or incomplete Topic_Doc
    still yields a ``TopicDoc`` (with empty title or missing sections) so the
    rule layer — not the parser — owns every compliance decision.
    """
    text = path.read_text(encoding="utf-8")
    raw_lines = text.split("\n")

    fenced_blocks, fenced_indices = _parse_fenced_blocks(raw_lines)
    title = _parse_title(raw_lines, fenced_indices)
    sections = _parse_sections(raw_lines, fenced_indices)
    all_links = _extract_links(raw_lines, fenced_indices)

    internal_links = [link for link in all_links if link.is_internal]
    external_links = [link for link in all_links if not link.is_internal]
    prerequisites = _parse_prerequisites(sections, all_links)

    return TopicDoc(
        path=path,
        filename=path.name,
        title=title,
        sections=sections,
        fenced_blocks=fenced_blocks,
        internal_links=internal_links,
        external_links=external_links,
        prerequisites=prerequisites,
        raw_lines=raw_lines,
    )


# ---------------------------------------------------------------------------
# Library-reading helpers
# ---------------------------------------------------------------------------


def walk_library(root: Path) -> Iterable[Path]:
    """Yield every Markdown file under the Learning_Docs_Library, sorted.

    ``root`` is the repository root; the library lives at ``docs/learning/``.
    The result is sorted for deterministic output across runs (the validator
    sorts findings, and a stable file order keeps that reproducible). When the
    library directory does not exist, the iterable is empty.
    """
    library_root = root / "docs" / "learning"
    if not library_root.is_dir():
        return []
    return sorted(p for p in library_root.rglob("*.md") if p.is_file())


def _split_table_row(line: str) -> list[str]:
    """Split a Markdown table row into stripped cell values.

    Leading and trailing pipes are dropped before splitting so
    ``| a | b |`` yields ``['a', 'b']``.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    """Return True for a Markdown table header-separator row (``--- | :--:``)."""
    return bool(cells) and all(re.fullmatch(r":?-+:?", cell) is not None for cell in cells)


def parse_phase_1_index(path: Path) -> list[CoverageRow]:
    """Parse the Phase_1_Index ``Topic coverage`` table into ``CoverageRow`` records.

    ``path`` is ``docs/learning/phase-1/README.md``. The function locates the
    ``## Topic coverage`` H2 section, finds the Markdown table within it (the
    header row ``Coverage entry | Requirement clause | Topic_Doc filename |
    Thematic section`` followed by a ``---`` separator), and returns one
    ``CoverageRow`` per data row. The ``Coverage entry`` column maps to
    ``CoverageRow.entry_text``. An empty ``Topic_Doc filename`` cell (a
    not-yet-authored Topic_Doc) is preserved as an empty string.

    Returns an empty list when the file or the ``Topic coverage`` table is
    absent or malformed; the rule layer decides what that means.
    """
    if not path.is_file():
        return []

    lines = path.read_text(encoding="utf-8").split("\n")

    # Find the bounds of the '## Topic coverage' section.
    start: int | None = None
    end = len(lines)
    for idx, raw in enumerate(lines):
        match = _H2_RE.match(raw)
        if match is None:
            continue
        heading = match.group(1).strip()
        if start is None and heading == "Topic coverage":
            start = idx + 1
        elif start is not None:
            end = idx
            break

    if start is None:
        return []

    return _coverage_rows_from_lines(lines[start:end])


def _coverage_rows_from_lines(section_lines: list[str]) -> list[CoverageRow]:
    """Build ``CoverageRow`` records from the lines of the Topic coverage section.

    Skips the header row and the ``---`` separator; reads each subsequent table
    row into a ``CoverageRow``. Rows with fewer than four columns are ignored.
    """
    rows: list[CoverageRow] = []
    seen_separator = False
    for raw in section_lines:
        if not _TABLE_ROW_RE.match(raw):
            continue
        cells = _split_table_row(raw)
        if _is_separator_row(cells):
            seen_separator = True
            continue
        if not seen_separator:
            # This is the header row (or pre-separator noise); skip it.
            continue
        if len(cells) < 4:
            continue
        rows.append(
            CoverageRow(
                entry_text=cells[0],
                requirement_clause=cells[1],
                topic_doc_filename=cells[2],
                thematic_section=cells[3],
            )
        )
    return rows


def parse_authoritative_hosts(conventions_path: Path) -> tuple[str, ...]:
    """Read the authoritative-host registry from ``CONVENTIONS.md``.

    The Conventions_Doc declares the registry as a fenced ``yaml`` block under
    its "Authoritative-host registry" heading, of the shape::

        authoritative_hosts:
          - developer.mozilla.org   # MDN Web Docs
          - docs.python.org         # Python

    To honour the stdlib-only runtime constraint this hand-parses the block with
    ``re``/string logic — no third-party YAML library. It scans the document's
    fenced blocks for the one whose body declares ``authoritative_hosts:`` and
    returns the hosts in document order (trailing ``# comments`` stripped,
    duplicates removed). Falls back to the ``AUTHORITATIVE_HOSTS`` constant when
    the file or the block cannot be read.
    """
    if not conventions_path.is_file():
        return AUTHORITATIVE_HOSTS

    lines = conventions_path.read_text(encoding="utf-8").split("\n")
    blocks, _ = _parse_fenced_blocks(lines)

    for block in blocks:
        if block.language != "yaml":
            continue
        hosts = _hosts_from_yaml_body(block.body)
        if hosts is not None:
            return hosts

    return AUTHORITATIVE_HOSTS


def _hosts_from_yaml_body(body: str) -> tuple[str, ...] | None:
    """Extract ``authoritative_hosts`` list items from a YAML block body.

    Returns the ordered, de-duplicated host tuple when the body declares an
    ``authoritative_hosts:`` key, or ``None`` when this block is some other YAML
    (e.g. the allowed-languages block), so the caller can keep scanning.
    """
    block_lines = body.split("\n")
    key_idx: int | None = None
    for idx, raw in enumerate(block_lines):
        if re.match(r"^\s*authoritative_hosts\s*:\s*$", raw):
            key_idx = idx
            break
    if key_idx is None:
        return None

    hosts: list[str] = []
    for raw in block_lines[key_idx + 1 :]:
        if raw.strip() == "":
            continue
        item = _YAML_LIST_ITEM_RE.match(raw)
        if item is None:
            # First non-blank, non-list line ends the block's list.
            break
        host = item.group(1).strip()
        if host and host not in hosts:
            hosts.append(host)

    return tuple(hosts)


# The Conventions_Doc heading that introduces the project-glossary term block.
# Matched case-insensitively against ATX heading text so a level change or a
# trailing parenthetical does not break the lookup.
_GLOSSARY_HEADING_RE: re.Pattern[str] = re.compile(
    r"^#{1,6}[ \t]+.*project glossary", re.IGNORECASE
)


def parse_glossary_terms(conventions_path: Path) -> tuple[str, ...]:
    """Read the project-glossary term list from ``CONVENTIONS.md``.

    The Conventions_Doc declares the glossary as a fenced ``text`` block under
    its "Project glossary (define-on-first-use list)" heading, one term per
    line::

        monorepo
        workspace
        pre-commit hook
        ...

    This locates that heading, takes the first fenced ``text`` block that opens
    after it, and returns its non-blank lines as the ordered, de-duplicated term
    tuple. Like :func:`parse_authoritative_hosts` it is stdlib-only and falls
    back to the :data:`GLOSSARY_TERMS` constant when the file, the heading, or
    the block cannot be read. The file is only read, never written.
    """
    if not conventions_path.is_file():
        return GLOSSARY_TERMS

    lines = conventions_path.read_text(encoding="utf-8").split("\n")
    blocks, _ = _parse_fenced_blocks(lines)

    heading_idx: int | None = next(
        (idx for idx, raw in enumerate(lines) if _GLOSSARY_HEADING_RE.match(raw)),
        None,
    )
    if heading_idx is None:
        return GLOSSARY_TERMS

    for block in blocks:
        # ``block.line`` is the 1-based opening-fence line; compare to the
        # 0-based heading index to keep only blocks that follow the heading.
        if block.language != "text" or block.line - 1 <= heading_idx:
            continue
        terms: list[str] = []
        for raw in block.body.split("\n"):
            term = raw.strip()
            if term and term not in terms:
                terms.append(term)
        if terms:
            return tuple(terms)
        break

    return GLOSSARY_TERMS


# ---------------------------------------------------------------------------
# Compliance rules (LDC001-LDC020)
# ---------------------------------------------------------------------------
#
# Rule-function convention
# ------------------------
# Rules come in two families, distinguished by what they inspect:
#
# 1. **Per-Topic_Doc rules** operate on one already-parsed ``TopicDoc`` and have
#    one of two signatures:
#
#      * ``check_ldcNNN(doc: TopicDoc) -> list[Finding]`` — a purely textual rule
#        that decides everything from the parsed document.
#      * ``check_ldcNNN(doc: TopicDoc, root: Path) -> list[Finding]`` — a rule
#        that must *resolve* repository paths (confirm an Implementation_File or
#        link target exists on disk), so it also needs the repository root.
#        ``check_ldc103`` is the first such rule; ``LDC005``, ``LDC006``,
#        ``LDC007``, ``LDC009``, ``LDC012``, ``LDC015`` follow the same shape.
#
#    These rules apply only to Topic_Docs — the Markdown files directly under
#    ``docs/learning/phase-1/`` other than ``README.md``. The CLI (task 2.46)
#    parses each candidate file once and dispatches it to every per-doc rule.
#
# 2. **Library-level rules** inspect the *index* files rather than a single
#    Topic_Doc: the Phase_1_Index (``docs/learning/phase-1/README.md``) and the
#    Library_Index (``docs/learning/README.md``), cross-referenced against the
#    on-disk state of the repository. They cannot be expressed over one parsed
#    ``TopicDoc`` and instead take the repository root directly:
#
#      * ``check_ldcNNN(root: Path) -> list[Finding]``
#
#    ``LDC016`` through ``LDC020`` (task 2.32) are the library-level rules. Each
#    reads the index file(s) it governs (via ``parse_phase_1_index``,
#    ``parse_topic_doc`` on the Phase_1_Index/Topic_Docs, or a section scan of
#    the Library_Index) and resolves paths under ``root``. The CLI invokes each
#    library-level rule exactly once per run, passing the discovered ``--root``,
#    rather than once per Topic_Doc.
#
# Common to both families: rules are pure, never mutate their input, never make
# network requests, and never short-circuit the whole run on a single failure —
# they just report. The CLI is responsible for selecting inputs, calling each
# rule, aggregating the findings, sorting them, and choosing the exit code.
#
# This file implements LDC001 and LDC002 (task 2.4), LDC003 (task 2.7), the
# per-section content rules LDC004 + LDC101-LDC105 (task 2.9), the file-reference
# and code-snippet rules LDC005-LDC008 (task 2.16), the link-integrity rules
# LDC009-LDC011 + LDC106 (task 2.21), the beginner-accessibility rules
# LDC012-LDC015 (task 2.26), and the library-level coverage/index rules
# LDC016-LDC020 (task 2.32). The argparse CLI is added in task 2.46, following
# the conventions above.

# Topic_Doc filename rule data (Conventions_Doc "Filename rules"; Req 2.9, 8.1).
# The pattern is the verbatim regex from the requirement: kebab-case stem made
# of lowercase-alphanumeric runs joined by single hyphens, with a ``.md``
# extension.
FILENAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.md$")
MAX_FILENAME_LENGTH: int = 80  # characters, including the '.md' extension
RESERVED_TOPIC_DOC_FILENAME: str = "README.md"  # the index file, never a Topic_Doc

# Topic_Doc H1-title length bounds (Conventions_Doc "Heading rules"; Req 8.2).
MIN_H1_TITLE_LENGTH: int = 3
MAX_H1_TITLE_LENGTH: int = 80


def check_ldc001(doc: TopicDoc) -> list[Finding]:
    """LDC001 — Topic_Doc filename conformance (Req 2.9, 8.1).

    The filename must be kebab-case matching :data:`FILENAME_PATTERN`, be at
    most :data:`MAX_FILENAME_LENGTH` characters including the ``.md`` extension,
    and must not be the reserved index name ``README.md``. Findings are
    file-level (``line == 0``).
    """
    name = doc.filename

    # The reserved index name fails the (lowercase-only) kebab-case pattern too;
    # report it as the single, most specific violation rather than two findings.
    if name == RESERVED_TOPIC_DOC_FILENAME:
        return [
            Finding(
                file=doc.path,
                line=0,
                rule_id="LDC001",
                requirement="Req 8.1",
                message=(
                    f"Topic_Doc filename {name!r} is the reserved name "
                    f"{RESERVED_TOPIC_DOC_FILENAME!r}; Topic_Docs must use a kebab-case name."
                ),
            )
        ]

    findings: list[Finding] = []

    if FILENAME_PATTERN.match(name) is None:
        findings.append(
            Finding(
                file=doc.path,
                line=0,
                rule_id="LDC001",
                requirement="Req 8.1",
                message=(
                    f"Topic_Doc filename {name!r} is not kebab-case; it must match "
                    f"{FILENAME_PATTERN.pattern!r}."
                ),
            )
        )

    if len(name) > MAX_FILENAME_LENGTH:
        findings.append(
            Finding(
                file=doc.path,
                line=0,
                rule_id="LDC001",
                requirement="Req 8.1",
                message=(
                    f"Topic_Doc filename {name!r} is {len(name)} characters; the maximum is "
                    f"{MAX_FILENAME_LENGTH} including the '.md' extension."
                ),
            )
        )

    return findings


def check_ldc002(doc: TopicDoc) -> list[Finding]:
    """LDC002 — Topic_Doc H1 conformance (Req 8.2).

    Line 1 must be a single H1 heading of the form ``# <title>`` whose title is
    between :data:`MIN_H1_TITLE_LENGTH` and :data:`MAX_H1_TITLE_LENGTH`
    characters, and line 2 must be blank (one blank line separates the title
    from the body). H1 problems are reported on line 1; the blank-line problem
    is reported on line 2, where it occurs.
    """
    findings: list[Finding] = []
    lines = doc.raw_lines

    first_line = lines[0] if lines else ""
    h1_match = _H1_RE.match(first_line)
    if h1_match is None:
        findings.append(
            Finding(
                file=doc.path,
                line=1,
                rule_id="LDC002",
                requirement="Req 8.2",
                message="Topic_Doc line 1 must be a single H1 heading of the form '# <title>'.",
            )
        )
    else:
        title = h1_match.group(1).strip()
        if not (MIN_H1_TITLE_LENGTH <= len(title) <= MAX_H1_TITLE_LENGTH):
            findings.append(
                Finding(
                    file=doc.path,
                    line=1,
                    rule_id="LDC002",
                    requirement="Req 8.2",
                    message=(
                        f"Topic_Doc H1 title must be between {MIN_H1_TITLE_LENGTH} and "
                        f"{MAX_H1_TITLE_LENGTH} characters; found {len(title)}."
                    ),
                )
            )

    second_line = lines[1] if len(lines) > 1 else None
    if second_line is None or second_line.strip() != "":
        findings.append(
            Finding(
                file=doc.path,
                line=2,
                rule_id="LDC002",
                requirement="Req 8.2",
                message="Topic_Doc line 2 must be blank (one blank line must follow the H1 title).",
            )
        )

    return findings


# Doc_Template H2-sequence rule data (Conventions_Doc "Doc_Template"; Req 3.1,
# 3.11, 8.3, 8.4, 8.5, 8.7). The canonical required sequence and the single
# optional heading with its legal "sandwich" position are the library-as-data
# constants ``DOC_TEMPLATE_REQUIRED`` and ``DOC_TEMPLATE_OPTIONAL`` defined
# above; the rule derives a set and a rank map from them so it stays in lockstep
# with that single source of truth.
_DOC_TEMPLATE_REQUIRED_SET: frozenset[str] = frozenset(DOC_TEMPLATE_REQUIRED)
_DOC_TEMPLATE_ORDER: dict[str, int] = {
    heading: index for index, heading in enumerate(DOC_TEMPLATE_REQUIRED)
}


def _drop_legal_optional(sections: list[Section]) -> list[Section]:
    """Return ``sections`` with one legally-placed optional H2 removed.

    An optional heading from :data:`DOC_TEMPLATE_OPTIONAL` is dropped only when
    it sits strictly between its required predecessor and successor in the H2
    sequence — its immediately preceding H2 equals the configured "must come
    after" heading and its immediately following H2 equals the configured "must
    come before" heading, with no other H2 in that gap. A single occurrence is
    dropped per optional heading; any other occurrence is left in place so the
    residual comparison flags it as misplaced.
    """
    result = list(sections)
    for optional, (after, before) in DOC_TEMPLATE_OPTIONAL.items():
        for idx in range(1, len(result) - 1):
            if (
                result[idx].heading == optional
                and result[idx - 1].heading == after
                and result[idx + 1].heading == before
            ):
                del result[idx]
                break
    return result


def check_ldc003(doc: TopicDoc) -> list[Finding]:
    """LDC003 — Doc_Template H2 sequence equality (Req 3.1, 3.11, 8.3, 8.4, 8.5, 8.7).

    Extracts the Topic_Doc's H2 headings in source order, drops a single
    legally-placed optional ``Hands-on checkpoint`` heading (one that sits
    immediately between ``Common pitfalls`` and ``External reading``), and
    requires the residual sequence to equal :data:`DOC_TEMPLATE_REQUIRED`. When
    the residual matches, the Topic_Doc is compliant and no findings are
    emitted; otherwise the rule reports one finding per deviation — a misplaced
    optional section, an unexpected extra section, a duplicate or missing
    required section, or a required section that appears out of order — citing
    the offending heading text and its line (line ``0`` for a wholly missing
    section).
    """
    residual = _drop_legal_optional(doc.sections)
    if tuple(section.heading for section in residual) == DOC_TEMPLATE_REQUIRED:
        return []

    findings: list[Finding] = []
    expected_order = ", ".join(DOC_TEMPLATE_REQUIRED)

    # Misplaced optional headings and unexpected extra headings: any residual H2
    # whose text is not one of the seven required sections.
    for section in residual:
        if section.heading in _DOC_TEMPLATE_REQUIRED_SET:
            continue
        if section.heading in DOC_TEMPLATE_OPTIONAL:
            after, before = DOC_TEMPLATE_OPTIONAL[section.heading]
            message = (
                f"Optional H2 {section.heading!r} is misplaced; it is allowed only "
                f"immediately between {after!r} and {before!r}."
            )
        else:
            message = (
                f"Unexpected H2 {section.heading!r} is not part of the Doc_Template; "
                f"the required sections are: {expected_order}."
            )
        findings.append(
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC003",
                requirement="Req 8.4",
                message=message,
            )
        )

    # Required sections in source order (those whose text is a required heading).
    present = [section for section in residual if section.heading in _DOC_TEMPLATE_REQUIRED_SET]

    # Missing required sections: required headings absent from the residual.
    present_headings = {section.heading for section in present}
    for required in DOC_TEMPLATE_REQUIRED:
        if required not in present_headings:
            findings.append(
                Finding(
                    file=doc.path,
                    line=0,
                    rule_id="LDC003",
                    requirement="Req 8.4",
                    message=(
                        f"Missing required H2 {required!r}; the required sections are: "
                        f"{expected_order}."
                    ),
                )
            )

    # Duplicate and out-of-order required sections. Walk the present required
    # headings once, tracking which have been seen and the highest canonical
    # rank reached so far: a repeat is a duplicate, and a heading whose rank is
    # below the running maximum is out of order.
    seen: set[str] = set()
    max_rank = -1
    for section in present:
        rank = _DOC_TEMPLATE_ORDER[section.heading]
        if section.heading in seen:
            findings.append(
                Finding(
                    file=doc.path,
                    line=section.line,
                    rule_id="LDC003",
                    requirement="Req 8.4",
                    message=(
                        f"Duplicate required H2 {section.heading!r}; each Doc_Template "
                        f"section must appear exactly once."
                    ),
                )
            )
            continue
        seen.add(section.heading)
        if rank < max_rank:
            findings.append(
                Finding(
                    file=doc.path,
                    line=section.line,
                    rule_id="LDC003",
                    requirement="Req 8.4",
                    message=(
                        f"H2 {section.heading!r} appears out of order; the required "
                        f"Doc_Template order is: {expected_order}."
                    ),
                )
            )
        else:
            max_rank = rank

    return findings


# ---------------------------------------------------------------------------
# Per-section content rules (task 2.9)
# ---------------------------------------------------------------------------
#
# These rules look inside individual Doc_Template sections. Each one first
# locates its target section (returning no findings when the section is absent —
# a missing required section is LDC003's responsibility, not theirs) and then
# checks the section body against the predicate the design fixes for it:
#
#   * ``LDC004``  How it works is implementation-agnostic   (Property 16, Req 3.5)
#   * ``LDC101``  Introduction declares >=3 learning outcomes (Property 14, Req 3.2)
#   * ``LDC102``  Mental model contains a concrete handhold   (Property 15, Req 3.4, 5.4)
#   * ``LDC103``  MatchLayer Phase 1 usage anchors to a file  (Property 17, Req 3.6)
#   * ``LDC104``  Common pitfalls has 3 labelled entries      (Property 18, Req 3.7)
#   * ``LDC105``  External reading link-count bounds          (Property 19, Req 3.8, 7.6)
#
# ``LDC004`` keeps the canonical four-digit rule id from the design's validator
# table. The five per-section content checks the table folds into the
# section-by-section requirements are given dedicated ``LDC1xx`` ids so they sit
# clear of the ``LDC005``-``LDC020`` range reserved for the file-reference,
# link, accessibility, and coverage rules implemented in tasks 2.16+.

# Doc_Template section names this task inspects (Conventions_Doc "Doc_Template";
# spelled exactly as they appear as H2 headings).
_SECTION_INTRODUCTION: str = "Introduction"
_SECTION_MENTAL_MODEL: str = "Mental model"
_SECTION_HOW_IT_WORKS: str = "How it works"
_SECTION_USAGE: str = "MatchLayer Phase 1 usage"
_SECTION_PITFALLS: str = "Common pitfalls"
_SECTION_EXTERNAL_READING: str = "External reading"

# LDC004 — strings forbidden in the implementation-agnostic ``How it works``
# section (Property 16, Req 3.5). These are the repository-root path prefixes
# plus the literal product name; the scan is a case-sensitive substring match
# outside fenced code blocks.
HOW_IT_WORKS_BANNED_STRINGS: tuple[str, ...] = (
    "apps/",
    "packages/",
    "infra/",
    "ml/",
    "tools/",
    ".kiro/",
    "docs/",
    "MatchLayer",
)

# LDC101 — a label that identifies the learning-outcomes list/paragraph, and the
# "sentence ending in a period" counter (Property 14). A sentence is a run of
# text containing at least one letter and terminated by a literal ``.``.
_LEARNING_OUTCOME_LABEL_RE: re.Pattern[str] = re.compile(r"learning[\s-]*outcomes?", re.IGNORECASE)
_SENTENCE_RE: re.Pattern[str] = re.compile(r"[^.\n]*[A-Za-z][^.\n]*\.")
_MIN_LEARNING_OUTCOMES: int = 3

# LDC102 — a numbered (ordered) list item, the diagram languages that count as a
# Mental model handhold, and a Markdown image reference (Property 15).
_ORDERED_LIST_ITEM_RE: re.Pattern[str] = re.compile(r"^\s*\d+[.)]\s+\S")
_MENTAL_MODEL_DIAGRAM_LANGUAGES: frozenset[str] = frozenset({"text", "mermaid"})
_IMAGE_REF_RE: re.Pattern[str] = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MIN_ORDERED_LIST_ITEMS: int = 3

# LDC103 — a path-like token (repo-root-relative POSIX path with at least one
# ``/`` separator) and the inline-code span it usually lives in (Property 17).
_PATH_LIKE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+$")
_INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`([^`]+)`")

# LDC104 — the three labelled parts every Common pitfalls entry must carry, each
# followed by non-empty text (Property 18). Matched case-sensitively, as fixed
# in the Conventions_Doc.
_PITFALL_LABELS: tuple[str, ...] = ("Mistake", "Symptom", "Recovery")
_MIN_PITFALL_ENTRIES: int = 3

# LDC105 — inclusive bounds on the Markdown hyperlink count in External reading
# (Property 19, Req 3.8/7.6).
_MIN_EXTERNAL_READING_LINKS: int = 1
_MAX_EXTERNAL_READING_LINKS: int = 10


def _find_section(doc: TopicDoc, heading: str) -> Section | None:
    """Return the first H2 ``Section`` whose heading equals ``heading``, or None."""
    return next((section for section in doc.sections if section.heading == heading), None)


def _resolves_under_root(root: Path, rel: str) -> bool:
    """Return True when ``rel`` is a repo-root-relative path to an existing file.

    ``rel`` must be a POSIX-style relative path (no leading ``/``, no backslash)
    and, once joined to ``root``, must resolve to an existing regular file that
    still lives under ``root`` (so a ``../`` escape is rejected). The filesystem
    is only read, never written.
    """
    rel = rel.strip()
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    try:
        resolved = (root / rel).resolve()
        root_resolved = root.resolve()
    except OSError:
        return False
    if not resolved.is_file():
        return False
    return root_resolved in resolved.parents


def check_ldc004(doc: TopicDoc) -> list[Finding]:
    """LDC004 — ``How it works`` is implementation-agnostic (Property 16, Req 3.5).

    Outside fenced code blocks, the ``How it works`` section body must contain no
    occurrence of any repository-root path prefix
    (:data:`HOW_IT_WORKS_BANNED_STRINGS`) or the literal product name
    ``MatchLayer``. The scan re-parses the section body to find its fenced
    regions (``Section.body`` is raw Markdown, fences included) and then matches
    each banned string case-sensitively on the prose lines. One finding is
    emitted per (line, banned string) occurrence, citing the document line.
    """
    section = _find_section(doc, _SECTION_HOW_IT_WORKS)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    _, fenced_indices = _parse_fenced_blocks(body_lines)

    findings: list[Finding] = []
    for body_idx, raw in enumerate(body_lines):
        if body_idx in fenced_indices:
            continue
        doc_line = section.line + body_idx + 1
        for banned in HOW_IT_WORKS_BANNED_STRINGS:
            if banned in raw:
                findings.append(
                    Finding(
                        file=doc.path,
                        line=doc_line,
                        rule_id="LDC004",
                        requirement="Req 3.5",
                        message=(
                            f"'How it works' must stay implementation-agnostic but references "
                            f"{banned!r}; move MatchLayer-specific paths and the product name "
                            f"into 'MatchLayer Phase 1 usage'."
                        ),
                    )
                )
    return findings


def check_ldc101(doc: TopicDoc) -> list[Finding]:
    """LDC101 — Introduction declares at least three learning outcomes (Property 14, Req 3.2).

    The ``Introduction`` body must (a) carry a label identifying its
    learning-outcomes list or paragraph, and (b) contain at least three sentences
    that each end with a period. The fenced regions of the body are excluded
    before both checks. A separate finding is emitted for the missing label and
    for an outcome shortfall so an author sees exactly which half failed.
    """
    section = _find_section(doc, _SECTION_INTRODUCTION)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    _, fenced_indices = _parse_fenced_blocks(body_lines)
    prose = "\n".join(
        raw for body_idx, raw in enumerate(body_lines) if body_idx not in fenced_indices
    )

    findings: list[Finding] = []

    if _LEARNING_OUTCOME_LABEL_RE.search(prose) is None:
        findings.append(
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC101",
                requirement="Req 3.2",
                message=(
                    "'Introduction' must aggregate its learning outcomes under a labelled list "
                    "or paragraph identified as the learning outcomes."
                ),
            )
        )

    sentence_count = len(_SENTENCE_RE.findall(prose))
    if sentence_count < _MIN_LEARNING_OUTCOMES:
        findings.append(
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC101",
                requirement="Req 3.2",
                message=(
                    f"'Introduction' must state at least {_MIN_LEARNING_OUTCOMES} learning "
                    f"outcomes as declarative sentences ending in '.'; found {sentence_count}."
                ),
            )
        )

    return findings


def check_ldc102(doc: TopicDoc) -> list[Finding]:
    """LDC102 — Mental model contains a concrete handhold (Property 15, Req 3.4, 5.4).

    The ``Mental model`` body must contain at least one of: (a) an ordered
    (numbered) Markdown list with at least three items, (b) a fenced block tagged
    ``text`` (ASCII art) or ``mermaid`` (a diagram), or (c) a Markdown image
    reference. The body is re-parsed so ordered-list and image scans ignore
    fenced content while the diagram check inspects the fenced blocks directly.
    A single finding is emitted when none of the three handholds is present.
    """
    section = _find_section(doc, _SECTION_MENTAL_MODEL)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    blocks, fenced_indices = _parse_fenced_blocks(body_lines)

    ordered_items = sum(
        1
        for body_idx, raw in enumerate(body_lines)
        if body_idx not in fenced_indices and _ORDERED_LIST_ITEM_RE.match(raw)
    )
    has_ordered_list = ordered_items >= _MIN_ORDERED_LIST_ITEMS

    has_diagram_block = any(block.language in _MENTAL_MODEL_DIAGRAM_LANGUAGES for block in blocks)

    has_image = any(
        body_idx not in fenced_indices and _IMAGE_REF_RE.search(raw)
        for body_idx, raw in enumerate(body_lines)
    )

    if has_ordered_list or has_diagram_block or has_image:
        return []

    return [
        Finding(
            file=doc.path,
            line=section.line,
            rule_id="LDC102",
            requirement="Req 3.4, 5.4",
            message=(
                "'Mental model' must contain a concrete handhold: a numbered list of at least "
                f"{_MIN_ORDERED_LIST_ITEMS} steps, a fenced 'text' or 'mermaid' diagram, or a "
                "Markdown image reference."
            ),
        )
    ]


def check_ldc103(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC103 — MatchLayer Phase 1 usage anchors to an Implementation_File (Property 17, Req 3.6).

    The ``MatchLayer Phase 1 usage`` body must (a) reference at least one
    Implementation_File path that resolves to an existing file under ``root``,
    and (b) contain at least one fenced code block whose adjacent ``Source:``
    citation names an existing Implementation_File. Candidate path references are
    drawn from inline-code spans (outside fenced blocks) that look like
    repository-root-relative paths, together with the ``Source:`` citations the
    parser attaches to fenced blocks. Resolving paths needs the repository root,
    so this rule takes the extra ``root`` argument.
    """
    section = _find_section(doc, _SECTION_USAGE)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    blocks, fenced_indices = _parse_fenced_blocks(body_lines)

    candidate_paths: set[str] = set()
    for body_idx, raw in enumerate(body_lines):
        if body_idx in fenced_indices:
            continue
        for match in _INLINE_CODE_RE.finditer(raw):
            token = match.group(1).strip()
            if _PATH_LIKE_RE.match(token):
                candidate_paths.add(token)
    for block in blocks:
        if block.source_path is not None:
            candidate_paths.add(block.source_path.as_posix())

    findings: list[Finding] = []

    if not any(_resolves_under_root(root, path) for path in candidate_paths):
        findings.append(
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC103",
                requirement="Req 3.6",
                message=(
                    "'MatchLayer Phase 1 usage' must reference at least one Implementation_File "
                    "path that resolves to an existing file under the repository root."
                ),
            )
        )

    has_sourced_block = any(
        block.source_path is not None and _resolves_under_root(root, block.source_path.as_posix())
        for block in blocks
    )
    if not has_sourced_block:
        findings.append(
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC103",
                requirement="Req 3.6",
                message=(
                    "'MatchLayer Phase 1 usage' must contain at least one fenced code block whose "
                    "adjacent 'Source:' citation references an existing Implementation_File."
                ),
            )
        )

    return findings


def check_ldc104(doc: TopicDoc) -> list[Finding]:
    """LDC104 — Common pitfalls has three labelled entries (Property 18, Req 3.7).

    The ``Common pitfalls`` body must contain at least three distinct entries,
    each labelling its ``Mistake:``, ``Symptom:``, and ``Recovery:`` parts with
    non-empty text after each label. The number of well-formed entries is taken
    as the minimum of the per-label counts (each label is matched followed by at
    least one non-space character), so a missing or empty label in an entry
    lowers the entry count. The labels are matched case-sensitively, as fixed in
    the Conventions_Doc. The body's fenced regions are excluded first.
    """
    section = _find_section(doc, _SECTION_PITFALLS)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    _, fenced_indices = _parse_fenced_blocks(body_lines)
    prose = "\n".join(
        raw for body_idx, raw in enumerate(body_lines) if body_idx not in fenced_indices
    )

    label_counts = [
        len(re.findall(rf"{re.escape(label)}\s*:\s*\S", prose)) for label in _PITFALL_LABELS
    ]
    entries = min(label_counts)

    if entries >= _MIN_PITFALL_ENTRIES:
        return []

    labels_display = ", ".join(f"{label}:" for label in _PITFALL_LABELS)
    return [
        Finding(
            file=doc.path,
            line=section.line,
            rule_id="LDC104",
            requirement="Req 3.7",
            message=(
                f"'Common pitfalls' must list at least {_MIN_PITFALL_ENTRIES} entries, each "
                f"labelling {labels_display} with non-empty text; found {entries} complete "
                f"entr{'y' if entries == 1 else 'ies'}."
            ),
        )
    ]


def check_ldc105(doc: TopicDoc) -> list[Finding]:
    """LDC105 — External reading link-count bounds (Property 19, Req 3.8, 7.6).

    The ``External reading`` body must contain between
    :data:`_MIN_EXTERNAL_READING_LINKS` and :data:`_MAX_EXTERNAL_READING_LINKS`
    Markdown hyperlinks inclusive. The body is re-parsed and links inside fenced
    code blocks are ignored. (The https-only scheme check and the
    external-specific count are dedicated rules added in task 2.21; this rule
    bounds the total Markdown hyperlink count.)
    """
    section = _find_section(doc, _SECTION_EXTERNAL_READING)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    _, fenced_indices = _parse_fenced_blocks(body_lines)
    links = _extract_links(body_lines, fenced_indices)
    count = len(links)

    if _MIN_EXTERNAL_READING_LINKS <= count <= _MAX_EXTERNAL_READING_LINKS:
        return []

    return [
        Finding(
            file=doc.path,
            line=section.line,
            rule_id="LDC105",
            requirement="Req 3.8, 7.6",
            message=(
                f"'External reading' must contain between {_MIN_EXTERNAL_READING_LINKS} and "
                f"{_MAX_EXTERNAL_READING_LINKS} Markdown hyperlinks; found {count}."
            ),
        )
    ]


# ---------------------------------------------------------------------------
# File-reference and code-snippet rules (task 2.16)
# ---------------------------------------------------------------------------
#
# These four rules tie a Topic_Doc's prose and code blocks back to the real
# repository, so three of them resolve paths and take the extra ``root``
# argument:
#
#   * ``LDC005``  Implementation_File path references resolve  (Property 27, Req 3.6, 6.1)
#   * ``LDC006``  Sourced fenced block matches its source       (Property 28, Req 3.6, 6.2, 6.7)
#   * ``LDC007``  Non-sourced block is simplified + linked       (Property 29, Req 6.3)
#   * ``LDC008``  Fenced blocks use an allowed language tag      (Property 30, Req 6.6)
#
# ``LDC008`` is purely textual (the language tag lives on the parsed block) and
# keeps the one-argument shape; ``LDC005``-``LDC007`` follow the two-argument
# path-resolving shape established by ``check_ldc103`` and reuse
# :func:`_resolves_under_root`.

# LDC005 — an inline-code token in the ``MatchLayer Phase 1 usage`` section that
# *looks like* an Implementation_File path reference (Property 27, Req 6.1). The
# pattern accepts an optional leading ``/`` or ``./`` run so malformed
# (non-repo-root-relative) references are still captured and flagged rather than
# silently ignored; a token must carry at least one ``/`` separator and contain
# only path characters, which keeps non-path inline code (``pool_pre_ping``,
# ``output: "standalone"``) out of the candidate set.
_USAGE_PATH_CANDIDATE_RE: re.Pattern[str] = re.compile(
    r"^[./]*[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+$"
)


def _is_wellformed_repo_path(rel: str) -> bool:
    """Return True when ``rel`` has the shape of a repo-root-relative POSIX path.

    A well-formed Implementation_File reference (Req 6.1) is non-empty, carries
    no leading ``/``, no ``./`` prefix, and no ``\\`` separator. This is a
    purely lexical check; :func:`_resolves_under_root` confirms the path exists.
    """
    rel = rel.strip()
    return bool(rel) and not rel.startswith("/") and not rel.startswith("./") and "\\" not in rel


def check_ldc005(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC005 — Implementation_File references resolve (Property 27, Req 3.6, 6.1).

    Every Implementation_File path the ``MatchLayer Phase 1 usage`` section
    references must be a POSIX-style, repository-root-relative path (no leading
    ``/``, no ``./`` prefix, no ``\\`` separator) that resolves to an existing
    file under ``root``. Candidate references are the path-like inline-code spans
    in the section prose together with the ``Source:`` citation paths attached to
    the section's fenced blocks (the same reference set :func:`check_ldc103`
    inspects). One finding is emitted per distinct malformed or non-resolving
    reference; a section that makes no path reference yields nothing here (the
    "at least one anchor" requirement is :func:`check_ldc103`'s job).
    """
    section = _find_section(doc, _SECTION_USAGE)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    blocks, fenced_indices = _parse_fenced_blocks(body_lines)

    # Collect candidate references as {token: first-seen document line}, so a
    # path referenced several times is reported once.
    candidates: dict[str, int] = {}
    for body_idx, raw in enumerate(body_lines):
        if body_idx in fenced_indices:
            continue
        doc_line = section.line + body_idx + 1
        for match in _INLINE_CODE_RE.finditer(raw):
            token = match.group(1).strip()
            if _USAGE_PATH_CANDIDATE_RE.match(token):
                candidates.setdefault(token, doc_line)
    for block in blocks:
        if block.source_path is not None:
            candidates.setdefault(block.source_path.as_posix(), section.line + block.line)

    findings: list[Finding] = []
    for token, line in candidates.items():
        if not _is_wellformed_repo_path(token):
            findings.append(
                Finding(
                    file=doc.path,
                    line=line,
                    rule_id="LDC005",
                    requirement="Req 3.6, 6.1",
                    message=(
                        f"Implementation_File reference {token!r} is not a POSIX-style "
                        f"repository-root-relative path (no leading '/', no './' prefix, no "
                        f"'\\' separator)."
                    ),
                )
            )
        elif not _resolves_under_root(root, token):
            findings.append(
                Finding(
                    file=doc.path,
                    line=line,
                    rule_id="LDC005",
                    requirement="Req 3.6, 6.1",
                    message=(
                        f"Implementation_File reference {token!r} does not resolve to an existing "
                        f"file under the repository root."
                    ),
                )
            )

    return findings


def _is_whole_line_subsequence(block_lines: list[str], file_lines: list[str]) -> bool:
    """Return True when ``block_lines`` is a whole-line subsequence of ``file_lines``.

    The block body is a whole-line subsequence when it can be obtained from the
    file by deleting zero or more whole lines: every block line matches a file
    line exactly (whitespace included), in order, with no line edited and no
    reordering (Req 6.2). Implemented as a single forward pass over the file.
    """
    file_idx = 0
    file_count = len(file_lines)
    for block_line in block_lines:
        while file_idx < file_count and file_lines[file_idx] != block_line:
            file_idx += 1
        if file_idx == file_count:
            return False
        file_idx += 1
    return True


def check_ldc006(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC006 — Sourced fenced block matches its source (Property 28, Req 3.6, 6.2, 6.7).

    Every fenced code block whose adjacent text carries a ``Source: `<path>` ``
    citation (anywhere in the Topic_Doc, not only the usage section) must name a
    source file that resolves under ``root`` and whose body is a whole-line
    subsequence of that file — obtainable by deleting zero or more whole lines,
    with no line edited (Req 6.2). The parser already validated the citation
    shape and captured ``source_path``, so this rule resolves the path and runs
    the subsequence check. One finding is emitted per offending block: either the
    cited path does not resolve or the body is not a whole-line subsequence of
    it.
    """
    findings: list[Finding] = []
    for block in doc.fenced_blocks:
        if block.source_path is None:
            continue

        rel = block.source_path.as_posix()
        if not _resolves_under_root(root, rel):
            findings.append(
                Finding(
                    file=doc.path,
                    line=block.line,
                    rule_id="LDC006",
                    requirement="Req 3.6, 6.2, 6.7",
                    message=(
                        f"Sourced code block cites {rel!r}, which does not resolve to an existing "
                        f"file under the repository root."
                    ),
                )
            )
            continue

        try:
            file_text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(
                Finding(
                    file=doc.path,
                    line=block.line,
                    rule_id="LDC006",
                    requirement="Req 3.6, 6.2, 6.7",
                    message=f"Sourced code block cites {rel!r}, which could not be read as text.",
                )
            )
            continue

        block_lines = block.body.split("\n") if block.body else []
        if not _is_whole_line_subsequence(block_lines, file_text.split("\n")):
            findings.append(
                Finding(
                    file=doc.path,
                    line=block.line,
                    rule_id="LDC006",
                    requirement="Req 3.6, 6.2, 6.7",
                    message=(
                        f"Sourced code block does not match {rel!r}; its body must be obtainable "
                        f"from the file by deleting whole lines only (no line edited)."
                    ),
                )
            )

    return findings


def _fence_close_index(lines: list[str], open_idx: int) -> int:
    """Return the 0-based index of the fence that closes the block opened at ``open_idx``.

    Mirrors :func:`_parse_fenced_blocks`' closing rule: the first delimiter with
    at least as many backticks and an empty info string. An unterminated fence
    is treated as running to the last line.
    """
    open_match = _FENCE_RE.match(lines[open_idx])
    if open_match is None:
        return open_idx
    open_ticks = open_match.group(1)
    for scan in range(open_idx + 1, len(lines)):
        close_match = _FENCE_RE.match(lines[scan])
        if (
            close_match is not None
            and len(close_match.group(1)) >= len(open_ticks)
            and close_match.group(2).strip() == ""
        ):
            return scan
    return len(lines) - 1


def _line_has_resolving_link(line: str, root: Path) -> bool:
    """Return True when ``line`` contains an internal Markdown link that resolves.

    Used by :func:`check_ldc007`: the link target's path component (its
    ``#fragment`` stripped) must be a repository-root-relative path that resolves
    to an existing file under ``root``.
    """
    for match in _LINK_RE.finditer(line):
        link = _make_link(match.group(1), match.group(2), 0)
        if not link.is_internal:
            continue
        target_path = link.target.partition("#")[0]
        if target_path and _resolves_under_root(root, target_path):
            return True
    return False


def check_ldc007(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC007 — Non-sourced block is simplified and linked (Property 29, Req 6.3).

    Every fenced code block that is not a verbatim-sourced block — i.e. one that
    carries no ``Source:`` citation, so it is not a whole-line copy of a named
    Implementation_File (cited blocks are :func:`check_ldc006`'s domain) — must be
    labelled as illustrative and point back to a real source: the immediately
    preceding non-empty line must contain the literal phrase ``simplified for
    illustration`` (captured by the parser as ``is_simplified``) and the
    immediately following non-empty line must contain a Markdown hyperlink whose
    target is a repository-root-relative path that resolves under ``root``. One
    finding is emitted per offending block, naming whichever half is missing.
    """
    findings: list[Finding] = []
    for block in doc.fenced_blocks:
        if block.source_path is not None:
            continue

        close_idx = _fence_close_index(doc.raw_lines, block.line - 1)
        following = _nearest_nonblank_after(doc.raw_lines, close_idx)
        has_link = following is not None and _line_has_resolving_link(following, root)

        if block.is_simplified and has_link:
            continue

        if not block.is_simplified and not has_link:
            detail = (
                "must be preceded by the phrase 'simplified for illustration' and followed by a "
                "Markdown link to a resolving repository path"
            )
        elif not block.is_simplified:
            detail = "must be preceded by the phrase 'simplified for illustration'"
        else:
            detail = "must be followed by a Markdown link to a resolving repository path"

        findings.append(
            Finding(
                file=doc.path,
                line=block.line,
                rule_id="LDC007",
                requirement="Req 6.3",
                message=(
                    f"Code block is not copied verbatim from a cited Implementation_File, so it "
                    f"{detail}."
                ),
            )
        )

    return findings


def check_ldc008(doc: TopicDoc) -> list[Finding]:
    """LDC008 — Fenced blocks use an allowed language tag (Property 30, Req 6.6).

    Every fenced code block's opening-fence language identifier must be a member
    of :data:`ALLOWED_LANGUAGES`. A missing tag (empty info string) or an
    unrecognised one is a finding, reported on the block's opening-fence line.
    """
    allowed_display = ", ".join(sorted(ALLOWED_LANGUAGES))
    findings: list[Finding] = []
    for block in doc.fenced_blocks:
        language = block.language.strip()
        if language in ALLOWED_LANGUAGES:
            continue
        shown = language if language else "<none>"
        findings.append(
            Finding(
                file=doc.path,
                line=block.line,
                rule_id="LDC008",
                requirement="Req 6.6",
                message=(
                    f"Fenced code block uses language tag {shown!r}; it must be one of the "
                    f"allowed identifiers: {allowed_display}."
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Link-integrity rules (task 2.21)
# ---------------------------------------------------------------------------
#
# These rules check that a Topic_Doc's hyperlinks point somewhere real and use
# the right scheme:
#
#   * ``LDC009``  Internal links resolve; fragments match a heading  (Props 11, 12; Req 7.1-7.3)
#   * ``LDC010``  External links use the ``https://`` scheme           (Property 31, Req 7.4)
#   * ``LDC011``  External reading has 1-10 external hyperlinks         (Req 7.6)
#   * ``LDC106``  Authoritative-source preference (advisory)           (Property 32, Req 7.5)
#
# ``LDC009`` resolves link targets *relative to the directory of the Topic_Doc*
# (Req 7.1, Property 11) — not relative to the repository root — so it needs the
# repo root only to confine resolution to the repository and takes the
# two-argument ``(doc, root)`` shape. ``LDC106`` reads the authoritative-host
# registry from ``CONVENTIONS.md`` and so also takes ``root``. ``LDC010`` and
# ``LDC011`` are purely textual and keep the one-argument shape. ``LDC106`` is an
# auxiliary advisory check (Req 7.5 has no dedicated id in the LDC001-LDC020
# table), so it takes an ``LDC1xx`` id alongside the per-section ``LDC101``-
# ``LDC105`` checks, clear of the canonical range.

# An ATX heading at any level (``#`` through ``######``); the captured group is
# the heading text. Used to slugify a target file's headings for the LDC009
# fragment check. Headings inside fenced code blocks are excluded by the caller.
_ATX_HEADING_RE: re.Pattern[str] = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$")

# The Conventions_Doc, relative to the repository root; LDC106 reads its
# authoritative-host registry via :func:`parse_authoritative_hosts`.
_CONVENTIONS_DOC_RELPATH: tuple[str, ...] = ("docs", "learning", "CONVENTIONS.md")

# The required external-link scheme prefix (Req 7.4, Property 31).
_HTTPS_SCHEME_PREFIX: str = "https://"


def _github_slug(heading: str) -> str:
    """Return the GitHub-style anchor slug for a Markdown heading.

    GitHub derives a heading's anchor by lowercasing the text, stripping
    punctuation (everything that is not a word character, whitespace, or
    hyphen — word characters keep underscores), and replacing each run of
    whitespace with a single hyphen. For example ``"MatchLayer Phase 1 usage"``
    becomes ``"matchlayer-phase-1-usage"`` and ``"How it works?"`` becomes
    ``"how-it-works"``.
    """
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    return slug


def _extract_heading_slugs(path: Path) -> frozenset[str]:
    """Return the GitHub-style anchor slugs of every heading in a Markdown file.

    Reads ``path`` (read-only), extracts every ATX heading outside fenced code
    blocks, and slugifies each with :func:`_github_slug`. Returns an empty set
    when the file cannot be read as text; the caller treats an empty set as
    "no matching heading" so an unreadable target still surfaces as a finding.
    """
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return frozenset()

    _, fenced_indices = _parse_fenced_blocks(lines)
    slugs: set[str] = set()
    for idx, raw in enumerate(lines):
        if idx in fenced_indices:
            continue
        match = _ATX_HEADING_RE.match(raw)
        if match:
            slugs.add(_github_slug(match.group(1).strip()))
    return frozenset(slugs)


def _resolve_internal_target(doc_path: Path, root: Path, path_part: str) -> Path | None:
    """Resolve an internal link's path part relative to the Topic_Doc's directory.

    Per Req 7.1 / Property 11 an internal link is interpreted relative to the
    directory of the file containing it (``doc_path.parent``), not the
    repository root. The path part must be a relative POSIX path: a leading
    ``/`` or a ``\\`` separator is rejected (returns ``None``). An empty
    ``path_part`` (a same-file ``#anchor``) resolves to ``doc_path`` itself.

    Returns the resolved path when it exists and still lives under ``root`` (so
    a ``../`` escape out of the repository is rejected), otherwise ``None``. The
    filesystem is only read, never written.
    """
    cleaned = path_part.strip()
    if cleaned.startswith("/") or "\\" in cleaned:
        return None

    target = doc_path if cleaned == "" else doc_path.parent / cleaned
    try:
        resolved = target.resolve()
        root_resolved = root.resolve()
    except OSError:
        return None

    if not resolved.exists():
        return None
    if resolved == root_resolved or root_resolved in resolved.parents:
        return resolved
    return None


def check_ldc009(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC009 — Internal hyperlink resolution and anchor matching (Properties 11, 12; Req 7.1-7.3).

    For every internal hyperlink in the Topic_Doc:

    * the link's path part (everything before any ``#`` fragment), interpreted
      relative to the Topic_Doc's own directory, must resolve to a path that
      exists in the repository (Property 11, Req 7.1/7.2); and
    * when the link carries a non-empty ``#fragment``, the resolved target must
      be a file containing a heading whose GitHub-style anchor slug equals the
      fragment (Property 12, Req 7.3).

    Resolution is relative to the document directory rather than the repository
    root, so this rule needs ``root`` only to confine resolution to the
    repository (rejecting a ``../`` escape). One finding is emitted per broken
    link and one per dangling fragment.
    """
    findings: list[Finding] = []
    for link in doc.internal_links:
        path_part = link.target.partition("#")[0]
        resolved = _resolve_internal_target(doc.path, root, path_part)

        if resolved is None:
            findings.append(
                Finding(
                    file=doc.path,
                    line=link.line,
                    rule_id="LDC009",
                    requirement="Req 7.1, 7.2",
                    message=(
                        f"Internal link target {link.target!r} does not resolve to an existing "
                        f"path in the repository when interpreted relative to "
                        f"{doc.path.parent.as_posix()!r}."
                    ),
                )
            )
            continue

        if not link.fragment:
            continue

        slugs = _extract_heading_slugs(resolved) if resolved.is_file() else frozenset()
        if link.fragment not in slugs:
            findings.append(
                Finding(
                    file=doc.path,
                    line=link.line,
                    rule_id="LDC009",
                    requirement="Req 7.3",
                    message=(
                        f"Internal link fragment '#{link.fragment}' does not match any heading in "
                        f"{resolved.name!r}; the fragment must equal a heading's GitHub-style "
                        f"anchor slug."
                    ),
                )
            )

    return findings


def check_ldc010(doc: TopicDoc) -> list[Finding]:
    """LDC010 — External hyperlinks use the https scheme (Property 31, Req 7.4).

    Every external hyperlink's target must begin with the literal scheme prefix
    ``https://``. Targets using ``http://``, a protocol-relative ``//host``
    prefix, or any other scheme (``ftp:``, ``mailto:`` …) are non-compliant. One
    finding is emitted per offending link, citing its line.
    """
    findings: list[Finding] = []
    for link in doc.external_links:
        if link.target.startswith(_HTTPS_SCHEME_PREFIX):
            continue
        findings.append(
            Finding(
                file=doc.path,
                line=link.line,
                rule_id="LDC010",
                requirement="Req 7.4",
                message=(
                    f"External link target {link.target!r} must use the 'https://' scheme "
                    f"exactly; 'http://', protocol-relative '//', and other schemes are "
                    f"non-compliant."
                ),
            )
        )
    return findings


def check_ldc011(doc: TopicDoc) -> list[Finding]:
    """LDC011 — External reading has 1-10 external hyperlinks (Req 7.6).

    The ``External reading`` section must contain between
    :data:`_MIN_EXTERNAL_READING_LINKS` and :data:`_MAX_EXTERNAL_READING_LINKS`
    *external* hyperlinks inclusive. This overlaps with :func:`check_ldc105`
    (which bounds the section's total Markdown-hyperlink count) but is enforced
    as a dedicated rule that counts only external links for a clearer finding.
    The section body is re-parsed and links inside fenced code blocks are
    ignored. A missing section yields no finding (LDC003 owns section presence).
    """
    section = _find_section(doc, _SECTION_EXTERNAL_READING)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    _, fenced_indices = _parse_fenced_blocks(body_lines)
    links = _extract_links(body_lines, fenced_indices)
    count = sum(1 for link in links if not link.is_internal)

    if _MIN_EXTERNAL_READING_LINKS <= count <= _MAX_EXTERNAL_READING_LINKS:
        return []

    return [
        Finding(
            file=doc.path,
            line=section.line,
            rule_id="LDC011",
            requirement="Req 7.6",
            message=(
                f"'External reading' must list between {_MIN_EXTERNAL_READING_LINKS} and "
                f"{_MAX_EXTERNAL_READING_LINKS} external hyperlinks; found {count}."
            ),
        )
    ]


def check_ldc106(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC106 — Authoritative-source preference (advisory) (Property 32, Req 7.5).

    Compares each external hyperlink's host against the authoritative-host
    registry declared in ``CONVENTIONS.md`` (read via
    :func:`parse_authoritative_hosts`, which falls back to
    :data:`AUTHORITATIVE_HOSTS` when the file or block is unreadable). When a
    link points at a host that is **not** in the registry, an *advisory* finding
    is emitted: the Conventions_Doc requires linking to an authoritative source
    (official product/library documentation, a standards-body specification, a
    first-party site, or a registered canonical host) in preference to a
    secondary tutorial, blog post, or aggregator whenever one exists for the
    topic (Property 32, Req 7.5).

    The finding is advisory — like the ``LDC012``/``LDC013`` accessibility
    heuristics, it flags links a reviewer should confirm rather than asserting a
    hard violation. Links without a host (e.g. ``mailto:``) are skipped.
    """
    conventions_path = root.joinpath(*_CONVENTIONS_DOC_RELPATH)
    registry = frozenset(parse_authoritative_hosts(conventions_path))

    findings: list[Finding] = []
    for link in doc.external_links:
        host = urlsplit(link.target).hostname
        if host is None or host in registry:
            continue
        findings.append(
            Finding(
                file=doc.path,
                line=link.line,
                rule_id="LDC106",
                requirement="Req 7.5",
                message=(
                    f"Advisory: external link host {host!r} is not in the authoritative-host "
                    f"registry; when an authoritative source exists for this topic, link to it "
                    f"instead of a secondary tutorial, blog post, or aggregator."
                ),
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Beginner-accessibility rules (task 2.26)
# ---------------------------------------------------------------------------
#
# These rules enforce the Conventions_Doc "Beginner-accessibility ruleset" so a
# Topic_Doc stays readable by the Reader (a junior developer with zero prior
# knowledge of the topic):
#
#   * ``LDC012``  Domain term defined on first use (advisory)  (Property 22, Req 5.2)
#   * ``LDC013``  Acronym expanded on first use (advisory)      (Property 23, Req 5.3)
#   * ``LDC014``  No banned phrases outside fenced blocks        (Property 24, Req 5.5)
#   * ``LDC015``  Prerequisites declared in the Introduction      (Property 25, Req 5.6)
#
# ``LDC012`` and ``LDC013`` are *heuristic* checks the Conventions_Doc marks as
# advisory: their findings flag prose a reviewer should confirm rather than
# asserting a hard violation, so — like ``LDC106`` — their messages open with
# ``Advisory:``. ``LDC014``'s ``just`` case is likewise downgraded to an advisory
# warning (the Conventions_Doc documents ``just`` as advisory because it has
# legitimate non-presuming uses); every other banned phrase is a hard finding.
#
# ``LDC012`` reads the project-glossary list from ``CONVENTIONS.md`` (via
# :func:`parse_glossary_terms`) and ``LDC015`` resolves prerequisite link targets
# to Topic_Docs under ``docs/learning/phase-1/``; both therefore take the
# two-argument ``(doc, root)`` shape. ``LDC013`` and ``LDC014`` are purely textual
# and keep the one-argument shape.

# LDC012 — definition-delimiter detectors (Property 22, Req 5.2). A glossary
# term's first-use paragraph satisfies the rule when it carries a parenthetical
# ``(...)``, an em-dash clause, or a copular ``is``/``are`` clause introducing the
# definition. The em-dash is the typographic U+2014 character the Conventions_Doc
# uses in its examples.
_PARENTHETICAL_RE: re.Pattern[str] = re.compile(r"\([^)]*\)")
_COPULAR_RE: re.Pattern[str] = re.compile(r"\b(?:is|are)\b")
_EM_DASH: str = "\u2014"

# LDC013 — an acronym is a run of two or more capital letters surrounded by word
# boundaries (Property 23, Req 5.3).
_ACRONYM_RE: re.Pattern[str] = re.compile(r"\b[A-Z]{2,}\b")

# LDC015 — the explicit "no prerequisites" escape hatch and the label that
# identifies a prerequisites list (Property 25, Req 5.6). Both are matched
# case-insensitively; the no-prerequisites sentence is checked first because it
# also contains the substring the label regex looks for.
_NO_PREREQUISITES_RE: re.Pattern[str] = re.compile(r"no\s+prerequisites?", re.IGNORECASE)
_PREREQUISITE_LABEL_RE: re.Pattern[str] = re.compile(r"prerequisite", re.IGNORECASE)


def _strip_inline_code(text: str) -> str:
    """Return ``text`` with each inline-code span replaced by equal-length spaces.

    Acronyms and glossary terms that appear inside a `` `code` `` span are code
    identifiers, not prose, so the accessibility heuristics ignore them. Spans
    are blanked with spaces of the same width so column positions in the line are
    preserved for the acronym context check in :func:`check_ldc013`.
    """
    return _INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), text)


def _prose_paragraphs(
    raw_lines: list[str], fenced_indices: frozenset[int]
) -> list[list[tuple[int, str]]]:
    """Group a document's prose lines into paragraphs of ``(line_no, text)`` pairs.

    A paragraph is a run of consecutive non-blank prose lines. Blank lines,
    lines inside fenced code blocks, and ATX heading lines all act as paragraph
    boundaries and are excluded, so a glossary term's first *in-prose* use is
    located in the body text rather than in a section title. Each retained line's
    text has its inline-code spans blanked by :func:`_strip_inline_code`; the
    ``line_no`` is 1-based.
    """
    paragraphs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for idx, raw in enumerate(raw_lines):
        is_break = idx in fenced_indices or raw.strip() == "" or _ATX_HEADING_RE.match(raw)
        if is_break:
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append((idx + 1, _strip_inline_code(raw)))
    if current:
        paragraphs.append(current)
    return paragraphs


def _glossary_term_pattern(term: str) -> re.Pattern[str]:
    """Compile a case-insensitive, boundary-aware matcher for a glossary term.

    A word boundary is required on whichever end of the term is alphanumeric, so
    ``container`` matches whole words only while a term that begins or ends with
    punctuation (e.g. ``accessibility (axe-core)``) is matched literally on that
    side.
    """
    escaped = re.escape(term)
    prefix = r"\b" if term[:1].isalnum() else ""
    suffix = r"\b" if term[-1:].isalnum() else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def _has_definition_delimiter(paragraph: str) -> bool:
    """Return True when ``paragraph`` carries a definition delimiter (Property 22).

    The delimiters the Conventions_Doc accepts for introducing a definition are a
    parenthetical ``(...)``, an em-dash clause, or a copular ``is``/``are`` clause.
    """
    return (
        _PARENTHETICAL_RE.search(paragraph) is not None
        or _EM_DASH in paragraph
        or _COPULAR_RE.search(paragraph) is not None
    )


def check_ldc012(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC012 — Domain term defined on first use (advisory) (Property 22, Req 5.2).

    For each term in the project glossary declared in ``CONVENTIONS.md`` (read via
    :func:`parse_glossary_terms`, which falls back to :data:`GLOSSARY_TERMS` when
    the file or block is unreadable), the rule finds the term's first in-prose
    occurrence in the Topic_Doc — outside fenced code blocks, inline-code spans,
    and headings — and checks that the surrounding paragraph carries a definition
    delimiter (a parenthetical, an em-dash clause, or a copular ``is``/``are``
    clause). Terms that never appear are skipped.

    The check is a heuristic the Conventions_Doc marks advisory, so each finding's
    message opens with ``Advisory:`` to signal a reviewer should confirm it rather
    than treat it as a hard violation. Reading the glossary needs the repository
    root, so this rule takes the extra ``root`` argument.
    """
    conventions_path = root.joinpath(*_CONVENTIONS_DOC_RELPATH)
    glossary = parse_glossary_terms(conventions_path)

    _, fenced_indices = _parse_fenced_blocks(doc.raw_lines)
    paragraphs = _prose_paragraphs(doc.raw_lines, fenced_indices)

    findings: list[Finding] = []
    for term in glossary:
        pattern = _glossary_term_pattern(term)
        for paragraph in paragraphs:
            first_hit = next(
                ((line_no, text) for line_no, text in paragraph if pattern.search(text)),
                None,
            )
            if first_hit is None:
                continue

            paragraph_text = " ".join(text for _, text in paragraph)
            if not _has_definition_delimiter(paragraph_text):
                line_no, _ = first_hit
                findings.append(
                    Finding(
                        file=doc.path,
                        line=line_no,
                        rule_id="LDC012",
                        requirement="Req 5.2",
                        message=(
                            f"Advisory: glossary term {term!r} is used here without a "
                            f"same-paragraph definition on first use; introduce it with a "
                            f"parenthetical, an em-dash clause, or a copular 'is'/'are' clause."
                        ),
                    )
                )
            break  # only the first occurrence of each term is checked

    return findings


def check_ldc013(doc: TopicDoc) -> list[Finding]:
    """LDC013 — Acronym expanded on first use (advisory) (Property 23, Req 5.3).

    Scans the Topic_Doc's prose (outside fenced code blocks, inline-code spans,
    and headings) for acronyms — runs of two or more capital letters surrounded by
    word boundaries — and, for the first occurrence of each distinct acronym,
    checks it is introduced in the form ``Expanded Form (ACRONYM)``: the acronym
    sits in parentheses ``(A)`` immediately preceded by an expansion. Later
    occurrences of an already-introduced acronym are not checked.

    The check is a heuristic the Conventions_Doc marks advisory, so each finding's
    message opens with ``Advisory:``. The rule is purely textual and takes only
    the parsed Topic_Doc.
    """
    _, fenced_indices = _parse_fenced_blocks(doc.raw_lines)
    paragraphs = _prose_paragraphs(doc.raw_lines, fenced_indices)

    findings: list[Finding] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        for line_no, text in paragraph:
            for match in _ACRONYM_RE.finditer(text):
                acronym = match.group(0)
                if acronym in seen:
                    continue
                seen.add(acronym)

                start, end = match.start(), match.end()
                preceded_by_paren = start > 0 and text[start - 1] == "("
                followed_by_paren = end < len(text) and text[end] == ")"
                has_expansion = preceded_by_paren and bool(re.search(r"\w\s*$", text[: start - 1]))
                if preceded_by_paren and followed_by_paren and has_expansion:
                    continue

                findings.append(
                    Finding(
                        file=doc.path,
                        line=line_no,
                        rule_id="LDC013",
                        requirement="Req 5.3",
                        message=(
                            f"Advisory: acronym {acronym!r} is used here without an expanded form "
                            f"on first use; introduce it as 'Expanded Form ({acronym})'."
                        ),
                    )
                )

    return findings


def check_ldc014(doc: TopicDoc) -> list[Finding]:
    """LDC014 — Banned phrases are absent (Property 24, Req 5.5).

    Outside fenced code blocks, no phrase in :data:`BANNED_PHRASES` may appear as
    a case-insensitive whole-word match. One finding is emitted per occurrence,
    citing the offending line. The phrase ``just`` is downgraded to an advisory
    warning (the Conventions_Doc documents it as advisory because it has
    legitimate non-presuming uses such as "just-in-time" or "just below"); its
    message opens with ``Advisory:`` while every other banned phrase is reported
    as a hard finding.
    """
    _, fenced_indices = _parse_fenced_blocks(doc.raw_lines)

    patterns = [
        (phrase, re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE))
        for phrase in BANNED_PHRASES
    ]

    findings: list[Finding] = []
    for idx, raw in enumerate(doc.raw_lines):
        if idx in fenced_indices:
            continue
        line_no = idx + 1
        for phrase, pattern in patterns:
            for _ in pattern.finditer(raw):
                if phrase == "just":
                    message = (
                        "Advisory: the word 'just' is a knowledge-presuming filler here; the "
                        "Conventions_Doc surfaces it as a warning for reviewer triage because it "
                        "also has legitimate non-presuming uses."
                    )
                else:
                    message = (
                        f"Banned knowledge-presuming phrase {phrase!r} must not appear outside "
                        f"fenced code blocks."
                    )
                findings.append(
                    Finding(
                        file=doc.path,
                        line=line_no,
                        rule_id="LDC014",
                        requirement="Req 5.5",
                        message=message,
                    )
                )

    return findings


def _resolves_to_topic_doc(doc_path: Path, root: Path, target: str) -> bool:
    """Return True when ``target`` resolves to another Topic_Doc under phase-1.

    A prerequisite link target (its ``#fragment`` stripped), interpreted relative
    to ``doc_path``'s directory, must resolve to an existing ``.md`` file that
    lives directly under ``docs/learning/phase-1/``, is not the index
    ``README.md``, and is not ``doc_path`` itself ("another" Topic_Doc). The
    filesystem is only read, never written.
    """
    path_part = target.partition("#")[0]
    resolved = _resolve_internal_target(doc_path, root, path_part)
    if resolved is None or not resolved.is_file():
        return False
    if resolved.suffix != ".md" or resolved.name == RESERVED_TOPIC_DOC_FILENAME:
        return False

    try:
        phase_1_dir = (root / "docs" / "learning" / "phase-1").resolve()
    except OSError:
        return False
    if resolved.parent != phase_1_dir:
        return False

    try:
        return resolved != doc_path.resolve()
    except OSError:
        return False


def check_ldc015(doc: TopicDoc, root: Path) -> list[Finding]:
    """LDC015 — Prerequisites declared in the Introduction (Property 25, Req 5.6).

    The ``Introduction`` section must declare prerequisites in one of two ways:

    * an explicit "no prerequisites" sentence (case-insensitive); or
    * a hyperlinked prerequisite list — at least one internal link in the
      Introduction, every one of which resolves to *another* Topic_Doc under
      ``docs/learning/phase-1/`` (via :func:`_resolves_to_topic_doc`).

    A missing ``Introduction`` yields no finding (section presence is
    :func:`check_ldc003`'s responsibility). When the section neither states
    "no prerequisites" nor declares a hyperlinked list, one structural finding is
    emitted; when a declared prerequisite link does not resolve to a Topic_Doc,
    one finding is emitted per offending link. Resolving link targets needs the
    repository root, so this rule takes the extra ``root`` argument.
    """
    section = _find_section(doc, _SECTION_INTRODUCTION)
    if section is None:
        return []

    body_lines = section.body.split("\n")
    _, fenced_indices = _parse_fenced_blocks(body_lines)
    prose = "\n".join(
        raw for body_idx, raw in enumerate(body_lines) if body_idx not in fenced_indices
    )

    # Escape hatch (b): an explicit "no prerequisites" sentence.
    if _NO_PREREQUISITES_RE.search(prose) is not None:
        return []

    prerequisites = doc.prerequisites
    has_label = _PREREQUISITE_LABEL_RE.search(prose) is not None

    if not prerequisites:
        return [
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC015",
                requirement="Req 5.6",
                message=(
                    "'Introduction' must declare prerequisites as a hyperlinked list of "
                    "Topic_Docs or state 'No prerequisites' explicitly; it does neither."
                ),
            )
        ]

    findings: list[Finding] = []

    if not has_label:
        findings.append(
            Finding(
                file=doc.path,
                line=section.line,
                rule_id="LDC015",
                requirement="Req 5.6",
                message=(
                    "'Introduction' lists internal links but does not identify them as a "
                    "Prerequisites list; label the list 'Prerequisites' or state 'No "
                    "prerequisites' explicitly."
                ),
            )
        )

    for link in prerequisites:
        if not _resolves_to_topic_doc(doc.path, root, link.target):
            findings.append(
                Finding(
                    file=doc.path,
                    line=link.line,
                    rule_id="LDC015",
                    requirement="Req 5.6",
                    message=(
                        f"Prerequisite link target {link.target!r} does not resolve to another "
                        f"Topic_Doc under 'docs/learning/phase-1/'."
                    ),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Library-level coverage and index rules (task 2.32): LDC016-LDC020
# ---------------------------------------------------------------------------
#
# Unlike every per-Topic_Doc rule above, these five rules inspect the *index*
# files of the library — the Phase_1_Index (``docs/learning/phase-1/README.md``)
# and the Library_Index (``docs/learning/README.md``) — cross-referenced against
# the on-disk state of the repository. They cannot be expressed over a single
# parsed ``TopicDoc``, so per the rule-function convention documented near the
# top of this rules block they take the repository root directly:
#
#     check_ldcNNN(root: Path) -> list[Finding]
#
# The CLI (task 2.46) calls each of these exactly once per run with the
# discovered ``--root``, rather than once per Topic_Doc. Like the per-doc rules
# they are pure, read-only, and never raise on missing/garbled input — a missing
# index file simply yields the findings that its absence implies.

# Locations of the index files and the Phase 1 sub-library, relative to the
# repository root. ``root.joinpath(*RELPATH)`` rebuilds each concrete path.
_LEARNING_DIR_RELPATH: tuple[str, ...] = ("docs", "learning")
_LIBRARY_INDEX_RELPATH: tuple[str, ...] = ("docs", "learning", "README.md")
_PHASE_1_DIR_RELPATH: tuple[str, ...] = ("docs", "learning", "phase-1")
_PHASE_1_INDEX_RELPATH: tuple[str, ...] = ("docs", "learning", "phase-1", "README.md")

# The twelve Phase_1_Index thematic-section H2 headings, in the canonical order
# the design fixes (Design §"Phase_1_Index", Property 6). ``LDC017`` scans these
# (and only these) sections for Topic_Doc entries — the ``Topic coverage`` table
# and ``Recommended reading order`` are deliberately excluded.
PHASE_1_THEMATIC_SECTIONS: tuple[str, ...] = (
    "Foundation and tooling",
    "Frontend",
    "Backend",
    "API and data conventions",
    "Security",
    "Authentication and accounts",
    "Database and storage",
    "Matching and scoring",
    "Testing and quality",
    "Containerization",
    "Contracts and codegen",
    "Hosting and deploy",
)

# Library_Index H2 headings the library-level rules look up. ``Non-goals`` may be
# titled ``What this is not`` (permitted by the Library_Index authoring task), so
# its lookup accepts either spelling.
_LIBRARY_PHASE_SUBLIBRARIES_HEADINGS: tuple[str, ...] = ("Phase Sub-Libraries",)
_LIBRARY_EXTERNAL_SOURCES_HEADINGS: tuple[str, ...] = ("External Sources",)
_LIBRARY_NON_GOALS_HEADINGS: tuple[str, ...] = ("Non-goals", "What this is not")

# A child of ``docs/learning/`` that is a phase sub-library: ``phase-<N>`` for
# 1 <= N <= 7 (Design §"Library_Index", Req 1.4).
_PHASE_DIR_RE: re.Pattern[str] = re.compile(r"^phase-([1-7])$")

# The canonical Phase_1_Topic_Coverage_List (Req 4.2-4.13), reproduced verbatim
# from the design's "Phase_1_Topic_Coverage_List -> Topic_Doc mapping" (Design
# §6) — and matching the ``Coverage entry`` column the Phase_1_Index seeds. Each
# string is one coverage-list entry; ``LDC016`` asserts every one of them has at
# least one ``Topic coverage`` row naming an authored Topic_Doc filename. Kept as
# data here (rather than re-derived from the index) so a row silently dropped
# from the table is still caught. Backticks and quotes are part of the entry
# text because the Markdown table cell carries them literally.
PHASE_1_COVERAGE_ENTRIES: tuple[str, ...] = (
    # Foundation and tooling (Req 4.2)
    "Monorepo concept and apps-vs-packages split",
    "pnpm and pnpm workspaces",
    "uv as a Python package manager",
    "Node.js + Python version pinning",
    "Root `package.json` and `tsconfig.base.json`",
    "`.editorconfig`",
    "Lockfiles and frozen-lockfile installs",
    "`.env`, `.env.example`, env-drift script",
    "Pre-commit hooks",
    "corepack pin activating the root `packageManager` pnpm version",
    'Next.js `output: "standalone"` build mode',
    # Frontend (Req 4.3)
    "Next.js App Router + Server vs Client Components",
    "TypeScript strict mode + repo compiler options",
    "Tailwind v4 + `@theme inline` token strategy",
    "shadcn/ui as a copy-in primitive library",
    "Geist Sans + Geist Mono via `next/font`",
    "Framer Motion + reduced-motion pattern",
    "`next-themes` + system-default theme",
    "Security-headers proxy (Next.js 16 `proxy.ts`)",
    "WCAG AA color contrast",
    # Backend (Req 4.4)
    "FastAPI as async ASGI + application-factory pattern",
    "Pydantic v2 + `pydantic-settings`",
    "Async Python and the asyncio model",
    "SQLAlchemy 2.x async + per-request session",
    "Connection pooling + `pool_pre_ping`",
    "Alembic migrations + empty baseline",
    "`structlog` and structured JSON logging",
    "Request-id ASGI middleware + `X-Request-Id`",
    "RFC 7807 error envelope",
    "OpenAPI dump CLI",
    # Security (Req 4.5)
    "Security headers (CSP, HSTS, etc.)",
    "CORS allowlists",
    "Structured logging as PII defense + redaction",
    "Secrets management, gitleaks, .env discipline",
    "Dependency + supply-chain scanning",
    "Threat-model categories",
    "Non-indexing of PII surfaces as a privacy control",
    # Authentication and accounts (Req 4.6)
    "JWT and PyJWT; access vs refresh tokens; HS256 allowlist",
    "Argon2id password hashing + common-password blocklist",
    "Refresh-token rotation + family reuse detection",
    "Double-submit-cookie CSRF + HttpOnly/Secure/SameSite",
    "Redis sliding-window rate limiting + account lockout",
    "Append-only audit log",
    "Password-reset tokens + dev-only reset surface",
    "TanStack Query + `useAuth` server-state hook",
    "Authenticated route-group shell `(app)` + redirect",
    "No-account-enumeration via dummy-hash timing + generic error",
    # Database and storage (Req 4.7)
    "PostgreSQL 16 fundamentals",
    "Postgres vs MinIO and why Phase 1 uses both",
    "Redis fundamentals + Phase 1 standby",
    "Named Docker volumes + persistence",
    "Future addition of pgvector in Phase 2",
    # Matching and scoring (Req 4.8)
    "ATS match score in Phase 1 + deterministic non-LLM approach",
    "TF-IDF + cosine similarity via scikit-learn",
    "Keyword/skill overlap + committed Skill_Lexicon",
    "Rule-based suggestion generation",
    "File-upload safety (magic-byte MIME, size, UUID keys)",
    "Bounded PDF/DOCX text extraction",
    "S3/MinIO storage abstraction",
    "Per-user daily upload/scoring quotas (cost-as-DoS)",
    "`ml/` vs `apps/api` separation + Scorer_Version",
    "Zod runtime validation generated from OpenAPI",
    "Skill-lexicon build pipeline (`ml/pipelines/build_skill_lexicon.py`)",
    "Lexicon drift check (`tools/check_lexicon_drift.py`)",
    "Zip-bomb / decompression-bomb defense",
    # Containerization (Req 4.9)
    "Containers vs virtual machines",
    "Docker images, layers, build cache",
    "Dockerfiles + multi-stage builds",
    "`docker compose` + healthchecks + `--wait`",
    "Production Dockerfiles in `infra/docker/`",
    "Distroless + non-root + read-only runtime",
    "Image digest pinning",
    # Contracts and codegen (Req 4.10)
    "OpenAPI generation by FastAPI",
    "Codegen orchestrator + `execa`",
    "`openapi-typescript`",
    "`openapi-zod-client`",
    "Curated `index.ts` re-export pattern",
    "OpenAPI drift check in CI",
    # Hosting and deploy (Req 4.11)
    "GitHub Actions workflow structure",
    "The five Phase 1 CI jobs",
    "Dependabot configuration",
    "Branch protection + required-checks aggregator",
    "Vercel hobby tier as Phase 1 frontend host",
    "Fly.io as Phase 1 backend host",
    "AWS S3 as Phase 1 file-storage backend",
    "Phase 6 AWS migration-path preservation",
    # API and data conventions (Req 4.12)
    "UUIDv7 time-ordered opaque identifiers",
    "Soft-delete via `deleted_at` timestamp",
    "Cursor-based pagination (`?limit=&cursor=`)",
    "Idempotency keys via `Idempotency-Key` header",
    "`/api/v1` versioning, plural resources, ISO 8601 UTC timestamps",
    # Testing and quality (Req 4.13)
    "pytest, pytest-asyncio, httpx backend testing",
    "Integration testing against real Postgres in Docker",
    "Vitest + Testing Library frontend component tests",
    "Playwright end-to-end (E2E) tests",
    "Hypothesis property-based testing for the Reader",
    "Test taxonomy of layers across Phase 1",
    "axe-core accessibility tests",
    "Import-boundary tests enforcing apps/packages/ml separation",
    "Timing-category tests for no-account-enumeration equalization",
)


def _index_section_span(
    doc: TopicDoc, headings: tuple[str, ...]
) -> tuple[Section, int, int] | None:
    """Return the first H2 section whose heading is in ``headings`` plus its span.

    The span is the 1-based half-open line range ``(start, end)`` covering the
    section body: ``start`` is the heading line and ``end`` is the next H2's
    heading line (or one past the last line for the final section). Returns
    ``None`` when no section matches any accepted heading.
    """
    for idx, section in enumerate(doc.sections):
        if section.heading in headings:
            start = section.line
            if idx + 1 < len(doc.sections):
                end = doc.sections[idx + 1].line
            else:
                end = len(doc.raw_lines) + 1
            return section, start, end
    return None


def _internal_links_in_span(doc: TopicDoc, start: int, end: int) -> list[Link]:
    """Return the internal (relative) links whose line falls within ``(start, end)``.

    ``start`` is the section's heading line (which carries no entry link) and
    ``end`` is exclusive, so the result is exactly the links in the section body.
    External (``https://`` …) links are excluded — every library-level rule that
    consumes this list is concerned only with relative repository links.
    """
    return [link for link in doc.internal_links if start < link.line < end]


def check_ldc016(root: Path) -> list[Finding]:
    """LDC016 — Topic coverage maps to existing Topic_Docs (Property 20, Req 4.1, 4.14, 4.16).

    Two complementary checks over the Phase_1_Index ``Topic coverage`` table
    (read via :func:`parse_phase_1_index`):

    * **(a)** every row whose ``Topic_Doc filename`` cell is non-empty must name
      a file that exists directly under ``docs/learning/phase-1/`` (Req 4.16); a
      blank cell — a not-yet-authored Topic_Doc — is skipped by this half.
    * **(b)** every entry in the canonical :data:`PHASE_1_COVERAGE_ENTRIES`
      (Req 4.2-4.13) must have at least one row naming a non-empty filename, so
      the coverage list is exhaustively assigned (Req 4.14).

    Until Topic_Docs are authored the filename column is blank, so (a) is silent
    while (b) reports one finding per coverage entry. That is the expected,
    tolerated state at the Section 3 checkpoint. Findings are file-level (line 0)
    because ``CoverageRow`` carries no source line; each message names the row.
    """
    index_path = root.joinpath(*_PHASE_1_INDEX_RELPATH)
    phase_1_dir = root.joinpath(*_PHASE_1_DIR_RELPATH)
    rows = parse_phase_1_index(index_path)

    findings: list[Finding] = []

    # (a) Every populated filename must resolve to a file directly under phase-1.
    for row in rows:
        filename = row.topic_doc_filename.strip()
        if not filename:
            continue
        is_bare = "/" not in filename and "\\" not in filename
        if not is_bare or not (phase_1_dir / filename).is_file():
            findings.append(
                Finding(
                    file=index_path,
                    line=0,
                    rule_id="LDC016",
                    requirement="Req 4.16",
                    message=(
                        f"Topic coverage row for {row.entry_text!r} names Topic_Doc "
                        f"{filename!r}, which does not exist as a file directly under "
                        f"'docs/learning/phase-1/'."
                    ),
                )
            )

    # (b) Every canonical coverage-list entry must have a filled-in row.
    for entry in PHASE_1_COVERAGE_ENTRIES:
        if any(row.entry_text.strip() == entry and row.topic_doc_filename.strip() for row in rows):
            continue
        findings.append(
            Finding(
                file=index_path,
                line=0,
                rule_id="LDC016",
                requirement="Req 4.14",
                message=(
                    f"Coverage-list entry {entry!r} has no Topic coverage row naming an "
                    f"authored Topic_Doc filename."
                ),
            )
        )

    return findings


def check_ldc017(root: Path) -> list[Finding]:
    """LDC017 — Every Topic_Doc is listed exactly once (Properties 7, 8; Req 2.5, 2.6, 2.10).

    For every ``*.md`` file directly under ``docs/learning/phase-1/`` other than
    the ``README.md`` index, exactly one Markdown link across the twelve thematic
    sections of the Phase_1_Index must target that filename, and that link's text
    must equal the Topic_Doc's H1 title. The scan is confined to the thematic
    sections, so links in the ``Recommended reading order`` section do not count
    toward the per-doc total.

    A finding is emitted when a Topic_Doc is listed zero times, more than once,
    or when its single entry's link text does not match its H1 title. With no
    Topic_Docs authored yet the file set is empty and the rule is silent.
    """
    index_path = root.joinpath(*_PHASE_1_INDEX_RELPATH)
    phase_1_dir = root.joinpath(*_PHASE_1_DIR_RELPATH)
    if not phase_1_dir.is_dir() or not index_path.is_file():
        return []

    topic_docs = sorted(
        p for p in phase_1_dir.glob("*.md") if p.is_file() and p.name != RESERVED_TOPIC_DOC_FILENAME
    )
    if not topic_docs:
        return []

    index_doc = parse_topic_doc(index_path)
    thematic_links: list[Link] = []
    for heading in PHASE_1_THEMATIC_SECTIONS:
        span = _index_section_span(index_doc, (heading,))
        if span is None:
            continue
        _, start, end = span
        thematic_links.extend(_internal_links_in_span(index_doc, start, end))

    findings: list[Finding] = []
    for topic_doc in topic_docs:
        filename = topic_doc.name
        matches = [link for link in thematic_links if link.target.partition("#")[0] == filename]
        count = len(matches)

        if count == 0:
            findings.append(
                Finding(
                    file=index_path,
                    line=0,
                    rule_id="LDC017",
                    requirement="Req 2.5, 2.10",
                    message=(
                        f"Topic_Doc {filename!r} is not listed in any of the twelve thematic "
                        f"sections of the Phase_1_Index; it must appear in exactly one."
                    ),
                )
            )
            continue

        if count > 1:
            findings.append(
                Finding(
                    file=index_path,
                    line=0,
                    rule_id="LDC017",
                    requirement="Req 2.5",
                    message=(
                        f"Topic_Doc {filename!r} is listed {count} times across the thematic "
                        f"sections of the Phase_1_Index; it must appear in exactly one."
                    ),
                )
            )
            continue

        link = matches[0]
        expected_title = parse_topic_doc(topic_doc).title
        if link.text != expected_title:
            findings.append(
                Finding(
                    file=index_path,
                    line=link.line,
                    rule_id="LDC017",
                    requirement="Req 2.6",
                    message=(
                        f"Phase_1_Index entry for {filename!r} has link text {link.text!r}, "
                        f"which does not equal the Topic_Doc's H1 title {expected_title!r}."
                    ),
                )
            )

    return findings


def check_ldc018(root: Path) -> list[Finding]:
    """LDC018 — Phase Sub-Libraries lists every present phase directory (Property 1, Req 1.4).

    Every ``docs/learning/phase-<N>/`` directory (1 <= N <= 7) present on disk
    must have a relative Markdown link under the Library_Index
    ``Phase Sub-Libraries`` section that resolves to it. A finding is emitted per
    present phase directory that is not linked (and a single file-level finding
    when the Library_Index or its section is missing while phase directories
    exist). When no phase directory is present the rule is silent.
    """
    index_path = root.joinpath(*_LIBRARY_INDEX_RELPATH)
    learning_dir = root.joinpath(*_LEARNING_DIR_RELPATH)

    present: list[tuple[int, Path]] = []
    if learning_dir.is_dir():
        for child in sorted(learning_dir.iterdir()):
            match = _PHASE_DIR_RE.match(child.name)
            if child.is_dir() and match is not None:
                present.append((int(match.group(1)), child))
    if not present:
        return []

    if not index_path.is_file():
        return [
            Finding(
                file=index_path,
                line=0,
                rule_id="LDC018",
                requirement="Req 1.4",
                message=(
                    "Library_Index 'docs/learning/README.md' is missing, so the present phase "
                    "sub-libraries cannot be listed."
                ),
            )
        ]

    index_doc = parse_topic_doc(index_path)
    span = _index_section_span(index_doc, _LIBRARY_PHASE_SUBLIBRARIES_HEADINGS)
    if span is None:
        return [
            Finding(
                file=index_path,
                line=0,
                rule_id="LDC018",
                requirement="Req 1.4",
                message=(
                    "Library_Index has no 'Phase Sub-Libraries' section to list the present "
                    "phase sub-libraries."
                ),
            )
        ]

    section, start, end = span
    links = _internal_links_in_span(index_doc, start, end)

    findings: list[Finding] = []
    for number, phase_dir in present:
        target = phase_dir.resolve()
        listed = any(
            _resolve_internal_target(index_path, root, link.target.partition("#")[0]) == target
            for link in links
        )
        if not listed:
            findings.append(
                Finding(
                    file=index_path,
                    line=section.line,
                    rule_id="LDC018",
                    requirement="Req 1.4",
                    message=(
                        f"Library_Index 'Phase Sub-Libraries' does not list a relative link "
                        f"resolving to the present 'docs/learning/phase-{number}/' directory."
                    ),
                )
            )

    return findings


def check_ldc019(root: Path) -> list[Finding]:
    """LDC019 — External Sources and Non-goals reference every source (Property 3, Req 1.7, 10.3).

    Both the Library_Index ``External Sources`` section and its ``Non-goals``
    section (which may be titled ``What this is not``) must reference, via a
    resolving relative link, every existing ``apps/*/README.md`` plus the
    ``.kiro/steering/`` and ``docs/adr/`` directories. A finding is emitted per
    (section, missing source) pair, and a single file-level finding per section
    that is absent entirely. Sources that do not exist on disk are not required
    (so the rule never demands a link that ``LDC020`` would then flag as broken).
    """
    index_path = root.joinpath(*_LIBRARY_INDEX_RELPATH)

    required: list[tuple[str, Path]] = []
    apps_dir = root / "apps"
    if apps_dir.is_dir():
        for app in sorted(apps_dir.iterdir()):
            readme = app / "README.md"
            if readme.is_file():
                required.append((f"apps/{app.name}/README.md", readme.resolve()))
    steering_dir = root / ".kiro" / "steering"
    if steering_dir.is_dir():
        required.append((".kiro/steering/", steering_dir.resolve()))
    adr_dir = root / "docs" / "adr"
    if adr_dir.is_dir():
        required.append(("docs/adr/", adr_dir.resolve()))

    if not required:
        return []

    if not index_path.is_file():
        return [
            Finding(
                file=index_path,
                line=0,
                rule_id="LDC019",
                requirement="Req 1.7, 10.3",
                message=(
                    "Library_Index 'docs/learning/README.md' is missing, so the required "
                    "external sources cannot be referenced."
                ),
            )
        ]

    index_doc = parse_topic_doc(index_path)
    findings: list[Finding] = []
    for headings in (_LIBRARY_EXTERNAL_SOURCES_HEADINGS, _LIBRARY_NON_GOALS_HEADINGS):
        label = headings[0]
        span = _index_section_span(index_doc, headings)
        if span is None:
            findings.append(
                Finding(
                    file=index_path,
                    line=0,
                    rule_id="LDC019",
                    requirement="Req 1.7, 10.3",
                    message=(
                        f"Library_Index has no {label!r} section to reference the required "
                        f"external sources."
                    ),
                )
            )
            continue

        section, start, end = span
        resolved_targets: set[Path | None] = {
            _resolve_internal_target(index_path, root, link.target.partition("#")[0])
            for link in _internal_links_in_span(index_doc, start, end)
        }
        for display, target in required:
            if target not in resolved_targets:
                findings.append(
                    Finding(
                        file=index_path,
                        line=section.line,
                        rule_id="LDC019",
                        requirement="Req 1.7, 10.3",
                        message=(
                            f"Library_Index {label!r} section does not reference {display!r} "
                            f"with a resolving relative link."
                        ),
                    )
                )

    return findings


def check_ldc020(root: Path) -> list[Finding]:
    """LDC020 — Library_Index Non-goals/External Sources links resolve (Req 10.7).

    Every relative Markdown link in the Library_Index ``Non-goals`` and
    ``External Sources`` sections must resolve to an existing path, interpreted
    relative to ``docs/learning/`` (the Library_Index's own directory). External
    ``https://`` links are not relative links and are out of scope. A finding is
    emitted per non-resolving relative link, citing its line. A missing section
    is silent here — section presence is :func:`check_ldc019`'s concern.
    """
    index_path = root.joinpath(*_LIBRARY_INDEX_RELPATH)
    if not index_path.is_file():
        return []

    index_doc = parse_topic_doc(index_path)
    findings: list[Finding] = []
    for headings in (_LIBRARY_EXTERNAL_SOURCES_HEADINGS, _LIBRARY_NON_GOALS_HEADINGS):
        span = _index_section_span(index_doc, headings)
        if span is None:
            continue
        label = headings[0]
        _, start, end = span
        for link in _internal_links_in_span(index_doc, start, end):
            path_part = link.target.partition("#")[0]
            if _resolve_internal_target(index_path, root, path_part) is None:
                findings.append(
                    Finding(
                        file=index_path,
                        line=link.line,
                        rule_id="LDC020",
                        requirement="Req 10.7",
                        message=(
                            f"Relative link {link.target!r} in the Library_Index {label!r} "
                            f"section does not resolve to an existing path."
                        ),
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# CLI orchestration and reporter (task 2.46)
# ---------------------------------------------------------------------------
#
# The orchestrator selects inputs, dispatches each to the rules that govern it,
# aggregates findings, sorts them for stable output, and the ``main`` entry
# point maps the aggregate to an exit code. Per-Topic_Doc rules run once per
# Topic_Doc (the ``*.md`` files directly under ``docs/learning/phase-1/`` other
# than ``README.md``); library-level rules run once per invocation against the
# repository root. Every individual rule call is wrapped so a single buggy rule
# surfaces as an ``LDC999`` finding instead of crashing the whole run.

# Per-Topic_Doc rules with the one-argument ``check(doc)`` signature.
_PER_DOC_RULES: tuple[Callable[[TopicDoc], list[Finding]], ...] = (
    check_ldc001,
    check_ldc002,
    check_ldc003,
    check_ldc004,
    check_ldc008,
    check_ldc010,
    check_ldc011,
    check_ldc013,
    check_ldc014,
    check_ldc101,
    check_ldc102,
    check_ldc104,
    check_ldc105,
)

# Per-Topic_Doc rules with the two-argument ``check(doc, root)`` signature
# (they resolve repository paths or read the Conventions_Doc).
_PER_DOC_ROOT_RULES: tuple[Callable[[TopicDoc, Path], list[Finding]], ...] = (
    check_ldc005,
    check_ldc006,
    check_ldc007,
    check_ldc009,
    check_ldc012,
    check_ldc015,
    check_ldc103,
    check_ldc106,
)

# Library-level rules with the ``check(root)`` signature.
_LIBRARY_RULES: tuple[Callable[[Path], list[Finding]], ...] = (
    check_ldc016,
    check_ldc017,
    check_ldc018,
    check_ldc019,
    check_ldc020,
)


class WalkError(Exception):
    """Raised when the library cannot be walked (missing root, unreadable file).

    Maps to CLI exit code ``2`` — distinct from "rules found violations" (exit
    ``1``). Carries a human-readable message printed to stderr without a
    traceback.
    """


def find_repo_root(start: Path) -> Path:
    """Return the repository root by walking up from ``start`` to the marker.

    The repository root is the nearest ancestor of ``start`` (inclusive) that
    contains ``pnpm-workspace.yaml`` — the monorepo marker, matching how the
    test suite and ``check_env_drift.py`` locate the root. Raises
    :class:`WalkError` when no ancestor carries the marker.
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / "pnpm-workspace.yaml").is_file():
            return candidate
    msg = (
        f"could not locate the repository root: no 'pnpm-workspace.yaml' found in "
        f"{start} or any parent directory"
    )
    raise WalkError(msg)


def _is_topic_doc(path: Path, root: Path) -> bool:
    """Return True when ``path`` is a Phase 1 Topic_Doc (not an index file).

    Topic_Docs are the Markdown files directly under ``docs/learning/phase-1/``
    other than the reserved ``README.md`` index.
    """
    phase_1_dir = root.joinpath(*_PHASE_1_DIR_RELPATH)
    return path.parent == phase_1_dir and path.name != RESERVED_TOPIC_DOC_FILENAME


def _finding_for_rule_error(rule_name: str, file: Path, exc: Exception) -> Finding:
    """Build the ``LDC999`` finding that contains a crashed rule.

    A rule must never crash the whole run or mask other rules' findings, so an
    unexpected exception becomes a single file-level ``LDC999`` finding naming
    the failing rule and a short cause. The validator never emits a traceback as
    user-visible output.
    """
    return Finding(
        file=file,
        line=0,
        rule_id="LDC999",
        requirement="unknown",
        message=f"internal error in {rule_name}: {type(exc).__name__}: {exc}",
    )


def _safe_doc_rule(rule: Callable[[TopicDoc], list[Finding]], doc: TopicDoc) -> list[Finding]:
    """Invoke a one-argument per-Topic_Doc rule, containing any exception as ``LDC999``."""
    try:
        return rule(doc)
    except Exception as exc:  # one buggy rule must not abort the whole run
        return [_finding_for_rule_error(rule.__name__, doc.path, exc)]


def _safe_doc_root_rule(
    rule: Callable[[TopicDoc, Path], list[Finding]], doc: TopicDoc, root: Path
) -> list[Finding]:
    """Invoke a ``(doc, root)`` per-Topic_Doc rule, containing any exception as ``LDC999``."""
    try:
        return rule(doc, root)
    except Exception as exc:  # one buggy rule must not abort the whole run
        return [_finding_for_rule_error(rule.__name__, doc.path, exc)]


def _safe_library_rule(
    rule: Callable[[Path], list[Finding]], root: Path, file: Path
) -> list[Finding]:
    """Invoke a library-level ``(root)`` rule, containing any exception as ``LDC999``."""
    try:
        return rule(root)
    except Exception as exc:  # one buggy rule must not abort the whole run
        return [_finding_for_rule_error(rule.__name__, file, exc)]


def run(root: Path) -> list[Finding]:
    """Validate the Learning_Docs_Library under ``root`` and return all findings.

    Walks every Markdown file under ``docs/learning/`` (via :func:`walk_library`),
    dispatches each Topic_Doc to every per-Topic_Doc rule, then runs each
    library-level rule once against ``root``. Findings are returned sorted by
    ``(file, line, rule_id)`` for stable, reproducible output. A failure to read
    or parse a file is raised as :class:`WalkError` (CLI exit ``2``); a buggy
    individual rule is contained as an ``LDC999`` finding rather than aborting.
    """
    findings: list[Finding] = []

    try:
        markdown_files = list(walk_library(root))
    except OSError as exc:
        raise WalkError(f"could not walk 'docs/learning/' under {root}: {exc}") from exc

    for path in markdown_files:
        if not _is_topic_doc(path, root):
            continue
        try:
            doc = parse_topic_doc(path)
        except (OSError, UnicodeDecodeError) as exc:
            raise WalkError(f"could not read Topic_Doc {path}: {exc}") from exc
        for rule in _PER_DOC_RULES:
            findings.extend(_safe_doc_rule(rule, doc))
        for root_rule in _PER_DOC_ROOT_RULES:
            findings.extend(_safe_doc_root_rule(root_rule, doc, root))

    index_path = root.joinpath(*_PHASE_1_INDEX_RELPATH)
    for lib_rule in _LIBRARY_RULES:
        findings.extend(_safe_library_rule(lib_rule, root, index_path))

    findings.sort(key=lambda f: (str(f.file), f.line, f.rule_id))
    return findings


def _relative_to_root(path: Path, root: Path) -> str:
    """Return ``path`` as a POSIX string relative to ``root`` when possible."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def format_findings_text(findings: list[Finding], root: Path) -> str:
    """Render findings in the human-readable text format (Design "Reporter behavior").

    Each finding is two lines: ``path:line  rule_id  requirement`` followed by an
    indented message. Returns the empty string when there are no findings.
    """
    blocks: list[str] = []
    for finding in findings:
        location = f"{_relative_to_root(finding.file, root)}:{finding.line}"
        blocks.append(f"{location}  {finding.rule_id}  {finding.requirement}\n  {finding.message}")
    return "\n\n".join(blocks)


def format_findings_json(findings: list[Finding], root: Path) -> str:
    """Render findings as one JSON object per line (newline-delimited JSON)."""
    return "\n".join(
        json.dumps(
            {
                "file": _relative_to_root(finding.file, root),
                "line": finding.line,
                "rule_id": finding.rule_id,
                "requirement": finding.requirement,
                "message": finding.message,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for finding in findings
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns ``0`` (clean), ``1`` (findings), or ``2`` (walk failure).

    Validates the Learning_Docs_Library and reports findings. ``--root`` defaults
    to the repository root discovered by walking up from the current working
    directory to the ``pnpm-workspace.yaml`` marker; ``--format`` selects the
    text (default) or newline-delimited JSON reporter. The validator is
    read-only and never mutates the repository.
    """
    parser = argparse.ArgumentParser(
        prog="learning_docs_check.py",
        description=(
            "Validate the MatchLayer Learning_Docs_Library (docs/learning/) against "
            "the phase-1-learning-docs structural rules. Read-only."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Repository root (the directory containing pnpm-workspace.yaml). "
            "Defaults to discovery by walking up from the current directory."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for findings (default: text).",
    )
    args = parser.parse_args(argv)

    try:
        root = find_repo_root(args.root) if args.root is not None else find_repo_root(Path.cwd())
        findings = run(root)
    except WalkError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if not findings:
        print("OK: docs/learning/ passes all learning-docs compliance rules.")
        return 0

    report = (
        format_findings_json(findings, root)
        if args.format == "json"
        else format_findings_text(findings, root)
    )
    print(report)
    plural = "s" if len(findings) != 1 else ""
    sys.stderr.write(f"\n{len(findings)} finding{plural} reported.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
