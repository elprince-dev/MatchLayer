"""Deterministic PII redaction applied before any text leaves the system (the ``PII_Redactor``).

Transforms Resume text, Job_Description text, or user-submitted bullet text
by replacing detected email addresses, phone numbers, and obvious full names
with indexed typed placeholders (``[EMAIL_n]``, ``[PHONE_n]``, ``[NAME_n]``)
before the ``LLM_Client`` transmits anything to the LLM_Provider.

Algorithm (design section "PII_Redactor", decision D8 — region-aware):

1. **Segment** (``kind="resume"`` only) the input into a contact/header
   region (lines before the first recognized section heading, capped at the
   first :data:`_HEADER_REGION_MAX_LINES` lines), employment-history sections
   (a section whose normalized heading is in
   :data:`EMPLOYMENT_SECTION_HEADINGS`, extending to the next recognized
   section heading), and other text. Any span **inside** an
   employment-history section is exempt from redaction (the documented
   ``Redaction_Exception``); see ``docs/redaction-policy.md`` for the
   committed boundary rule and its rationale (Req 3.3).
2. **Detect** emails and phone numbers by the committed regexes over the
   whole text; obvious full names by the committed heuristic over the
   contact/header region (maximal runs of 2-4 capitalized tokens, excluding
   lexicon words). A detected name is then matched at *every* occurrence in
   the whole text (Req 3.2).
3. **Replace** non-exempt occurrences with indexed typed placeholders: one
   index per distinct detected value per type, indices starting at 1,
   assigned in first-replaced-occurrence order; every occurrence of the same
   value receives the same placeholder (Req 3.1, 3.5).
4. **Guardrails**: the whole redaction runs inside a
   :data:`_TIME_BUDGET_SECONDS` wall-clock bound; any exception or timeout
   raises :class:`RedactionError` carrying *no fragment of the input* so the
   caller takes the Fallback_Response path (Req 3.6).

PII discipline (Req 3.7): this module never logs — neither the input text
nor the redacted output may appear in any log line, error message, or
telemetry signal. :class:`RedactionError` messages are fixed strings, and
the original exception is deliberately *not* chained (``from None``) because
third-party exception messages can embed input fragments.

Determinism (Req 3.4): :func:`redact` is a pure function of its arguments —
identical input yields byte-identical output under the same
:data:`REDACTOR_VERSION`, which the orchestrator records in every
LLM_Invocation_Log entry for Phase 5 replay.

Design reference: §"PII_Redactor (services/llm/redaction.py)".
Requirements covered: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7.
Policy documentation: ``docs/redaction-policy.md``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel

REDACTOR_VERSION: Final[str] = "1.0.0"
"""Recorded in every LLM_Invocation_Log entry so redacted inputs are reproducible (Req 3.4)."""

_TIME_BUDGET_SECONDS: Final[float] = 5.0
"""Wall-clock bound for a single :func:`redact` call (Req 3.6)."""

_HEADER_REGION_MAX_LINES: Final[int] = 10
"""The contact/header region never extends past this many leading lines."""

# ---------------------------------------------------------------------------
# Committed lexicons — the reviewable segmentation rule (Req 3.2, 3.3).
# The full boundary rule is documented in docs/redaction-policy.md; these
# constants are its committed, testable form.
# ---------------------------------------------------------------------------

EMPLOYMENT_SECTION_HEADINGS: Final[frozenset[str]] = frozenset(
    {
        "experience",
        "work experience",
        "employment",
        "employment history",
        "work history",
        "professional experience",
    }
)
"""Normalized headings that open an employment-history (Redaction_Exception) section."""

SECTION_HEADINGS: Final[frozenset[str]] = EMPLOYMENT_SECTION_HEADINGS | frozenset(
    {
        "summary",
        "professional summary",
        "objective",
        "career objective",
        "profile",
        "about",
        "about me",
        "contact",
        "contact information",
        "education",
        "skills",
        "technical skills",
        "core competencies",
        "projects",
        "certifications",
        "certificates",
        "licenses",
        "awards",
        "honors",
        "publications",
        "languages",
        "volunteer",
        "volunteering",
        "volunteer experience",
        "interests",
        "hobbies",
        "references",
    }
)
"""All recognized section headings. The first one ends the contact/header region;
each one terminates the section that precedes it."""

_NAME_EXCLUSION_WORDS: Final[frozenset[str]] = frozenset(
    # Every individual word of every recognized section heading...
    {word for heading in SECTION_HEADINGS for word in heading.split()}
    # ...plus common resume-header words (job titles, contact labels) that are
    # capitalized in headers but are never part of a person's name.
    | {
        "resume",
        "curriculum",
        "vitae",
        "senior",
        "junior",
        "lead",
        "staff",
        "principal",
        "chief",
        "head",
        "software",
        "engineer",
        "engineering",
        "developer",
        "development",
        "manager",
        "director",
        "analyst",
        "scientist",
        "architect",
        "consultant",
        "designer",
        "specialist",
        "administrator",
        "intern",
        "associate",
        "freelance",
        "product",
        "data",
        "web",
        "frontend",
        "backend",
        "fullstack",
        "full-stack",
        "devops",
        "phone",
        "mobile",
        "tel",
        "email",
        "e-mail",
        "address",
        "linkedin",
        "github",
        "portfolio",
        "website",
        "twitter",
        "city",
        "state",
        "street",
        "avenue",
        "road",
        "apt",
        "suite",
    }
)
"""Words that break a capitalized-token run in the name heuristic (Req 3.2)."""

# ---------------------------------------------------------------------------
# Committed detection patterns (Req 3.2).
# ---------------------------------------------------------------------------

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
"""Email addresses, applied over the entire text."""

_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?<![\w.-])                              # not inside a larger number/word/date
    (?:\+\d{1,3}[ .-]?)?                     # optional international prefix
    (?:\(\d{1,4}\)[ .-]?|\d{1,4}[ .-]?)?     # optional area code
    \d{3}[ .-]?\d{4}                         # subscriber number (>= 7 digits total)
    (?![\w-])
    """,
    re.VERBOSE,
)
"""Phone numbers (common US/international formats), applied over the entire text."""

_WORD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z'\u2019.-]*")
"""A word token for the name heuristic (letters, apostrophes, hyphens, periods)."""

_INTER_TOKEN_GAP_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+")
"""Tokens belong to the same run only when separated purely by spaces/tabs."""

_HEADING_DECORATION_CHARS: Final[str] = "#*=\u2013\u2014- \t"
"""Decoration characters stripped from both ends of a line before heading matching."""

_DEADLINE_CHECK_EVERY: Final[int] = 64
"""How often (in loop iterations) the wall-clock deadline is re-checked."""


class RedactionError(Exception):
    """Redaction failed or exceeded its wall-clock bound (Req 3.6).

    Instances carry a fixed, PII-free message — never the input text or any
    fragment of it — so the failure signal is safe to log upstream.
    """


class RedactionResult(BaseModel):
    """The redacted text plus the redactor version that produced it (Req 3.4)."""

    text: str
    redactor_version: str


@dataclass(frozen=True)
class _Span:
    """A detected PII occurrence: half-open character range, exact value, type label."""

    start: int
    end: int
    value: str
    label: str  # "EMAIL" | "PHONE" | "NAME"


def redact(text: str, *, kind: Literal["resume", "job_description", "bullet"]) -> RedactionResult:
    """Redact PII from ``text`` per the committed policy (``docs/redaction-policy.md``).

    Deterministic and pure: identical arguments produce identical output
    under the same :data:`REDACTOR_VERSION`. Raises :class:`RedactionError`
    (with no input fragment) on any internal failure or when processing
    exceeds the 5-second wall-clock bound.
    """
    deadline = time.monotonic() + _TIME_BUDGET_SECONDS
    try:
        redacted = _redact_inner(text, kind, deadline)
    except RedactionError:
        raise
    except Exception:
        # Deliberately not chained (`from None`): third-party exception
        # messages can embed fragments of the input text (Req 3.6, 3.7).
        raise RedactionError("PII redaction failed") from None
    return RedactionResult(text=redacted, redactor_version=REDACTOR_VERSION)


def _redact_inner(
    text: str,
    kind: Literal["resume", "job_description", "bullet"],
    deadline: float,
) -> str:
    lines = _split_lines_with_offsets(text)
    _check_deadline(deadline)

    if kind == "resume":
        exempt_regions = _employment_section_regions(text, lines)
        header_lines = _header_region_lines(lines)
        names = _detect_names(header_lines, deadline)
    else:
        # The header region, name heuristic, and employment-history exemption
        # are resume-shaped constructs; job descriptions and bullets get
        # whole-text email/phone redaction with no exemptions. Committed
        # boundary rule: docs/redaction-policy.md.
        exempt_regions = []
        names = []

    candidates: list[_Span] = []
    for match in _EMAIL_RE.finditer(text):
        candidates.append(_Span(match.start(), match.end(), match.group(), "EMAIL"))
    _check_deadline(deadline)
    for match in _PHONE_RE.finditer(text):
        candidates.append(_Span(match.start(), match.end(), match.group(), "PHONE"))
    _check_deadline(deadline)
    for name in names:
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
        for match in pattern.finditer(text):
            candidates.append(_Span(match.start(), match.end(), name, "NAME"))
        _check_deadline(deadline)

    kept = _resolve_overlaps(candidates, deadline)
    replaceable = [span for span in kept if not _is_inside_any(span, exempt_regions)]
    replaceable.sort(key=lambda span: span.start)

    return _apply_placeholders(text, replaceable)


def _split_lines_with_offsets(text: str) -> list[tuple[int, str]]:
    """Split on ``\\n``, returning each line with its absolute start offset."""
    offsets: list[tuple[int, str]] = []
    position = 0
    for line in text.split("\n"):
        offsets.append((position, line))
        position += len(line) + 1
    return offsets


def _normalize_heading(line: str) -> str:
    """Normalize a line for lexicon matching (the committed heading rule).

    Strips surrounding whitespace and decoration characters, removes one
    trailing colon, lowercases. A line is a recognized section heading iff
    the result is exactly an entry of :data:`SECTION_HEADINGS`.
    """
    stripped = line.strip().strip(_HEADING_DECORATION_CHARS)
    stripped = stripped.rstrip(":").strip()
    return stripped.lower()


def _heading_line_indexes(lines: list[tuple[int, str]]) -> list[int]:
    return [
        index
        for index, (_, line) in enumerate(lines)
        if _normalize_heading(line) in SECTION_HEADINGS
    ]


def _employment_section_regions(text: str, lines: list[tuple[int, str]]) -> list[tuple[int, int]]:
    """Half-open character ranges covering employment-history sections (Req 3.3).

    A section starts at its heading line and extends to the start of the next
    recognized section heading, or to the end of the text.
    """
    heading_indexes = _heading_line_indexes(lines)
    regions: list[tuple[int, int]] = []
    for position, line_index in enumerate(heading_indexes):
        _, line = lines[line_index]
        if _normalize_heading(line) not in EMPLOYMENT_SECTION_HEADINGS:
            continue
        start = lines[line_index][0]
        next_position = position + 1
        if next_position < len(heading_indexes):
            end = lines[heading_indexes[next_position]][0]
        else:
            end = len(text)
        regions.append((start, end))
    return regions


def _header_region_lines(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Lines before the first recognized section heading, capped at the first 10."""
    heading_indexes = _heading_line_indexes(lines)
    first_heading = heading_indexes[0] if heading_indexes else len(lines)
    return lines[: min(first_heading, _HEADER_REGION_MAX_LINES)]


def _detect_names(header_lines: list[tuple[int, str]], deadline: float) -> list[str]:
    """The committed name heuristic (Req 3.2), applied to the header region only.

    A detected name is a maximal run of 2-4 consecutive capitalized word
    tokens, separated only by spaces/tabs, none of which is (case-
    insensitively) in :data:`_NAME_EXCLUSION_WORDS`. Runs of 1 token or more
    than 4 tokens are not names. Distinct detected strings are returned in
    detection order.
    """
    seen: set[str] = set()
    names: list[str] = []
    for _, line in header_lines:
        _check_deadline(deadline)
        if not line.strip():
            continue
        tokens = list(_WORD_TOKEN_RE.finditer(line))
        run: list[re.Match[str]] = []
        runs: list[list[re.Match[str]]] = []
        for token in tokens:
            eligible = (
                token.group()[0].isupper() and token.group().lower() not in _NAME_EXCLUSION_WORDS
            )
            contiguous = bool(run) and _INTER_TOKEN_GAP_RE.fullmatch(
                line, run[-1].end(), token.start()
            )
            if eligible and (not run or contiguous):
                run.append(token)
                continue
            if run:
                runs.append(run)
            run = [token] if eligible else []
        if run:
            runs.append(run)
        for candidate in runs:
            if not 2 <= len(candidate) <= 4:
                continue
            name = line[candidate[0].start() : candidate[-1].end()]
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _resolve_overlaps(candidates: list[_Span], deadline: float) -> list[_Span]:
    """Drop lower-priority spans that overlap an already-accepted span.

    Candidates arrive grouped by type in priority order (emails, phones,
    names) and in text order within each type, so acceptance order is the
    deterministic precedence rule: EMAIL > PHONE > NAME, earlier occurrence
    first within a type.
    """
    kept: list[_Span] = []
    for iteration, span in enumerate(candidates):
        if iteration % _DEADLINE_CHECK_EVERY == 0:
            _check_deadline(deadline)
        if any(span.start < other.end and other.start < span.end for other in kept):
            continue
        kept.append(span)
    return kept


def _is_inside_any(span: _Span, regions: list[tuple[int, int]]) -> bool:
    """True iff the span lies fully inside one of the (exempt) regions."""
    return any(start <= span.start and span.end <= end for start, end in regions)


def _apply_placeholders(text: str, spans: list[_Span]) -> str:
    """Replace spans (sorted by start) with indexed typed placeholders (Req 3.1, 3.5)."""
    indices: dict[str, dict[str, int]] = {"EMAIL": {}, "PHONE": {}, "NAME": {}}
    pieces: list[str] = []
    cursor = 0
    for span in spans:
        per_type = indices[span.label]
        if span.value not in per_type:
            per_type[span.value] = len(per_type) + 1
        pieces.append(text[cursor : span.start])
        pieces.append(f"[{span.label}_{per_type[span.value]}]")
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise RedactionError("PII redaction exceeded the 5-second wall-clock bound")
