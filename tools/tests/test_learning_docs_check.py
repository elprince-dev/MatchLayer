"""Tests for the Phase 1 learning-docs compliance validator.

This module currently holds the two non-property test layers from the
``phase-1-learning-docs`` design's *Testing Strategy* (task 2.3):

* ``TestSmoke`` — Layer 3 smoke tests (Design §"Layer 3: Smoke tests on
  filesystem state"). They assert the Learning_Docs_Library entry points exist
  on disk and are non-empty (Req 1.1, 1.2, 1.3, 2.1, 2.2). These run first;
  every later test assumes the library is present.
* ``TestParseTopicDocRoundTrip`` — round-trip tests for
  :func:`learning_docs_check.parse_topic_doc` against a hand-written fixture
  Topic_Doc that exercises every shape the parser captures: the H1 title, the
  seven required H2 sections in order, a verbatim sourced fenced block, a
  ``simplified for illustration`` block followed by a link, and a hyperlinked
  prerequisites list in the ``Introduction``.

The smoke/example layer lives in ``TestSmoke`` and ``TestParseTopicDocRoundTrip``.
The Hypothesis property-based tests (Design §"Layer 1") for the filename and H1
rules live in ``TestProperty10FilenameConformance`` and
``TestProperty33H1Conformance`` further down (tasks 2.5 and 2.6).
"""

from __future__ import annotations

import re
import string
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from learning_docs_check import (
    ALLOWED_LANGUAGES,
    BANNED_PHRASES,
    DOC_TEMPLATE_REQUIRED,
    HOW_IT_WORKS_BANNED_STRINGS,
    MAX_FILENAME_LENGTH,
    MAX_H1_TITLE_LENGTH,
    MIN_H1_TITLE_LENGTH,
    PHASE_1_COVERAGE_ENTRIES,
    PHASE_1_THEMATIC_SECTIONS,
    RESERVED_TOPIC_DOC_FILENAME,
    CoverageRow,
    FencedBlock,
    Section,
    TopicDoc,
    check_ldc001,
    check_ldc002,
    check_ldc003,
    check_ldc004,
    check_ldc005,
    check_ldc006,
    check_ldc007,
    check_ldc008,
    check_ldc009,
    check_ldc010,
    check_ldc012,
    check_ldc013,
    check_ldc014,
    check_ldc015,
    check_ldc016,
    check_ldc017,
    check_ldc018,
    check_ldc019,
    check_ldc101,
    check_ldc102,
    check_ldc103,
    check_ldc104,
    check_ldc105,
    check_ldc106,
    parse_phase_1_index,
    parse_topic_doc,
)

# ---------------------------------------------------------------------------
# Repo-root discovery (smoke layer)
# ---------------------------------------------------------------------------

# The repository root is the nearest ancestor of this test file that contains
# ``pnpm-workspace.yaml`` (the monorepo marker, per .kiro/steering/structure.md).
# The Learning_Docs_Library lives at ``<root>/docs/learning/``.
_WORKSPACE_MARKER = "pnpm-workspace.yaml"


def _repo_root() -> Path:
    """Return the repo root by walking up from this file to the workspace marker."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / _WORKSPACE_MARKER).is_file():
            return parent
    msg = f"could not locate {_WORKSPACE_MARKER!r} in any ancestor of {here}"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Fixture Topic_Doc for the parser round-trip tests
# ---------------------------------------------------------------------------

FIXTURE_TITLE = "Example Topic For Parser Round Trip"
FIXTURE_SOURCE_PATH = "apps/api/src/matchlayer_api/main.py"

# A complete, well-formed Topic_Doc skeleton: H1 + the seven required H2s in
# order, a prerequisites list of internal links in the Introduction, a
# ``simplified for illustration`` block (in How it works) followed by a link, a
# verbatim sourced block (in MatchLayer Phase 1 usage) carrying a ``Source:``
# citation, and two https External reading links. The triple-backtick fences
# live happily inside the single-quoted triple-quoted string.
FIXTURE_TOPIC_DOC = """# Example Topic For Parser Round Trip

## Introduction

This Topic_Doc exists only to exercise the Markdown parser end to end.

Prerequisites:

- [Monorepo layout](monorepo-layout.md)
- [pnpm and workspaces](pnpm-and-workspaces.md)

## Problem it solves

It names a concrete problem and describes a prior approach.

## Mental model

1. First, picture the parser reading a file.
2. Then, picture it splitting that file into sections.
3. Finally, picture each section becoming a record.

## How it works

The snippet below is simplified for illustration:

```python
def add(first, second):
    return first + second
```

See [the real implementation](monorepo-layout.md) for the full version.

## MatchLayer Phase 1 usage

The application factory is defined in the API package.

Source: `apps/api/src/matchlayer_api/main.py`

```python
def create_app() -> FastAPI:
    app = FastAPI()
    return app
```

## Common pitfalls

- Mistake: skipping the factory. Symptom: import-time side effects. Recovery: wrap it in a function.
- Mistake: a second mistake. Symptom: a second symptom. Recovery: a second recovery.
- Mistake: a third mistake. Symptom: a third symptom. Recovery: a third recovery.

## External reading

- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [Python documentation](https://docs.python.org/3/)
"""


# ---------------------------------------------------------------------------
# Layer 3 — smoke tests on filesystem state (Req 1.1, 1.2, 1.3, 2.1, 2.2)
# ---------------------------------------------------------------------------


class TestSmoke:
    """Existence-shaped checks for the Learning_Docs_Library entry points.

    Mirrors Design §"Layer 3: Smoke tests on filesystem state": the library
    root and Phase 1 sub-library are directories, and each of the three index
    Markdown files exists and is non-empty.
    """

    def test_learning_root_is_a_directory(self) -> None:
        learning_root = _repo_root() / "docs" / "learning"
        assert learning_root.is_dir(), f"{learning_root} should be a directory"

    def test_library_index_exists_and_is_non_empty(self) -> None:
        library_index = _repo_root() / "docs" / "learning" / "README.md"
        assert library_index.is_file(), f"{library_index} should exist"
        assert library_index.stat().st_size > 0, f"{library_index} should be non-empty"

    def test_conventions_doc_exists_and_is_non_empty(self) -> None:
        conventions = _repo_root() / "docs" / "learning" / "CONVENTIONS.md"
        assert conventions.is_file(), f"{conventions} should exist"
        assert conventions.stat().st_size > 0, f"{conventions} should be non-empty"

    def test_phase_1_sub_library_is_a_directory(self) -> None:
        phase_1 = _repo_root() / "docs" / "learning" / "phase-1"
        assert phase_1.is_dir(), f"{phase_1} should be a directory"

    def test_phase_1_index_exists_and_is_non_empty(self) -> None:
        phase_1_index = _repo_root() / "docs" / "learning" / "phase-1" / "README.md"
        assert phase_1_index.is_file(), f"{phase_1_index} should exist"
        assert phase_1_index.stat().st_size > 0, f"{phase_1_index} should be non-empty"


# ---------------------------------------------------------------------------
# parse_topic_doc round-trip tests
# ---------------------------------------------------------------------------


class TestParseTopicDocRoundTrip:
    """Round-trip ``parse_topic_doc`` against the fixture Topic_Doc above.

    Each test asserts one facet of the parsed :class:`TopicDoc`: the title, the
    section sequence, the fenced blocks (sourced vs simplified), the
    internal/external link split, and the parsed prerequisites.
    """

    def test_captures_h1_title(self, tmp_path: Path) -> None:
        doc = parse_topic_doc(_write_fixture(tmp_path))
        assert doc.title == FIXTURE_TITLE
        assert doc.filename == "example-topic.md"

    def test_captures_seven_required_sections_in_order(self, tmp_path: Path) -> None:
        doc = parse_topic_doc(_write_fixture(tmp_path))
        headings = [section.heading for section in doc.sections]
        assert headings == list(DOC_TEMPLATE_REQUIRED)

    def test_captures_sourced_and_simplified_fenced_blocks(self, tmp_path: Path) -> None:
        doc = parse_topic_doc(_write_fixture(tmp_path))

        assert len(doc.fenced_blocks) == 2

        simplified = [block for block in doc.fenced_blocks if block.is_simplified]
        sourced = [block for block in doc.fenced_blocks if block.source_path is not None]
        assert len(simplified) == 1
        assert len(sourced) == 1

        # The simplified-for-illustration block is labelled but un-sourced.
        assert simplified[0].language == "python"
        assert simplified[0].source_path is None

        # The sourced block carries its Source: citation and is not simplified.
        assert sourced[0].language == "python"
        assert sourced[0].source_path == Path(FIXTURE_SOURCE_PATH)
        assert sourced[0].is_simplified is False

    def test_classifies_internal_and_external_links(self, tmp_path: Path) -> None:
        doc = parse_topic_doc(_write_fixture(tmp_path))

        assert all(link.is_internal for link in doc.internal_links)
        assert all(not link.is_internal for link in doc.external_links)

        external_targets = {link.target for link in doc.external_links}
        assert external_targets == {
            "https://fastapi.tiangolo.com/",
            "https://docs.python.org/3/",
        }

        internal_targets = {link.target for link in doc.internal_links}
        assert "monorepo-layout.md" in internal_targets
        assert "pnpm-and-workspaces.md" in internal_targets

    def test_captures_introduction_prerequisites(self, tmp_path: Path) -> None:
        doc = parse_topic_doc(_write_fixture(tmp_path))

        prerequisite_targets = [link.target for link in doc.prerequisites]
        assert prerequisite_targets == ["monorepo-layout.md", "pnpm-and-workspaces.md"]
        assert all(link.is_internal for link in doc.prerequisites)


def _write_fixture(tmp_path: Path) -> Path:
    """Write the fixture Topic_Doc into ``tmp_path`` and return its path."""
    path = tmp_path / "example-topic.md"
    path.write_text(FIXTURE_TOPIC_DOC, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Layer 1 — property-based tests (Design §"Layer 1: Property-based tests")
# ---------------------------------------------------------------------------
#
# These tests construct ``TopicDoc`` values directly with synthetic field
# values rather than touching the filesystem: ``check_ldc001`` reads only
# ``doc.filename`` and ``check_ldc002`` reads only ``doc.raw_lines``, so a
# hand-built ``TopicDoc`` exercises each rule end to end with no I/O. Each test
# runs at least 100 Hypothesis examples and carries a ``Feature:`` docstring so
# a failure traces back to the design property it validates.

# Alphabets for the synthetic generators.
_KEBAB_ALPHABET = string.ascii_lowercase + string.digits
_TITLE_ALPHABET = string.ascii_letters + string.digits


def _topic_doc_with_filename(name: str) -> TopicDoc:
    """Build a minimal ``TopicDoc`` carrying ``name`` as its filename.

    Only ``filename`` (and ``path``, for finding provenance) matter to
    ``check_ldc001``; every other field is empty. ``path`` is a pure
    ``Path`` value and is never read from disk, so a non-conforming ``name``
    (uppercase, underscores, over-length) is harmless here.
    """
    return TopicDoc(
        path=Path("docs/learning/phase-1") / name,
        filename=name,
        title="",
        sections=[],
        fenced_blocks=[],
        internal_links=[],
        external_links=[],
        prerequisites=[],
        raw_lines=[],
    )


def _topic_doc_with_lines(lines: list[str]) -> TopicDoc:
    """Build a minimal ``TopicDoc`` whose ``raw_lines`` are ``lines``.

    Only ``raw_lines`` matters to ``check_ldc002`` (it inspects lines 1 and 2).
    """
    return TopicDoc(
        path=Path("docs/learning/phase-1/example-topic.md"),
        filename="example-topic.md",
        title="",
        sections=[],
        fenced_blocks=[],
        internal_links=[],
        external_links=[],
        prerequisites=[],
        raw_lines=list(lines),
    )


# ---- Property 10 generators: Topic_Doc filenames -------------------------


@st.composite
def _conforming_filenames(draw: st.DrawFn) -> str:
    """A kebab-case ``<stem>.md`` name: matches the regex, well under 80 chars."""
    segments = draw(
        st.lists(
            st.text(alphabet=_KEBAB_ALPHABET, min_size=1, max_size=12),
            min_size=1,
            max_size=5,
        )
    )
    # At most 5*12 + 4 hyphens + 3 ('.md') == 67 chars, always <= 80.
    return "-".join(segments) + ".md"


@st.composite
def _uppercase_filenames(draw: st.DrawFn) -> str:
    """A name with at least one uppercase letter in the stem (fails the regex)."""
    base = draw(st.text(alphabet=_KEBAB_ALPHABET, min_size=0, max_size=10))
    upper = draw(st.text(alphabet=string.ascii_uppercase, min_size=1, max_size=4))
    pos = draw(st.integers(min_value=0, max_value=len(base)))
    return f"{base[:pos]}{upper}{base[pos:]}.md"


@st.composite
def _underscore_filenames(draw: st.DrawFn) -> str:
    """A ``left_right.md`` name (underscore is not allowed by the regex)."""
    left = draw(st.text(alphabet=_KEBAB_ALPHABET, min_size=1, max_size=10))
    right = draw(st.text(alphabet=_KEBAB_ALPHABET, min_size=1, max_size=10))
    return f"{left}_{right}.md"


@st.composite
def _leading_hyphen_filenames(draw: st.DrawFn) -> str:
    """A ``-stem.md`` name (a leading hyphen fails the regex)."""
    stem = draw(st.text(alphabet=_KEBAB_ALPHABET, min_size=1, max_size=10))
    return f"-{stem}.md"


@st.composite
def _trailing_hyphen_filenames(draw: st.DrawFn) -> str:
    """A ``stem-.md`` name (a trailing hyphen before ``.md`` fails the regex)."""
    stem = draw(st.text(alphabet=_KEBAB_ALPHABET, min_size=1, max_size=10))
    return f"{stem}-.md"


@st.composite
def _too_long_filenames(draw: st.DrawFn) -> str:
    """A kebab-valid single-segment name whose length exceeds 80 chars."""
    length = draw(st.integers(min_value=MAX_FILENAME_LENGTH, max_value=MAX_FILENAME_LENGTH + 40))
    stem = draw(st.text(alphabet=_KEBAB_ALPHABET, min_size=length, max_size=length))
    return f"{stem}.md"  # len == length + 3 > 80


_non_conforming_filenames = st.one_of(
    _uppercase_filenames(),
    _underscore_filenames(),
    _leading_hyphen_filenames(),
    _trailing_hyphen_filenames(),
    _too_long_filenames(),
    st.just(RESERVED_TOPIC_DOC_FILENAME),  # 'README.md' is reserved, never a Topic_Doc
)


class TestProperty10FilenameConformance:
    r"""Property 10: ``check_ldc001`` accepts conforming filenames, flags the rest."""

    @settings(max_examples=100)
    @given(name=_conforming_filenames())
    def test_conforming_filenames_yield_no_findings(self, name: str) -> None:
        """Feature: phase-1-learning-docs, Property 10: Topic_Doc filename conformance."""
        doc = _topic_doc_with_filename(name)
        assert check_ldc001(doc) == []

    @settings(max_examples=100)
    @given(name=_non_conforming_filenames)
    def test_non_conforming_filenames_are_flagged(self, name: str) -> None:
        """Feature: phase-1-learning-docs, Property 10: Topic_Doc filename conformance."""
        doc = _topic_doc_with_filename(name)
        findings = check_ldc001(doc)
        assert findings, f"expected an LDC001 finding for non-conforming filename {name!r}"
        assert all(finding.rule_id == "LDC001" for finding in findings)


# ---- Property 33 generators: Topic_Doc first/second line ------------------


@st.composite
def _conforming_h1_lines(draw: st.DrawFn) -> list[str]:
    """Line 1 is ``# <title>`` (title 3-80 chars), line 2 blank, plus body."""
    title = draw(
        st.text(
            alphabet=_TITLE_ALPHABET,
            min_size=MIN_H1_TITLE_LENGTH,
            max_size=MAX_H1_TITLE_LENGTH,
        )
    )
    body = draw(st.lists(st.text(alphabet=_TITLE_ALPHABET + " ", max_size=40), max_size=5))
    return [f"# {title}", "", *body]


@st.composite
def _missing_h1_lines(draw: st.DrawFn) -> list[str]:
    """Line 1 is not a valid H1 heading (H2, plain text, empty, or no-space)."""
    kind = draw(st.sampled_from(("h2", "plain", "empty", "hash_no_space")))
    if kind == "h2":
        rest = draw(st.text(alphabet=_TITLE_ALPHABET, min_size=1, max_size=20))
        first = f"## {rest}"
    elif kind == "plain":
        first = draw(st.text(alphabet=_TITLE_ALPHABET, min_size=1, max_size=20))
    elif kind == "empty":
        first = ""
    else:  # hash_no_space: '#text' with no separating whitespace
        rest = draw(st.text(alphabet=_TITLE_ALPHABET, min_size=1, max_size=20))
        first = f"#{rest}"
    return [first, ""]


@st.composite
def _short_title_lines(draw: st.DrawFn) -> list[str]:
    """A valid H1 whose title is shorter than the 3-character minimum."""
    length = draw(st.integers(min_value=1, max_value=MIN_H1_TITLE_LENGTH - 1))
    title = draw(st.text(alphabet=_TITLE_ALPHABET, min_size=length, max_size=length))
    return [f"# {title}", ""]


@st.composite
def _long_title_lines(draw: st.DrawFn) -> list[str]:
    """A valid H1 whose title exceeds the 80-character maximum."""
    length = draw(
        st.integers(min_value=MAX_H1_TITLE_LENGTH + 1, max_value=MAX_H1_TITLE_LENGTH + 40)
    )
    title = draw(st.text(alphabet=_TITLE_ALPHABET, min_size=length, max_size=length))
    return [f"# {title}", ""]


@st.composite
def _nonblank_line2_lines(draw: st.DrawFn) -> list[str]:
    """A valid H1 on line 1, but a non-blank line 2."""
    title = draw(
        st.text(
            alphabet=_TITLE_ALPHABET,
            min_size=MIN_H1_TITLE_LENGTH,
            max_size=MAX_H1_TITLE_LENGTH,
        )
    )
    second = draw(st.text(alphabet=_TITLE_ALPHABET, min_size=1, max_size=40))
    return [f"# {title}", second]


_h1_violation_lines = st.one_of(
    _missing_h1_lines(),
    _short_title_lines(),
    _long_title_lines(),
    _nonblank_line2_lines(),
)


class TestProperty33H1Conformance:
    r"""Property 33: ``check_ldc002`` accepts a conforming H1 + blank line, flags violations."""

    @settings(max_examples=100)
    @given(lines=_conforming_h1_lines())
    def test_conforming_h1_yields_no_findings(self, lines: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 33: Topic_Doc H1 conformance."""
        doc = _topic_doc_with_lines(lines)
        assert check_ldc002(doc) == []

    @settings(max_examples=100)
    @given(lines=_h1_violation_lines)
    def test_h1_violations_are_flagged(self, lines: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 33: Topic_Doc H1 conformance."""
        doc = _topic_doc_with_lines(lines)
        findings = check_ldc002(doc)
        assert findings, f"expected an LDC002 finding for H1 violation {lines[:2]!r}"
        assert all(finding.rule_id == "LDC002" for finding in findings)


# ---- Property 13: Doc_Template H2-sequence equality (check_ldc003) --------
#
# ``check_ldc003`` reads only ``doc.sections`` (and ``doc.path`` for finding
# provenance), so a ``TopicDoc`` built from a synthetic list of H2 headings
# exercises the rule end to end with no filesystem I/O. The rule extracts the
# H2 headings in source order, drops a single ``Hands-on checkpoint`` only when
# it sits strictly between ``Common pitfalls`` and ``External reading``, and
# requires the residual to equal ``DOC_TEMPLATE_REQUIRED``.

# The optional heading and the one position at which it is legal: immediately
# between ``Common pitfalls`` and ``External reading``. In the canonical list
# that is the insertion index of ``External reading`` (the optional slots in
# just ahead of it, after ``Common pitfalls``).
_OPTIONAL_HEADING = "Hands-on checkpoint"
_LEGAL_CHECKPOINT_INDEX = DOC_TEMPLATE_REQUIRED.index("External reading")

# H2 texts that are neither required sections nor the optional checkpoint, used
# to generate an unexpected extra heading.
_UNEXPECTED_HEADINGS = ("Overview", "Summary", "Background", "Extra section", "Notes")


def _topic_doc_with_sections(headings: list[str]) -> TopicDoc:
    """Build a minimal ``TopicDoc`` whose H2 ``sections`` carry ``headings``.

    Only ``sections`` matters to ``check_ldc003`` (it inspects each section's
    ``heading`` in source order); ``path`` is a pure ``Path`` used for finding
    provenance and is never read from disk. Each ``Section`` gets a distinct,
    increasing 1-based ``line`` so any emitted finding cites a plausible
    location, and an empty ``body`` (the rule does not read it).
    """
    sections = [
        Section(heading=heading, line=index * 2 + 3, body="")
        for index, heading in enumerate(headings)
    ]
    return TopicDoc(
        path=Path("docs/learning/phase-1/example-topic.md"),
        filename="example-topic.md",
        title="",
        sections=sections,
        fenced_blocks=[],
        internal_links=[],
        external_links=[],
        prerequisites=[],
        raw_lines=[],
    )


@st.composite
def _compliant_headings(draw: st.DrawFn) -> list[str]:
    """The canonical seven-heading sequence, optionally with a legal checkpoint.

    Half the draws return the bare canonical sequence; the other half insert the
    optional ``Hands-on checkpoint`` at its one legal position (immediately
    between ``Common pitfalls`` and ``External reading``). Both are compliant, so
    ``check_ldc003`` must return no findings for either.
    """
    headings = list(DOC_TEMPLATE_REQUIRED)
    if draw(st.booleans()):
        headings.insert(_LEGAL_CHECKPOINT_INDEX, _OPTIONAL_HEADING)
    return headings


@st.composite
def _one_section_missing(draw: st.DrawFn) -> list[str]:
    """The canonical sequence with one required section removed."""
    headings = list(DOC_TEMPLATE_REQUIRED)
    drop_index = draw(st.integers(min_value=0, max_value=len(headings) - 1))
    del headings[drop_index]
    return headings


@st.composite
def _two_adjacent_swapped(draw: st.DrawFn) -> list[str]:
    """The canonical sequence with one adjacent pair of sections swapped."""
    headings = list(DOC_TEMPLATE_REQUIRED)
    first = draw(st.integers(min_value=0, max_value=len(headings) - 2))
    headings[first], headings[first + 1] = headings[first + 1], headings[first]
    return headings


@st.composite
def _checkpoint_illegal(draw: st.DrawFn) -> list[str]:
    """The canonical sequence with ``Hands-on checkpoint`` at an illegal index.

    Every insertion index except the one legal slot is illegal, so the optional
    heading is never dropped and the residual never matches the canonical tuple.
    """
    headings = list(DOC_TEMPLATE_REQUIRED)
    illegal_indices = [
        index for index in range(len(headings) + 1) if index != _LEGAL_CHECKPOINT_INDEX
    ]
    insert_index = draw(st.sampled_from(illegal_indices))
    headings.insert(insert_index, _OPTIONAL_HEADING)
    return headings


@st.composite
def _extra_unexpected_h2(draw: st.DrawFn) -> list[str]:
    """The canonical sequence with one extra, non-template H2 spliced in."""
    headings = list(DOC_TEMPLATE_REQUIRED)
    extra = draw(st.sampled_from(_UNEXPECTED_HEADINGS))
    insert_index = draw(st.integers(min_value=0, max_value=len(headings)))
    headings.insert(insert_index, extra)
    return headings


_non_compliant_headings = st.one_of(
    _one_section_missing(),
    _two_adjacent_swapped(),
    _checkpoint_illegal(),
    _extra_unexpected_h2(),
)


class TestProperty13DocTemplateH2Sequence:
    r"""Property 13: ``check_ldc003`` accepts the canonical H2 sequence, flags deviations.

    The canonical sequence — with or without a legally-placed ``Hands-on
    checkpoint`` — yields no findings; any deviation (a missing required
    section, two sections swapped, an illegally-placed optional checkpoint, or
    an unexpected extra H2) yields at least one ``LDC003`` finding.
    """

    @settings(max_examples=100)
    @given(headings=_compliant_headings())
    def test_canonical_sequence_yields_no_findings(self, headings: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 13: Doc_Template H2 sequence equality."""
        doc = _topic_doc_with_sections(headings)
        assert check_ldc003(doc) == []

    @settings(max_examples=100)
    @given(headings=_non_compliant_headings)
    def test_non_canonical_sequences_are_flagged(self, headings: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 13: Doc_Template H2 sequence equality."""
        doc = _topic_doc_with_sections(headings)
        findings = check_ldc003(doc)
        assert findings, f"expected an LDC003 finding for non-canonical H2 sequence {headings!r}"
        assert all(finding.rule_id == "LDC003" for finding in findings)


# ---------------------------------------------------------------------------
# Per-section content rules — property tests (tasks 2.10-2.15)
# ---------------------------------------------------------------------------
#
# Properties 14-19 each validate one per-section content rule
# (``check_ldc101``, ``check_ldc102``, ``check_ldc004``, ``check_ldc103``,
# ``check_ldc104``, ``check_ldc105``). Every rule reads only ``doc.sections``
# (``check_ldc103`` also resolves Implementation_File paths under a ``root``),
# so a ``TopicDoc`` carrying a single synthetic section exercises each rule end
# to end. ``_topic_doc_with_section`` builds that one-section document; each
# rule re-parses ``Section.body`` for fenced blocks, links, and prose itself.
# Generators below produce BOTH compliant section bodies (rule returns ``[]``)
# and violating bodies (rule returns a finding with the matching rule id).

# A safe synthetic-prose vocabulary: all lowercase, contains none of the LDC004
# banned substrings (no '/', no 'MatchLayer'), never spells 'learning outcomes',
# and never collides with the case-sensitive 'Mistake'/'Symptom'/'Recovery'
# pitfall labels. Reused across every generator that needs filler prose.
_PROSE_WORDS: tuple[str, ...] = (
    "you",
    "will",
    "understand",
    "the",
    "concept",
    "and",
    "apply",
    "core",
    "idea",
    "context",
    "we",
    "explain",
    "how",
    "things",
    "connect",
    "here",
    "this",
    "covers",
    "topic",
    "model",
    "reader",
    "gains",
    "from",
    "section",
)


def _topic_doc_with_section(heading: str, body: str) -> TopicDoc:
    """Build a minimal ``TopicDoc`` carrying a single H2 ``section`` (heading + body).

    The per-section content rules locate their target via ``_find_section`` and
    then re-parse ``Section.body`` themselves, so only ``sections`` needs to be
    populated; ``path`` is a pure ``Path`` for finding provenance and is never
    read from disk. ``line`` is a plausible non-zero heading line so any emitted
    finding cites a sensible location.
    """
    return TopicDoc(
        path=Path("docs/learning/phase-1/example-topic.md"),
        filename="example-topic.md",
        title="",
        sections=[Section(heading=heading, line=3, body=body)],
        fenced_blocks=[],
        internal_links=[],
        external_links=[],
        prerequisites=[],
        raw_lines=[],
    )


@st.composite
def _sentence(draw: st.DrawFn) -> str:
    """A single declarative sentence: filler words capitalised, ending in '.'."""
    words = draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=2, max_size=6))
    return " ".join(words).capitalize() + "."


@st.composite
def _safe_prose_lines(draw: st.DrawFn, min_lines: int = 1, max_lines: int = 5) -> list[str]:
    """A list of filler prose lines free of links, paths, fences, and pitfall labels."""
    count = draw(st.integers(min_value=min_lines, max_value=max_lines))
    lines: list[str] = []
    for _ in range(count):
        words = draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=2, max_size=6))
        lines.append(" ".join(words))
    return lines


# ---- Property 14: Introduction learning outcomes (check_ldc101) -----------

# Labels that satisfy the learning-outcomes regex (learning[\s-]*outcomes?),
# none of which themselves end in a period (so none counts as a sentence).
_LEARNING_OUTCOME_LABELS: tuple[str, ...] = (
    "Learning outcomes:",
    "Learning Outcomes",
    "The learning outcomes are:",
    "By the end, the learning-outcomes you gain:",
)


@st.composite
def _ldc101_compliant_body(draw: st.DrawFn) -> str:
    """A labelled learning-outcomes list/paragraph with at least three sentences."""
    label = draw(st.sampled_from(_LEARNING_OUTCOME_LABELS))
    sentences = draw(st.lists(_sentence(), min_size=3, max_size=6))
    return label + "\n\n" + "\n".join(sentences)


@st.composite
def _ldc101_missing_label(draw: st.DrawFn) -> str:
    """Three or more sentences but no learning-outcomes label."""
    sentences = draw(st.lists(_sentence(), min_size=3, max_size=6))
    return "\n".join(sentences)


@st.composite
def _ldc101_too_few_sentences(draw: st.DrawFn) -> str:
    """A learning-outcomes label but fewer than three sentences."""
    label = draw(st.sampled_from(_LEARNING_OUTCOME_LABELS))
    sentences = draw(st.lists(_sentence(), min_size=0, max_size=2))
    return label if not sentences else label + "\n\n" + "\n".join(sentences)


_ldc101_violations = st.one_of(_ldc101_missing_label(), _ldc101_too_few_sentences())


class TestProperty14IntroductionLearningOutcomes:
    r"""Property 14: ``check_ldc101`` accepts a labelled >=3-outcome Introduction."""

    @settings(max_examples=100)
    @given(body=_ldc101_compliant_body())
    def test_compliant_introduction_yields_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 14: Introduction learning outcomes."""
        doc = _topic_doc_with_section("Introduction", body)
        assert check_ldc101(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc101_violations)
    def test_violations_are_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 14: Introduction learning outcomes."""
        doc = _topic_doc_with_section("Introduction", body)
        findings = check_ldc101(doc)
        assert findings, f"expected an LDC101 finding for Introduction body {body!r}"
        assert all(finding.rule_id == "LDC101" for finding in findings)


# ---- Property 15: Mental model handhold (check_ldc102) --------------------


@st.composite
def _ldc102_numbered_list(draw: st.DrawFn) -> str:
    """An ordered Markdown list with at least three items."""
    count = draw(st.integers(min_value=3, max_value=6))
    sep = draw(st.sampled_from((".", ")")))
    items: list[str] = []
    for index in range(1, count + 1):
        words = draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=2, max_size=5))
        items.append(f"{index}{sep} " + " ".join(words))
    return "\n".join(items)


@st.composite
def _ldc102_diagram(draw: st.DrawFn) -> str:
    """A fenced ``text`` (ASCII art) or ``mermaid`` diagram block."""
    language = draw(st.sampled_from(("text", "mermaid")))
    inner = " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=1, max_size=4)))
    return f"```{language}\n{inner}\n```"


@st.composite
def _ldc102_image(draw: st.DrawFn) -> str:
    """A Markdown image reference, optionally preceded by a prose line."""
    alt = " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=1, max_size=3)))
    name = draw(st.sampled_from(("diagram.png", "model.svg", "sketch.png")))
    image = f"![{alt}]({name})"
    intro = " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=0, max_size=4)))
    return f"{intro}\n\n{image}" if intro else image


@st.composite
def _ldc102_violation(draw: st.DrawFn) -> str:
    """Prose and/or bulleted lines with no numbered list, diagram, or image."""
    count = draw(st.integers(min_value=1, max_value=5))
    lines: list[str] = []
    for _ in range(count):
        words = " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=2, max_size=6)))
        bullet = draw(st.booleans())
        lines.append(f"- {words}" if bullet else words)
    return "\n".join(lines)


_ldc102_compliant = st.one_of(_ldc102_numbered_list(), _ldc102_diagram(), _ldc102_image())


class TestProperty15MentalModelHandhold:
    r"""Property 15: ``check_ldc102`` accepts a Mental model with a concrete handhold."""

    @settings(max_examples=100)
    @given(body=_ldc102_compliant)
    def test_handhold_yields_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 15: Mental model handhold."""
        doc = _topic_doc_with_section("Mental model", body)
        assert check_ldc102(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc102_violation())
    def test_missing_handhold_is_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 15: Mental model handhold."""
        doc = _topic_doc_with_section("Mental model", body)
        findings = check_ldc102(doc)
        assert findings, f"expected an LDC102 finding for Mental model body {body!r}"
        assert all(finding.rule_id == "LDC102" for finding in findings)


# ---- Property 16: How it works is implementation-agnostic (check_ldc004) --


@st.composite
def _ldc004_compliant_body(draw: st.DrawFn) -> str:
    """Prose free of banned strings, optionally with banned strings INSIDE a fence.

    Banned path prefixes and the literal ``MatchLayer`` are allowed inside
    fenced code blocks, so half the draws append a fenced block that carries
    them to prove the rule ignores fenced content.
    """
    lines = draw(_safe_prose_lines())
    if draw(st.booleans()):
        language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
        banned = draw(st.sampled_from(HOW_IT_WORKS_BANNED_STRINGS))
        lines = [*lines, "", f"```{language}", f"# wiring under {banned} for MatchLayer", "```"]
    return "\n".join(lines)


@st.composite
def _ldc004_violation_body(draw: st.DrawFn) -> str:
    """Prose with one banned path prefix or the product name injected outside fences."""
    lines = draw(_safe_prose_lines())
    banned = draw(st.sampled_from(HOW_IT_WORKS_BANNED_STRINGS))
    target = draw(st.integers(min_value=0, max_value=len(lines) - 1))
    lines[target] = f"{lines[target]} references {banned} here"
    return "\n".join(lines)


class TestProperty16HowItWorksImplementationAgnostic:
    r"""Property 16: ``check_ldc004`` accepts banned-string-free prose, flags leaks."""

    @settings(max_examples=100)
    @given(body=_ldc004_compliant_body())
    def test_agnostic_body_yields_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 16: How it works is implementation-agnostic."""
        doc = _topic_doc_with_section("How it works", body)
        assert check_ldc004(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc004_violation_body())
    def test_banned_string_in_prose_is_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 16: How it works is implementation-agnostic."""
        doc = _topic_doc_with_section("How it works", body)
        findings = check_ldc004(doc)
        assert findings, f"expected an LDC004 finding for How it works body {body!r}"
        assert all(finding.rule_id == "LDC004" for finding in findings)


# ---- Property 17: MatchLayer Phase 1 usage anchored content (check_ldc103) -
#
# ``check_ldc103`` resolves Implementation_File paths against a repository root,
# so it needs real files on disk. ``ldc103_root`` (module-scoped) materialises a
# tmp root once with a known existing Implementation_File; generators reference
# that relative path for the compliant case and a non-existent sibling for the
# violation case.

_LDC103_EXISTING_REL: str = "apps/api/src/matchlayer_api/main.py"
_LDC103_MISSING_REL: str = "apps/api/src/matchlayer_api/does-not-exist.py"


@pytest.fixture(scope="module")
def ldc103_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tmp repo root containing the single existing Implementation_File used below."""
    root = tmp_path_factory.mktemp("ldc103-root")
    implementation_file = root / _LDC103_EXISTING_REL
    implementation_file.parent.mkdir(parents=True, exist_ok=True)
    implementation_file.write_text("def create_app():\n    return None\n", encoding="utf-8")
    return root


@st.composite
def _ldc103_compliant_body(draw: st.DrawFn) -> str:
    """Usage prose plus a fenced block sourced to the existing Implementation_File."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    intro = " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=2, max_size=6)))
    code = draw(st.sampled_from(("def create_app():", "value = compute()", "x = 1")))
    return f"{intro}\n\nSource: `{_LDC103_EXISTING_REL}`\n\n```{language}\n{code}\n```"


@st.composite
def _ldc103_missing_source(draw: st.DrawFn) -> str:
    """A fenced block sourced to a path that does NOT resolve under the root."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    code = draw(st.sampled_from(("def f():", "y = 2")))
    return f"Usage notes.\n\nSource: `{_LDC103_MISSING_REL}`\n\n```{language}\n{code}\n```"


@st.composite
def _ldc103_no_sourced_block(draw: st.DrawFn) -> str:
    """Prose plus an UNSOURCED fenced block: no resolvable path, no Source citation."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    code = draw(st.sampled_from(("def f():", "z = 3")))
    lines = draw(_safe_prose_lines(min_lines=1, max_lines=3))
    return "\n".join(lines) + f"\n\n```{language}\n{code}\n```"


_ldc103_violations = st.one_of(_ldc103_missing_source(), _ldc103_no_sourced_block())


class TestProperty17UsageAnchoredContent:
    r"""Property 17: ``check_ldc103`` accepts usage anchored to an Implementation_File."""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(body=_ldc103_compliant_body())
    def test_anchored_usage_yields_no_findings(self, ldc103_root: Path, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 17: Usage section anchors to a file."""
        doc = _topic_doc_with_section("MatchLayer Phase 1 usage", body)
        assert check_ldc103(doc, ldc103_root) == []

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(body=_ldc103_violations)
    def test_unanchored_usage_is_flagged(self, ldc103_root: Path, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 17: Usage section anchors to a file."""
        doc = _topic_doc_with_section("MatchLayer Phase 1 usage", body)
        findings = check_ldc103(doc, ldc103_root)
        assert findings, f"expected an LDC103 finding for usage body {body!r}"
        assert all(finding.rule_id == "LDC103" for finding in findings)


# ---- Property 18: Common pitfalls labelled entries (check_ldc104) ---------


@st.composite
def _pitfall_value(draw: st.DrawFn) -> str:
    """Non-empty filler text for a pitfall label."""
    return " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=1, max_size=4)))


@st.composite
def _ldc104_compliant_body(draw: st.DrawFn) -> str:
    """Three or more entries, each labelling Mistake/Symptom/Recovery with text."""
    count = draw(st.integers(min_value=3, max_value=6))
    entries: list[str] = []
    for _ in range(count):
        mistake = draw(_pitfall_value())
        symptom = draw(_pitfall_value())
        recovery = draw(_pitfall_value())
        entries.append(f"- Mistake: {mistake}. Symptom: {symptom}. Recovery: {recovery}.")
    return "\n".join(entries)


@st.composite
def _ldc104_too_few(draw: st.DrawFn) -> str:
    """Fewer than three complete entries (zero, one, or two)."""
    count = draw(st.integers(min_value=0, max_value=2))
    entries: list[str] = []
    for _ in range(count):
        mistake = draw(_pitfall_value())
        symptom = draw(_pitfall_value())
        recovery = draw(_pitfall_value())
        entries.append(f"- Mistake: {mistake}. Symptom: {symptom}. Recovery: {recovery}.")
    if not entries:
        return "\n".join(draw(_safe_prose_lines()))
    return "\n".join(entries)


@st.composite
def _ldc104_missing_one_label(draw: st.DrawFn) -> str:
    """Three or more entries, but one of the three labels is dropped from every entry."""
    count = draw(st.integers(min_value=3, max_value=6))
    dropped = draw(st.sampled_from(("Mistake", "Symptom", "Recovery")))
    entries: list[str] = []
    for _ in range(count):
        parts = [
            f"{label}: {draw(_pitfall_value())}."
            for label in ("Mistake", "Symptom", "Recovery")
            if label != dropped
        ]
        entries.append("- " + " ".join(parts))
    return "\n".join(entries)


_ldc104_violations = st.one_of(_ldc104_too_few(), _ldc104_missing_one_label())


class TestProperty18CommonPitfallsLabelledEntries:
    r"""Property 18: ``check_ldc104`` accepts >=3 fully-labelled pitfall entries."""

    @settings(max_examples=100)
    @given(body=_ldc104_compliant_body())
    def test_three_labelled_entries_yield_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 18: Common pitfalls labelled entries."""
        doc = _topic_doc_with_section("Common pitfalls", body)
        assert check_ldc104(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc104_violations)
    def test_incomplete_entries_are_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 18: Common pitfalls labelled entries."""
        doc = _topic_doc_with_section("Common pitfalls", body)
        findings = check_ldc104(doc)
        assert findings, f"expected an LDC104 finding for Common pitfalls body {body!r}"
        assert all(finding.rule_id == "LDC104" for finding in findings)


# ---- Property 19: External reading size bounds (check_ldc105) -------------


@st.composite
def _ldc105_compliant_body(draw: st.DrawFn) -> str:
    """Between 1 and 10 Markdown hyperlinks, one per line."""
    count = draw(st.integers(min_value=1, max_value=10))
    lines: list[str] = []
    for index in range(count):
        words = " ".join(draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=1, max_size=3)))
        lines.append(f"- [{words} {index}](https://example.com/{index})")
    return "\n".join(lines)


@st.composite
def _ldc105_zero_links(draw: st.DrawFn) -> str:
    """Prose with no Markdown hyperlinks at all."""
    return "\n".join(draw(_safe_prose_lines(min_lines=1, max_lines=5)))


@st.composite
def _ldc105_too_many_links(draw: st.DrawFn) -> str:
    """More than ten Markdown hyperlinks."""
    count = draw(st.integers(min_value=11, max_value=20))
    return "\n".join(f"- [link {index}](https://example.com/{index})" for index in range(count))


_ldc105_violations = st.one_of(_ldc105_zero_links(), _ldc105_too_many_links())


class TestProperty19ExternalReadingSizeBounds:
    r"""Property 19: ``check_ldc105`` accepts 1-10 links, flags 0 or >10."""

    @settings(max_examples=100)
    @given(body=_ldc105_compliant_body())
    def test_in_bounds_link_count_yields_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 19: External reading size bounds."""
        doc = _topic_doc_with_section("External reading", body)
        assert check_ldc105(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc105_violations)
    def test_out_of_bounds_link_count_is_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 19: External reading size bounds."""
        doc = _topic_doc_with_section("External reading", body)
        findings = check_ldc105(doc)
        assert findings, f"expected an LDC105 finding for External reading body {body!r}"
        assert all(finding.rule_id == "LDC105" for finding in findings)


# ---------------------------------------------------------------------------
# File-reference and code-snippet rules — property tests (tasks 2.17-2.20)
# ---------------------------------------------------------------------------
#
# Properties 27-30 validate the four file-reference / code-snippet rules
# (``check_ldc005``, ``check_ldc006``, ``check_ldc007``, ``check_ldc008``).
#
# Three of these rules (LDC005/006/007) resolve repository-root-relative paths
# against a ``root``, so they need real files on disk. ``file_ref_root``
# (module-scoped, mirroring the ``ldc103_root`` pattern above) materialises a
# tmp repo root once, holding a single real multi-line Implementation_File. The
# LDC005/006/007 generators build a Markdown string and round-trip it through
# the real ``parse_topic_doc`` parser — which is what attaches ``source_path``
# and ``is_simplified`` to fenced blocks — and then call the rule against that
# tmp root, exercising the genuine parser -> rule pipeline.
#
# LDC008 is purely textual (it reads only each block's language tag), so its
# tests construct ``FencedBlock`` records directly via ``_topic_doc_with_blocks``
# with no filesystem I/O.

# The single real Implementation_File the LDC005/006/007 roots expose, and a
# sibling path that is deliberately absent (used for the non-resolving cases).
_IMPL_REL: str = "apps/api/src/matchlayer_api/main.py"
_MISSING_REL: str = "apps/api/src/matchlayer_api/does-not-exist.py"

# The exact lines of that file. LDC006's compliant generator selects ordered
# subsets of the non-blank lines as a whole-line subsequence of the source.
_IMPL_LINES: tuple[str, ...] = (
    "from fastapi import FastAPI",
    "",
    "def create_app() -> FastAPI:",
    "    app = FastAPI()",
    "    return app",
)
_IMPL_NONBLANK_INDICES: tuple[int, ...] = tuple(
    index for index, line in enumerate(_IMPL_LINES) if line
)


@pytest.fixture(scope="module")
def file_ref_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tmp repo root holding the single real Implementation_File used below."""
    root = tmp_path_factory.mktemp("file-ref-root")
    implementation_file = root / _IMPL_REL
    implementation_file.parent.mkdir(parents=True, exist_ok=True)
    implementation_file.write_text("\n".join(_IMPL_LINES) + "\n", encoding="utf-8")
    return root


def _parse_markdown(root: Path, markdown: str) -> TopicDoc:
    """Write ``markdown`` into ``root`` and return the parsed ``TopicDoc``.

    Round-tripping through ``parse_topic_doc`` is deliberate: the parser is what
    classifies fenced blocks (their language, ``Source:`` citation, and the
    ``simplified for illustration`` label) and links, so the rule under test sees
    exactly what it would see for a real on-disk Topic_Doc. The doc file lives at
    the repo-root top level and is never itself a path the rules resolve.
    """
    doc_path = root / "_generated_topic.md"
    doc_path.write_text(markdown, encoding="utf-8")
    return parse_topic_doc(doc_path)


def _prose(draw: st.DrawFn, *, min_words: int = 2, max_words: int = 6) -> str:
    """A single line of filler prose drawn from the safe vocabulary."""
    words = draw(st.lists(st.sampled_from(_PROSE_WORDS), min_size=min_words, max_size=max_words))
    return " ".join(words)


# ---- Property 27: Implementation_File reference resolves (check_ldc005) ----
#
# ``check_ldc005`` inspects the ``MatchLayer Phase 1 usage`` section: every
# path-like inline-code token (and every block ``Source:`` citation) must be a
# POSIX repo-root-relative path (no leading ``/``, no ``./``, no ``\``) that
# resolves under ``root``. The generators reference the real ``_IMPL_REL`` for
# the compliant case and a malformed or non-resolving path for the violations.


def _usage_markdown(reference_line: str) -> str:
    """A minimal Topic_Doc whose usage section carries ``reference_line``."""
    return (
        "# Implementation File Reference Topic\n\n"
        "## MatchLayer Phase 1 usage\n\n"
        f"{reference_line}\n"
    )


@st.composite
def _ldc005_compliant_markdown(draw: st.DrawFn) -> str:
    """Usage prose citing the real Implementation_File via inline code."""
    lead = _prose(draw)
    tail = _prose(draw)
    return _usage_markdown(f"{lead} `{_IMPL_REL}` {tail}.")


@st.composite
def _ldc005_malformed_markdown(draw: st.DrawFn) -> str:
    """Usage prose citing a path with a leading ``/`` or ``./`` (not well-formed)."""
    prefix = draw(st.sampled_from(("/", "./")))
    return _usage_markdown(f"{_prose(draw)} `{prefix}{_IMPL_REL}` here.")


@st.composite
def _ldc005_missing_markdown(draw: st.DrawFn) -> str:
    """Usage prose citing a well-formed path that does not resolve under root."""
    return _usage_markdown(f"{_prose(draw)} `{_MISSING_REL}` here.")


_ldc005_violations = st.one_of(_ldc005_malformed_markdown(), _ldc005_missing_markdown())


class TestProperty27ImplementationFileReferenceResolves:
    r"""Property 27: ``check_ldc005`` accepts well-formed resolving refs, flags the rest."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc005_compliant_markdown())
    def test_resolving_reference_yields_no_findings(
        self, file_ref_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 27: Implementation_File reference resolves."""
        doc = _parse_markdown(file_ref_root, markdown)
        assert check_ldc005(doc, file_ref_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc005_violations)
    def test_malformed_or_missing_reference_is_flagged(
        self, file_ref_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 27: Implementation_File reference resolves."""
        doc = _parse_markdown(file_ref_root, markdown)
        findings = check_ldc005(doc, file_ref_root)
        assert findings, f"expected an LDC005 finding for usage markdown {markdown!r}"
        assert all(finding.rule_id == "LDC005" for finding in findings)


# ---- Property 28: Sourced fenced block matches its source (check_ldc006) ---
#
# ``check_ldc006`` requires every fenced block carrying a ``Source: `<path>` ``
# citation to (a) cite a path that resolves under ``root`` and (b) have a body
# that is a whole-line subsequence of that file. The compliant generator emits a
# block whose body is an ordered subset of the real file's lines; the violations
# edit a line out of subsequence, or cite a missing path.


def _sourced_block_markdown(cited_rel: str, language: str, body: str) -> str:
    """A Topic_Doc with one fenced block citing ``cited_rel`` immediately above it."""
    return (
        "# Sourced Block Topic\n\n"
        "## MatchLayer Phase 1 usage\n\n"
        f"Source: `{cited_rel}`\n\n"
        f"```{language}\n{body}\n```\n"
    )


@st.composite
def _impl_subsequence(draw: st.DrawFn) -> list[str]:
    """An ordered, non-empty subset of the source file's non-blank lines."""
    indices = draw(
        st.lists(
            st.sampled_from(_IMPL_NONBLANK_INDICES),
            min_size=1,
            max_size=len(_IMPL_NONBLANK_INDICES),
            unique=True,
        )
    )
    indices.sort()
    return [_IMPL_LINES[index] for index in indices]


@st.composite
def _ldc006_compliant_markdown(draw: st.DrawFn) -> str:
    """A block sourced to the real file whose body is a whole-line subsequence."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    body = "\n".join(draw(_impl_subsequence()))
    return _sourced_block_markdown(_IMPL_REL, language, body)


@st.composite
def _ldc006_edited_markdown(draw: st.DrawFn) -> str:
    """A block sourced to the real file but with one line edited out of subsequence."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    lines = draw(_impl_subsequence())
    target = draw(st.integers(min_value=0, max_value=len(lines) - 1))
    # The marker never appears in the source file, so the edited line matches no
    # file line and the body is no longer a whole-line subsequence.
    lines[target] = f"{lines[target]}  # NOT-IN-SOURCE-MARKER"
    return _sourced_block_markdown(_IMPL_REL, language, "\n".join(lines))


@st.composite
def _ldc006_missing_source_markdown(draw: st.DrawFn) -> str:
    """A subsequence block whose ``Source:`` path does not resolve under root."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    body = "\n".join(draw(_impl_subsequence()))
    return _sourced_block_markdown(_MISSING_REL, language, body)


_ldc006_violations = st.one_of(_ldc006_edited_markdown(), _ldc006_missing_source_markdown())


class TestProperty28SourcedBlockMatchesSource:
    r"""Property 28: ``check_ldc006`` accepts subsequence blocks, flags edits/missing sources."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc006_compliant_markdown())
    def test_subsequence_block_yields_no_findings(self, file_ref_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 28: Sourced fenced block matches its source."""
        doc = _parse_markdown(file_ref_root, markdown)
        assert check_ldc006(doc, file_ref_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc006_violations)
    def test_edited_or_missing_source_is_flagged(self, file_ref_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 28: Sourced fenced block matches its source."""
        doc = _parse_markdown(file_ref_root, markdown)
        findings = check_ldc006(doc, file_ref_root)
        assert findings, f"expected an LDC006 finding for markdown {markdown!r}"
        assert all(finding.rule_id == "LDC006" for finding in findings)


# ---- Property 29: Simplified-for-illustration block is labelled (check_ldc007)
#
# ``check_ldc007`` requires every fenced block that carries NO ``Source:``
# citation to be (a) preceded by the literal phrase ``simplified for
# illustration`` and (b) followed by a Markdown link to a resolving repo path.
# The compliant generator satisfies both; the violations drop the label, drop
# the link, or point the link at a non-resolving path.

_SAMPLE_CODE_BODIES: tuple[str, ...] = (
    "def add(first, second):\n    return first + second",
    "value = compute()",
    "result = total / count",
)


def _illustrative_markdown(*, preceding: str, language: str, body: str, following: str) -> str:
    """A Topic_Doc with one unsourced block framed by ``preceding`` and ``following``."""
    return (
        "# Simplified Illustration Topic\n\n"
        "## How it works\n\n"
        f"{preceding}\n\n"
        f"```{language}\n{body}\n```\n\n"
        f"{following}\n"
    )


@st.composite
def _ldc007_compliant_markdown(draw: st.DrawFn) -> str:
    """A labelled illustrative block followed by a link to the real file."""
    preceding = f"{_prose(draw)}, simplified for illustration:"
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    body = draw(st.sampled_from(_SAMPLE_CODE_BODIES))
    following = f"See [{_prose(draw, min_words=1, max_words=3)}]({_IMPL_REL}) for the full version."
    return _illustrative_markdown(
        preceding=preceding, language=language, body=body, following=following
    )


@st.composite
def _ldc007_unlabelled_markdown(draw: st.DrawFn) -> str:
    """A block with a resolving link but no ``simplified for illustration`` label."""
    preceding = f"{_prose(draw)} here is an example."
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    body = draw(st.sampled_from(_SAMPLE_CODE_BODIES))
    following = f"See [{_prose(draw, min_words=1, max_words=3)}]({_IMPL_REL}) for the full version."
    return _illustrative_markdown(
        preceding=preceding, language=language, body=body, following=following
    )


@st.composite
def _ldc007_unlinked_markdown(draw: st.DrawFn) -> str:
    """A labelled block followed by plain prose (no Markdown link at all)."""
    preceding = f"{_prose(draw)}, simplified for illustration:"
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    body = draw(st.sampled_from(_SAMPLE_CODE_BODIES))
    following = f"{_prose(draw)} and nothing more."
    return _illustrative_markdown(
        preceding=preceding, language=language, body=body, following=following
    )


@st.composite
def _ldc007_bad_link_markdown(draw: st.DrawFn) -> str:
    """A labelled block followed by a link whose target does not resolve."""
    preceding = f"{_prose(draw)}, simplified for illustration:"
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    body = draw(st.sampled_from(_SAMPLE_CODE_BODIES))
    following = f"See [{_prose(draw, min_words=1, max_words=3)}]({_MISSING_REL}) for details."
    return _illustrative_markdown(
        preceding=preceding, language=language, body=body, following=following
    )


_ldc007_violations = st.one_of(
    _ldc007_unlabelled_markdown(),
    _ldc007_unlinked_markdown(),
    _ldc007_bad_link_markdown(),
)


class TestProperty29SimplifiedBlockIsLabelledAndLinked:
    r"""Property 29: ``check_ldc007`` accepts labelled+linked blocks, flags the rest."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc007_compliant_markdown())
    def test_labelled_and_linked_block_yields_no_findings(
        self, file_ref_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 29: Simplified-for-illustration block."""
        doc = _parse_markdown(file_ref_root, markdown)
        assert check_ldc007(doc, file_ref_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc007_violations)
    def test_unlabelled_or_unlinked_block_is_flagged(
        self, file_ref_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 29: Simplified-for-illustration block."""
        doc = _parse_markdown(file_ref_root, markdown)
        findings = check_ldc007(doc, file_ref_root)
        assert findings, f"expected an LDC007 finding for markdown {markdown!r}"
        assert all(finding.rule_id == "LDC007" for finding in findings)


# ---- Property 30: Fenced blocks use allowed language tags (check_ldc008) ---
#
# ``check_ldc008`` reads only each fenced block's language tag, so these tests
# build ``FencedBlock`` records directly (no filesystem I/O). A tag is compliant
# when, after stripping surrounding whitespace, it is a member of
# ``ALLOWED_LANGUAGES``; an unrecognised tag or an empty one is a finding.

# A spread of realistic tags that are NOT in the allowed set, including
# case-variants (the set is all-lowercase), an empty tag, and a whitespace-only
# tag (which strips to empty).
_KNOWN_DISALLOWED_LANGUAGES: tuple[str, ...] = (
    "ruby",
    "go",
    "rust",
    "java",
    "html",
    "css",
    "toml",
    "Python",
    "JSON",
    "py",
    "",
    "   ",
)


def _topic_doc_with_blocks(languages: list[str]) -> TopicDoc:
    """Build a minimal ``TopicDoc`` carrying one ``FencedBlock`` per language tag.

    Only ``fenced_blocks`` matters to ``check_ldc008`` (it reads each block's
    ``language`` and ``line``); ``path`` is a pure ``Path`` for finding
    provenance and is never read from disk. Each block gets a distinct, plausible
    opening-fence line so any emitted finding cites a sensible location.
    """
    blocks = [
        FencedBlock(
            language=language,
            body="value = 1",
            line=index * 4 + 5,
            source_path=None,
            is_simplified=False,
        )
        for index, language in enumerate(languages)
    ]
    return TopicDoc(
        path=Path("docs/learning/phase-1/example-topic.md"),
        filename="example-topic.md",
        title="",
        sections=[],
        fenced_blocks=blocks,
        internal_links=[],
        external_links=[],
        prerequisites=[],
        raw_lines=[],
    )


@st.composite
def _allowed_language_tag(draw: st.DrawFn) -> str:
    """An allowed identifier, optionally padded (the rule strips before matching)."""
    language = draw(st.sampled_from(sorted(ALLOWED_LANGUAGES)))
    pad_left = draw(st.sampled_from(("", " ", "  ", "\t")))
    pad_right = draw(st.sampled_from(("", " ", "  ", "\t")))
    return f"{pad_left}{language}{pad_right}"


@st.composite
def _disallowed_language_tag(draw: st.DrawFn) -> str:
    """A tag that, once stripped, is not a member of the allowed set."""
    return draw(
        st.one_of(
            st.sampled_from(_KNOWN_DISALLOWED_LANGUAGES),
            st.text(max_size=12).filter(lambda tag: tag.strip() not in ALLOWED_LANGUAGES),
        )
    )


class TestProperty30FencedBlockLanguageTags:
    r"""Property 30: ``check_ldc008`` accepts allowed language tags, flags the rest."""

    @settings(max_examples=100)
    @given(languages=st.lists(_allowed_language_tag(), min_size=1, max_size=5))
    def test_allowed_language_tags_yield_no_findings(self, languages: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 30: Fenced code blocks use allowed tags."""
        doc = _topic_doc_with_blocks(languages)
        assert check_ldc008(doc) == []

    @settings(max_examples=100)
    @given(languages=st.lists(_disallowed_language_tag(), min_size=1, max_size=5))
    def test_disallowed_language_tags_are_flagged(self, languages: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 30: Fenced code blocks use allowed tags."""
        doc = _topic_doc_with_blocks(languages)
        findings = check_ldc008(doc)
        assert findings, f"expected an LDC008 finding for language tags {languages!r}"
        assert all(finding.rule_id == "LDC008" for finding in findings)


# ---------------------------------------------------------------------------
# Link-integrity rules — property tests (tasks 2.22-2.25)
# ---------------------------------------------------------------------------
#
# Properties 11, 12, 31, and 32 validate the link-integrity rules
# (``check_ldc009`` for internal resolution + anchor matching, ``check_ldc010``
# for the https-only external scheme, and ``check_ldc106`` for the advisory
# authoritative-host preference).
#
# ``check_ldc009`` and ``check_ldc106`` resolve link targets / read the
# authoritative-host registry relative to a repository root, so they need real
# on-disk state. ``link_root`` and ``authoritative_root`` (both module-scoped,
# mirroring the ``ldc103_root`` / ``file_ref_root`` pattern above) materialise a
# tmp root once with the layout each rule reads. Every generator builds a
# Markdown string, round-trips it through the real ``parse_topic_doc`` parser
# (which is what classifies internal vs external links and captures fragments),
# and then calls the rule against that root — exercising the genuine parser ->
# rule pipeline. ``deadline=None`` because each example writes a file.


# ---- Property 11 & 12: internal link resolution + anchors (check_ldc009) ---
#
# ``check_ldc009`` resolves each internal link's path part relative to the
# Topic_Doc's OWN directory (``doc.path.parent``), so the source Topic_Doc must
# live at a real path under the tmp root. ``link_root`` lays out a phase-1
# directory holding a real ``target.md`` (with known headings -> known slugs), a
# sibling ``another-topic.md``, and a library-level ``README.md`` reachable via
# ``../README.md`` from a phase-1 doc.

# Internal targets that resolve when interpreted relative to a phase-1 doc.
_RESOLVING_INTERNAL_TARGETS: tuple[str, ...] = (
    "target.md",
    "another-topic.md",
    "../README.md",
)

# Internal targets that do NOT resolve: a missing sibling, a missing nested
# path, and an absolute-style leading-slash path (rejected by the resolver).
_NON_RESOLVING_INTERNAL_TARGETS: tuple[str, ...] = (
    "missing.md",
    "nope/deeper.md",
    "/target.md",
)

# Headings authored into ``target.md`` slugify (GitHub-style) to exactly these.
_REAL_TARGET_SLUGS: tuple[str, ...] = (
    "target-topic",  # the H1 '# Target Topic'
    "real-heading",  # '## Real Heading'
    "another-section",  # '## Another Section'
)

# Fragments that match no heading in ``target.md``.
_FAKE_TARGET_SLUGS: tuple[str, ...] = (
    "nonexistent",
    "missing-heading",
    "no-such-anchor",
)


@pytest.fixture(scope="module")
def link_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tmp repo root whose phase-1 dir holds the link targets used below."""
    root = tmp_path_factory.mktemp("link-root")
    phase_1 = root / "docs" / "learning" / "phase-1"
    phase_1.mkdir(parents=True, exist_ok=True)
    # A real target file whose headings give the known slugs above.
    (phase_1 / "target.md").write_text(
        "# Target Topic\n\n## Real Heading\n\nbody\n\n## Another Section\n\nbody\n",
        encoding="utf-8",
    )
    (phase_1 / "another-topic.md").write_text("# Another Topic\n\nbody\n", encoding="utf-8")
    # Reachable from a phase-1 doc via ``../README.md``.
    (root / "docs" / "learning" / "README.md").write_text(
        "# Library Index\n\nbody\n", encoding="utf-8"
    )
    return root


def _parse_phase_1_doc(root: Path, markdown: str) -> TopicDoc:
    """Write ``markdown`` as ``<root>/docs/learning/phase-1/source.md`` and parse it.

    The source Topic_Doc must sit at a real path under the tmp root because
    ``check_ldc009`` resolves internal links relative to ``doc.path.parent``;
    writing it into the phase-1 dir makes ``target.md`` etc. resolve as siblings.
    """
    doc_path = root / "docs" / "learning" / "phase-1" / "source.md"
    doc_path.write_text(markdown, encoding="utf-8")
    return parse_topic_doc(doc_path)


@st.composite
def _ldc009_resolving_markdown(draw: st.DrawFn) -> str:
    """A doc with one internal link whose path resolves relative to the doc dir."""
    target = draw(st.sampled_from(_RESOLVING_INTERNAL_TARGETS))
    text = _prose(draw, min_words=1, max_words=3)
    return f"# Source Topic\n\nSee [{text}]({target}) for details.\n"


@st.composite
def _ldc009_broken_markdown(draw: st.DrawFn) -> str:
    """A doc with one internal link whose path does NOT resolve."""
    target = draw(st.sampled_from(_NON_RESOLVING_INTERNAL_TARGETS))
    text = _prose(draw, min_words=1, max_words=3)
    return f"# Source Topic\n\nSee [{text}]({target}) for details.\n"


class TestProperty11InternalHyperlinkResolution:
    r"""Property 11: ``check_ldc009`` accepts resolving internal links, flags broken ones."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc009_resolving_markdown())
    def test_resolving_internal_link_yields_no_findings(
        self, link_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 11: Internal hyperlink resolution."""
        doc = _parse_phase_1_doc(link_root, markdown)
        assert check_ldc009(doc, link_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc009_broken_markdown())
    def test_broken_internal_link_is_flagged(self, link_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 11: Internal hyperlink resolution."""
        doc = _parse_phase_1_doc(link_root, markdown)
        findings = check_ldc009(doc, link_root)
        assert findings, f"expected an LDC009 finding for broken internal link {markdown!r}"
        assert all(finding.rule_id == "LDC009" for finding in findings)


@st.composite
def _ldc009_matching_fragment_markdown(draw: st.DrawFn) -> str:
    """A doc linking to ``target.md`` with a fragment that matches a real heading."""
    slug = draw(st.sampled_from(_REAL_TARGET_SLUGS))
    text = _prose(draw, min_words=1, max_words=3)
    return f"# Source Topic\n\nSee [{text}](target.md#{slug}) for details.\n"


@st.composite
def _ldc009_dangling_fragment_markdown(draw: st.DrawFn) -> str:
    """A doc linking to ``target.md`` with a fragment matching no heading there."""
    slug = draw(st.sampled_from(_FAKE_TARGET_SLUGS))
    text = _prose(draw, min_words=1, max_words=3)
    return f"# Source Topic\n\nSee [{text}](target.md#{slug}) for details.\n"


class TestProperty12InternalHeadingAnchorMatching:
    r"""Property 12: ``check_ldc009`` accepts fragments matching a real heading, flags the rest.

    The path part (``target.md``) resolves in every case, so the only variable
    is the ``#fragment``: a fragment equal to a target-file heading's
    GitHub-style slug is compliant; any other fragment is a dangling anchor.
    """

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc009_matching_fragment_markdown())
    def test_matching_fragment_yields_no_findings(self, link_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 12: Internal anchor matches a heading."""
        doc = _parse_phase_1_doc(link_root, markdown)
        assert check_ldc009(doc, link_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc009_dangling_fragment_markdown())
    def test_dangling_fragment_is_flagged(self, link_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 12: Internal anchor matches a heading."""
        doc = _parse_phase_1_doc(link_root, markdown)
        findings = check_ldc009(doc, link_root)
        assert findings, f"expected an LDC009 finding for dangling fragment {markdown!r}"
        assert all(finding.rule_id == "LDC009" for finding in findings)


# ---- Property 31: external links use the https scheme (check_ldc010) ------
#
# ``check_ldc010`` is purely textual on ``doc.external_links`` and needs no
# ``root``. The generators still round-trip through ``parse_topic_doc`` (via the
# module-scoped ``scheme_root`` write target) so the parser is what classifies
# each target as external, exactly as it would for a real Topic_Doc.


@pytest.fixture(scope="module")
def scheme_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tmp dir used only as a write target for the LDC010 source docs."""
    return tmp_path_factory.mktemp("scheme-root")


@st.composite
def _https_url(draw: st.DrawFn) -> str:
    """A compliant ``https://`` URL."""
    host = draw(
        st.sampled_from(("example.com", "docs.python.org", "nextjs.org", "developer.mozilla.org"))
    )
    path = draw(st.sampled_from(("", "/", "/a", "/a/b", "/docs/guide")))
    return f"https://{host}{path}"


@st.composite
def _non_https_url(draw: st.DrawFn) -> str:
    """A non-compliant external target: http, protocol-relative, ftp, or mailto."""
    scheme = draw(st.sampled_from(("http://", "//", "ftp://", "mailto:")))
    host = draw(st.sampled_from(("example.com", "docs.python.org", "host.test")))
    if scheme == "mailto:":
        return f"mailto:user@{host}"
    return f"{scheme}{host}/path"


@st.composite
def _ldc010_compliant_markdown(draw: st.DrawFn) -> str:
    """An External reading section whose links are all ``https://``."""
    count = draw(st.integers(min_value=1, max_value=5))
    lines = [
        f"- [{_prose(draw, min_words=1, max_words=2)} {index}]({draw(_https_url())})"
        for index in range(count)
    ]
    return "# External Scheme Topic\n\n## External reading\n\n" + "\n".join(lines) + "\n"


@st.composite
def _ldc010_violation_markdown(draw: st.DrawFn) -> str:
    """An External reading section with at least one non-https external link."""
    count = draw(st.integers(min_value=1, max_value=4))
    lines = [
        f"- [{_prose(draw, min_words=1, max_words=2)} {index}]({draw(_non_https_url())})"
        for index in range(count)
    ]
    return "# External Scheme Topic\n\n## External reading\n\n" + "\n".join(lines) + "\n"


class TestProperty31ExternalLinksUseHttps:
    r"""Property 31: ``check_ldc010`` accepts https-only links, flags every other scheme."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc010_compliant_markdown())
    def test_https_links_yield_no_findings(self, scheme_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 31: External links use the https scheme."""
        doc = _parse_markdown(scheme_root, markdown)
        assert check_ldc010(doc) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc010_violation_markdown())
    def test_non_https_links_are_flagged(self, scheme_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 31: External links use the https scheme."""
        doc = _parse_markdown(scheme_root, markdown)
        findings = check_ldc010(doc)
        assert findings, f"expected an LDC010 finding for non-https links {markdown!r}"
        assert all(finding.rule_id == "LDC010" for finding in findings)


# ---- Property 32: authoritative-source preference (check_ldc106) ----------
#
# ``check_ldc106`` reads the authoritative-host registry from
# ``<root>/docs/learning/CONVENTIONS.md``. ``authoritative_root`` writes a
# minimal CONVENTIONS.md carrying the ``authoritative_hosts:`` YAML block.
# The registry includes one host (``learn.example-authority.test``) that is NOT
# in the ``AUTHORITATIVE_HOSTS`` fallback constant, so a no-findings result on
# that host proves the rule is reading the file rather than the fallback.

_AUTHORITATIVE_REGISTRY_HOSTS: tuple[str, ...] = (
    "developer.mozilla.org",
    "docs.python.org",
    "nextjs.org",
    "learn.example-authority.test",
)

# Hosts deliberately absent from the registry above.
_NON_AUTHORITATIVE_HOSTS: tuple[str, ...] = (
    "some-random-blog.example",
    "medium.com",
    "dev.to",
    "random.example.org",
)


@pytest.fixture(scope="module")
def authoritative_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tmp repo root with a CONVENTIONS.md declaring the host registry above."""
    root = tmp_path_factory.mktemp("authoritative-root")
    learning = root / "docs" / "learning"
    (learning / "phase-1").mkdir(parents=True, exist_ok=True)
    yaml_items = "\n".join(f"  - {host}" for host in _AUTHORITATIVE_REGISTRY_HOSTS)
    (learning / "CONVENTIONS.md").write_text(
        "# Conventions\n\n"
        "## Link rules\n\n"
        "Authoritative-host registry:\n\n"
        "```yaml\n"
        "authoritative_hosts:\n"
        f"{yaml_items}\n"
        "```\n",
        encoding="utf-8",
    )
    return root


@st.composite
def _ldc106_authoritative_markdown(draw: st.DrawFn) -> str:
    """An External reading section whose links all point at registered hosts."""
    count = draw(st.integers(min_value=1, max_value=5))
    lines = []
    for index in range(count):
        host = draw(st.sampled_from(_AUTHORITATIVE_REGISTRY_HOSTS))
        lines.append(f"- [{_prose(draw, min_words=1, max_words=2)} {index}](https://{host}/x)")
    return "# Authoritative Topic\n\n## External reading\n\n" + "\n".join(lines) + "\n"


@st.composite
def _ldc106_non_authoritative_markdown(draw: st.DrawFn) -> str:
    """An External reading section with at least one non-registered host."""
    count = draw(st.integers(min_value=1, max_value=4))
    lines = []
    for index in range(count):
        host = draw(st.sampled_from(_NON_AUTHORITATIVE_HOSTS))
        lines.append(f"- [{_prose(draw, min_words=1, max_words=2)} {index}](https://{host}/x)")
    return "# Secondary Topic\n\n## External reading\n\n" + "\n".join(lines) + "\n"


class TestProperty32AuthoritativeSourcePreference:
    r"""Property 32: ``check_ldc106`` is silent for registered hosts, advises for the rest."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc106_authoritative_markdown())
    def test_registered_hosts_yield_no_findings(
        self, authoritative_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 32: Authoritative source preference."""
        doc = _parse_phase_1_doc(authoritative_root, markdown)
        assert check_ldc106(doc, authoritative_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc106_non_authoritative_markdown())
    def test_non_registered_hosts_are_flagged(
        self, authoritative_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 32: Authoritative source preference."""
        doc = _parse_phase_1_doc(authoritative_root, markdown)
        findings = check_ldc106(doc, authoritative_root)
        assert findings, f"expected an LDC106 finding for non-authoritative hosts {markdown!r}"
        assert all(finding.rule_id == "LDC106" for finding in findings)


# ---------------------------------------------------------------------------
# Beginner-accessibility rules — property tests (tasks 2.27-2.31)
# ---------------------------------------------------------------------------
#
# Properties 22-26 validate the four beginner-accessibility rules
# (``check_ldc012``, ``check_ldc013``, ``check_ldc014``, ``check_ldc015``).
#
# ``check_ldc012`` reads the project-glossary list from ``CONVENTIONS.md`` and
# ``check_ldc015`` resolves prerequisite link targets to Topic_Docs under
# ``docs/learning/phase-1/``; both take ``(doc, root)`` and so need a tmp repo
# root on disk. ``accessibility_root`` (module-scoped) materialises that root
# once: a ``CONVENTIONS.md`` whose "Project glossary" fenced ``text`` block lists
# the terms the LDC012 tests reference, plus two sibling Topic_Docs the LDC015
# tests link to as prerequisites. ``check_ldc013`` and ``check_ldc014`` are
# purely textual, so their tests build one-section ``TopicDoc`` values directly
# via ``_topic_doc_with_section`` with no filesystem I/O.
#
# Note on Property 26 (cross-reference is hyperlinked or inline-defined, Req
# 5.7): there is no dedicated rule function for Req 5.7 in
# ``learning_docs_check.py`` — the design folds cross-reference handling into the
# reviewer gate and into ``check_ldc012``'s definition-on-first-use heuristic
# (the machine-checkable half). ``TestProperty26CrossReference`` therefore
# exercises that machine-checkable half via ``check_ldc012`` and documents that
# the full cross-reference semantics remain a reviewer responsibility.

# A glossary term the accessibility_root CONVENTIONS.md declares and the LDC012
# generators reference. Lowercase so it never collides with an acronym.
_GLOSSARY_TERM = "monorepo"


@pytest.fixture(scope="module")
def accessibility_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tmp repo root with a glossary CONVENTIONS.md and two prerequisite docs."""
    root = tmp_path_factory.mktemp("accessibility-root")
    learning = root / "docs" / "learning"
    phase_1 = learning / "phase-1"
    phase_1.mkdir(parents=True, exist_ok=True)

    # A CONVENTIONS.md whose "Project glossary" fenced ``text`` block lists the
    # single term the LDC012 generators use; parse_glossary_terms reads it.
    learning.joinpath("CONVENTIONS.md").write_text(
        "# Conventions\n\n"
        "## Beginner-accessibility ruleset\n\n"
        "### Project glossary\n\n"
        "```text\n"
        f"{_GLOSSARY_TERM}\n"
        "```\n",
        encoding="utf-8",
    )

    # Two real sibling Topic_Docs the LDC015 prerequisite list links to.
    phase_1.joinpath("prereq-one.md").write_text("# Prereq One\n\nbody\n", encoding="utf-8")
    phase_1.joinpath("prereq-two.md").write_text("# Prereq Two\n\nbody\n", encoding="utf-8")
    return root


def _accessibility_doc(root: Path, markdown: str) -> TopicDoc:
    """Write ``markdown`` as a phase-1 Topic_Doc under ``root`` and parse it."""
    doc_path = root / "docs" / "learning" / "phase-1" / "accessibility-source.md"
    doc_path.write_text(markdown, encoding="utf-8")
    return parse_topic_doc(doc_path)


# ---- Property 22: domain term defined on first use (check_ldc012) ----------
#
# A definition delimiter is a parenthetical, an em-dash clause, or a copular
# ``is``/``are`` clause sharing the term's paragraph on first use.

_DEFINITION_TEMPLATES: tuple[str, ...] = (
    f"A {_GLOSSARY_TERM} (a single repository holding many projects) keeps things together.",
    f"A {_GLOSSARY_TERM} is a single repository that holds many related projects together.",
    f"The {_GLOSSARY_TERM} — one repository for many projects — keeps the tree unified.",
)


@st.composite
def _ldc012_defined_markdown(draw: st.DrawFn) -> str:
    """An Introduction whose first use of the glossary term carries a definition."""
    sentence = draw(st.sampled_from(_DEFINITION_TEMPLATES))
    return f"# Defined Term Topic\n\n## Introduction\n\n{sentence}\n"


@st.composite
def _ldc012_undefined_markdown(draw: st.DrawFn) -> str:
    """An Introduction using the glossary term with no same-paragraph definition."""
    tail = _prose(draw, min_words=2, max_words=5)
    return f"# Undefined Term Topic\n\n## Introduction\n\nWe adopt a {_GLOSSARY_TERM} and {tail}\n"


class TestProperty22DomainTermDefinedOnFirstUse:
    r"""Property 22: ``check_ldc012`` is silent when a term is defined, advises otherwise."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc012_defined_markdown())
    def test_defined_term_yields_no_findings(self, accessibility_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 22: Domain term defined on first use."""
        doc = _accessibility_doc(accessibility_root, markdown)
        assert check_ldc012(doc, accessibility_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc012_undefined_markdown())
    def test_undefined_term_is_flagged(self, accessibility_root: Path, markdown: str) -> None:
        """Feature: phase-1-learning-docs, Property 22: Domain term defined on first use."""
        doc = _accessibility_doc(accessibility_root, markdown)
        findings = check_ldc012(doc, accessibility_root)
        assert findings, f"expected an LDC012 advisory for undefined term in {markdown!r}"
        assert all(finding.rule_id == "LDC012" for finding in findings)


# ---- Property 23: acronym expanded on first use (check_ldc013) -------------


@st.composite
def _ldc013_expanded_body(draw: st.DrawFn) -> str:
    """Prose whose first use of an acronym is in ``Expanded Form (ACRONYM)`` shape.

    The expansions are deliberately free of embedded acronyms (no all-caps runs),
    so the only acronym the rule sees is the one inside the parentheses.
    """
    acronym, expansion = draw(
        st.sampled_from(
            (
                ("CORS", "Cross-Origin Resource Sharing"),
                ("ASGI", "Asynchronous Server Gateway Interface"),
                ("DRY", "Don't Repeat Yourself"),
            )
        )
    )
    lead = _prose(draw, min_words=2, max_words=5)
    return f"{lead.capitalize()} uses {expansion} ({acronym}) for this."


@st.composite
def _ldc013_bare_body(draw: st.DrawFn) -> str:
    """Prose whose first use of an acronym is bare, with no expansion in parens."""
    acronym = draw(st.sampled_from(("CORS", "JWT", "ASGI", "HSTS")))
    lead = _prose(draw, min_words=2, max_words=5)
    return f"{lead.capitalize()} relies on {acronym} for this."


class TestProperty23AcronymExpandedOnFirstUse:
    r"""Property 23: ``check_ldc013`` is silent for expanded acronyms, advises for bare ones."""

    @settings(max_examples=100)
    @given(body=_ldc013_expanded_body())
    def test_expanded_acronym_yields_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 23: Acronym expanded on first use."""
        doc = _topic_doc_with_lines(body.split("\n"))
        assert check_ldc013(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc013_bare_body())
    def test_bare_acronym_is_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 23: Acronym expanded on first use."""
        doc = _topic_doc_with_lines(body.split("\n"))
        findings = check_ldc013(doc)
        assert findings, f"expected an LDC013 advisory for a bare acronym in {body!r}"
        assert all(finding.rule_id == "LDC013" for finding in findings)


# ---- Property 24: banned phrases are absent (check_ldc014) -----------------
#
# The hard banned phrases (everything except ``just``) are emitted as plain
# findings; ``just`` is downgraded to an ``Advisory:`` warning.

_HARD_BANNED_PHRASES: tuple[str, ...] = tuple(p for p in BANNED_PHRASES if p != "just")


@st.composite
def _ldc014_clean_body(draw: st.DrawFn) -> str:
    """Prose drawn from the safe vocabulary, free of every banned phrase."""
    return "\n".join(draw(_safe_prose_lines(min_lines=1, max_lines=4)))


@st.composite
def _ldc014_hard_banned_body(draw: st.DrawFn) -> str:
    """Prose containing one hard banned phrase as a whole word."""
    phrase = draw(st.sampled_from(_HARD_BANNED_PHRASES))
    lead = _prose(draw, min_words=1, max_words=4)
    return f"{lead}, {phrase}, the rest follows."


class TestProperty24BannedPhrasesAbsent:
    r"""Property 24: ``check_ldc014`` is silent for clean prose, flags banned phrases."""

    @settings(max_examples=100)
    @given(body=_ldc014_clean_body())
    def test_clean_prose_yields_no_findings(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 24: Banned phrases are absent."""
        doc = _topic_doc_with_lines(body.split("\n"))
        assert check_ldc014(doc) == []

    @settings(max_examples=100)
    @given(body=_ldc014_hard_banned_body())
    def test_hard_banned_phrase_is_flagged(self, body: str) -> None:
        """Feature: phase-1-learning-docs, Property 24: Banned phrases are absent."""
        doc = _topic_doc_with_lines(body.split("\n"))
        findings = check_ldc014(doc)
        assert findings, f"expected an LDC014 finding for a banned phrase in {body!r}"
        assert all(finding.rule_id == "LDC014" for finding in findings)
        # Hard banned phrases are not downgraded to the 'Advisory:' warning shape.
        assert all(not finding.message.startswith("Advisory:") for finding in findings)

    def test_just_is_emitted_as_an_advisory_warning(self) -> None:
        """Feature: phase-1-learning-docs, Property 24: Banned phrases are absent."""
        doc = _topic_doc_with_lines(["You just call the function and move on."])
        findings = check_ldc014(doc)
        assert findings, "expected an LDC014 advisory for the word 'just'"
        assert all(finding.rule_id == "LDC014" for finding in findings)
        assert all(finding.message.startswith("Advisory:") for finding in findings)


# ---- Property 25: prerequisites declared in the Introduction (check_ldc015) -


@st.composite
def _ldc015_no_prereqs_markdown(draw: st.DrawFn) -> str:
    """An Introduction with an explicit 'No prerequisites' sentence."""
    tail = _prose(draw, min_words=2, max_words=5)
    return f"# No Prereqs Topic\n\n## Introduction\n\nNo prerequisites. {tail.capitalize()}.\n"


@st.composite
def _ldc015_hyperlinked_markdown(draw: st.DrawFn) -> str:
    """An Introduction with a labelled Prerequisites list of resolving Topic_Doc links."""
    one = _prose(draw, min_words=1, max_words=2)
    two = _prose(draw, min_words=1, max_words=2)
    return (
        "# Hyperlinked Prereqs Topic\n\n"
        "## Introduction\n\n"
        "Prerequisites:\n\n"
        f"- [{one}](prereq-one.md)\n"
        f"- [{two}](prereq-two.md)\n"
    )


@st.composite
def _ldc015_missing_markdown(draw: st.DrawFn) -> str:
    """An Introduction that neither states 'no prerequisites' nor lists any links."""
    sentences = draw(st.lists(_sentence(), min_size=1, max_size=3))
    return "# Missing Prereqs Topic\n\n## Introduction\n\n" + " ".join(sentences) + "\n"


_ldc015_compliant_markdown = st.one_of(
    _ldc015_no_prereqs_markdown(),
    _ldc015_hyperlinked_markdown(),
)


class TestProperty25PrerequisitesDeclared:
    r"""Property 25: ``check_ldc015`` accepts declared prerequisites, flags their absence."""

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc015_compliant_markdown)
    def test_declared_prerequisites_yield_no_findings(
        self, accessibility_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 25: Prerequisites declared in Introduction."""
        doc = _accessibility_doc(accessibility_root, markdown)
        assert check_ldc015(doc, accessibility_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc015_missing_markdown())
    def test_missing_prerequisites_is_flagged(
        self, accessibility_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 25: Prerequisites declared in Introduction."""
        doc = _accessibility_doc(accessibility_root, markdown)
        findings = check_ldc015(doc, accessibility_root)
        assert findings, f"expected an LDC015 finding for a prereq-less Introduction {markdown!r}"
        assert all(finding.rule_id == "LDC015" for finding in findings)


# ---- Property 26: cross-reference is hyperlinked or inline-defined ---------
#
# Req 5.7 has no dedicated rule function: the design folds cross-reference
# handling into the reviewer gate plus ``check_ldc012``'s machine-checkable
# definition-on-first-use heuristic. These tests therefore exercise that
# heuristic via ``check_ldc012`` — a referenced concept named without an
# inline definition is surfaced as an advisory, while a same-paragraph
# definition (the inline-define escape hatch of Req 5.7) clears it. The full
# "hyperlinked OR inline-defined" semantics remain a reviewer responsibility.


class TestProperty26CrossReference:
    r"""Property 26: the machine-checkable half of Req 5.7 via ``check_ldc012``.

    Req 5.7 (reference a concept either by hyperlink or by a one-to-three
    sentence inline definition) has no standalone rule; its machine-checkable
    portion is ``check_ldc012``'s definition-on-first-use heuristic. A glossary
    concept used with a same-paragraph inline definition is accepted; the same
    concept used with neither a definition nor (here) a link is surfaced as an
    advisory for reviewer follow-up.
    """

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc012_defined_markdown())
    def test_inline_defined_reference_yields_no_findings(
        self, accessibility_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 26: Cross-reference is defined or linked."""
        doc = _accessibility_doc(accessibility_root, markdown)
        assert check_ldc012(doc, accessibility_root) == []

    @settings(max_examples=100, deadline=None)
    @given(markdown=_ldc012_undefined_markdown())
    def test_undefined_unlinked_reference_is_flagged(
        self, accessibility_root: Path, markdown: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 26: Cross-reference is defined or linked."""
        doc = _accessibility_doc(accessibility_root, markdown)
        findings = check_ldc012(doc, accessibility_root)
        assert findings, f"expected an LDC012 advisory for an undefined reference in {markdown!r}"
        assert all(finding.rule_id == "LDC012" for finding in findings)


# ===========================================================================
# Library-level rules and Library_Index / Phase_1_Index structure
# (tasks 2.33-2.45)
# ===========================================================================
#
# These cover the five library-level rules (``check_ldc016``..``check_ldc019``)
# plus the Library_Index / Phase_1_Index structural properties. Library-level
# rules take a repository ``root`` and read the whole tree, so each Hypothesis
# example builds a *fresh* synthetic root under a ``TemporaryDirectory`` and
# asserts the rule's findings — there is no parsed-``TopicDoc`` shortcut the way
# the per-doc rules have, because the rule resolves filenames and directories on
# disk. Properties with NO dedicated rule function (Req 4.15, 1.6, 1.8, 2.3,
# 2.4, 2.8, 10.1, 10.6) are written as structural/example assertions — against
# the real committed library or a synthetic state — and say so in their
# docstrings, following the precedent set for Property 26 above.

_COVERAGE_HEADER = (
    "| Coverage entry | Requirement clause | Topic_Doc filename | Thematic section |\n"
    "| --- | --- | --- | --- |"
)


def _render_coverage_table(rows: list[tuple[str, str, str, str]]) -> str:
    """Render a Phase_1_Index ``Topic coverage`` Markdown table from 4-tuple rows."""
    lines = [_COVERAGE_HEADER]
    for entry, clause, filename, section in rows:
        lines.append(f"| {entry} | {clause} | {filename} | {section} |")
    return "\n".join(lines)


def _write_phase_1_index_with_coverage(root: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """Write a minimal Phase_1_Index carrying ``rows`` as its coverage table.

    Returns the ``docs/learning/phase-1`` directory so the caller can drop the
    Topic_Doc files the table references next to the index.
    """
    phase_1 = root / "docs" / "learning" / "phase-1"
    phase_1.mkdir(parents=True, exist_ok=True)
    index = (
        "# MatchLayer Phase 1 — Learning Docs\n\n"
        "## Topic coverage\n\n"
        f"{_render_coverage_table(rows)}\n"
    )
    phase_1.joinpath("README.md").write_text(index, encoding="utf-8")
    return phase_1


# ---- Property 20: coverage table maps to an existing Topic_Doc (check_ldc016)


@st.composite
def _ldc016_compliant(draw: st.DrawFn) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """A fully-authored coverage table: every entry maps to one created file.

    A single Topic_Doc filename is shared across every coverage row — a legal
    consolidation — so each canonical entry has a non-empty filename cell and
    that one file is created on disk.
    """
    filename = draw(_conforming_filenames())
    rows = [
        (entry, "4.2", filename, "Foundation and tooling") for entry in PHASE_1_COVERAGE_ENTRIES
    ]
    return rows, [filename]


@st.composite
def _ldc016_violation(draw: st.DrawFn) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """A coverage table with either a blank filename cell or a non-existent file."""
    filename = draw(_conforming_filenames())
    mutable = [
        [entry, "4.2", filename, "Foundation and tooling"] for entry in PHASE_1_COVERAGE_ENTRIES
    ]
    mode = draw(st.sampled_from(("blank_cell", "missing_file")))
    if mode == "blank_cell":
        idx = draw(st.integers(min_value=0, max_value=len(mutable) - 1))
        mutable[idx][2] = ""  # an authored entry left without a Topic_Doc filename
        created = [filename]
    else:
        created = []  # cells non-empty, but the file is never created on disk
    rows = [(a, b, c, d) for a, b, c, d in mutable]
    return rows, created


class TestProperty20CoverageTableMapsToExistingDoc:
    r"""Property 20: ``check_ldc016`` is silent for a fully-authored, resolving table.

    Rule-backed property test. A coverage table whose every canonical entry
    names a created Topic_Doc file yields no findings; blanking a row's filename
    cell (Req 4.14) or naming a file that does not exist (Req 4.16) yields an
    ``LDC016`` finding.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(state=_ldc016_compliant())
    def test_fully_authored_table_yields_no_findings(
        self, state: tuple[list[tuple[str, str, str, str]], list[str]]
    ) -> None:
        """Feature: phase-1-learning-docs, Property 20: Coverage maps entries to existing docs."""
        rows, created = state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            phase_1 = _write_phase_1_index_with_coverage(root, rows)
            for filename in created:
                phase_1.joinpath(filename).write_text("# Topic\n\nbody\n", encoding="utf-8")
            assert check_ldc016(root) == []

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(state=_ldc016_violation())
    def test_blank_or_missing_filename_is_flagged(
        self, state: tuple[list[tuple[str, str, str, str]], list[str]]
    ) -> None:
        """Feature: phase-1-learning-docs, Property 20: Coverage maps entries to existing docs."""
        rows, created = state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            phase_1 = _write_phase_1_index_with_coverage(root, rows)
            for filename in created:
                phase_1.joinpath(filename).write_text("# Topic\n\nbody\n", encoding="utf-8")
            findings = check_ldc016(root)
            assert findings, f"expected an LDC016 finding for coverage state {created!r}"
            assert all(finding.rule_id == "LDC016" for finding in findings)


# ---- Property 21: consolidated docs name every consolidated entry (structural)
#
# Req 4.15 has no dedicated rule function: the design lets ``check_ldc016``
# guarantee every coverage entry maps to an authored file, but the
# "consolidated Topic_Doc names every consolidated entry in its Introduction"
# half of Req 4.15 is a reviewer responsibility. This is therefore a STRUCTURAL
# test (mirroring the Property 26 precedent): it builds a synthetic library
# state and exercises the predicate directly through the real ``parse_*``
# helpers rather than through a rule.


def _consolidation_satisfied(root: Path) -> bool:
    """Return True when every multi-entry Topic_Doc names each entry in its Introduction.

    A "consolidated" Topic_Doc is one whose filename is recorded against two or
    more distinct coverage entries in the Phase_1_Index. Req 4.15 requires every
    such entry's verbatim text to appear in that Topic_Doc's ``Introduction``.
    """
    index_path = root / "docs" / "learning" / "phase-1" / "README.md"
    rows: list[CoverageRow] = parse_phase_1_index(index_path)

    by_file: dict[str, list[str]] = {}
    for row in rows:
        filename = row.topic_doc_filename.strip()
        if filename:
            by_file.setdefault(filename, []).append(row.entry_text.strip())

    for filename, entries in by_file.items():
        if len(entries) < 2:
            continue
        doc = parse_topic_doc(index_path.parent / filename)
        intro = next((s for s in doc.sections if s.heading == "Introduction"), None)
        body = intro.body if intro is not None else ""
        if not all(entry in body for entry in entries):
            return False
    return True


def _write_consolidated_state(
    root: Path, entries: list[str], filename: str, intro_mentions: list[str]
) -> None:
    """Write a Phase_1_Index recording ``entries`` against ``filename`` plus that doc."""
    phase_1 = root / "docs" / "learning" / "phase-1"
    phase_1.mkdir(parents=True, exist_ok=True)
    rows = [(entry, "4.2", filename, "Backend") for entry in entries]
    phase_1.joinpath("README.md").write_text(
        "# Phase 1\n\n## Topic coverage\n\n" + _render_coverage_table(rows) + "\n",
        encoding="utf-8",
    )
    intro_body = " ".join(intro_mentions)
    phase_1.joinpath(filename).write_text(
        f"# Consolidated Topic\n\n## Introduction\n\n{intro_body}\n",
        encoding="utf-8",
    )


@st.composite
def _two_distinct_entries(draw: st.DrawFn) -> tuple[str, str]:
    """Two distinct coverage entries, neither a substring of the other."""
    pair = draw(
        st.lists(
            st.sampled_from(PHASE_1_COVERAGE_ENTRIES), min_size=2, max_size=2, unique=True
        ).filter(lambda p: p[0] not in p[1] and p[1] not in p[0])
    )
    return pair[0], pair[1]


class TestProperty21ConsolidatedDocsNameEveryEntry:
    r"""Property 21 (structural): a consolidated Topic_Doc names every entry it covers.

    No rule enforces Req 4.15's Introduction-mention half, so this exercises the
    predicate directly over a synthetic library: a Topic_Doc whose Introduction
    names both consolidated entries satisfies the predicate; omitting one entry
    is detected. The full check remains a reviewer responsibility.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(entries=_two_distinct_entries(), filename=_conforming_filenames())
    def test_introduction_naming_both_entries_satisfies(
        self, entries: tuple[str, str], filename: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 21: Consolidated docs name every entry."""
        first, second = entries
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_consolidated_state(root, [first, second], filename, [first, second])
            assert _consolidation_satisfied(root) is True

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(entries=_two_distinct_entries(), filename=_conforming_filenames())
    def test_introduction_omitting_an_entry_is_detected(
        self, entries: tuple[str, str], filename: str
    ) -> None:
        """Feature: phase-1-learning-docs, Property 21: Consolidated docs name every entry."""
        first, second = entries
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_consolidated_state(root, [first, second], filename, [first])
            assert _consolidation_satisfied(root) is False


# ---- Property 1: Phase Sub-Libraries listing reflects on-disk state (check_ldc018)

_PHASE_NUMBERS = st.lists(
    st.integers(min_value=1, max_value=7), min_size=1, max_size=7, unique=True
).map(sorted)


def _create_phase_dirs(root: Path, numbers: list[int]) -> None:
    """Create ``docs/learning/phase-<n>/`` directories for each ``n`` in ``numbers``."""
    learning = root / "docs" / "learning"
    for number in numbers:
        phase_dir = learning / f"phase-{number}"
        phase_dir.mkdir(parents=True, exist_ok=True)
        phase_dir.joinpath("README.md").write_text(f"# Phase {number}\n\nbody\n", encoding="utf-8")


def _write_phase_sublib_index(root: Path, listed_numbers: list[int]) -> None:
    """Write a Library_Index whose ``Phase Sub-Libraries`` lists ``listed_numbers``."""
    learning = root / "docs" / "learning"
    learning.mkdir(parents=True, exist_ok=True)
    bullets = "\n".join(f"- [phase-{number}/](phase-{number}/)" for number in listed_numbers)
    learning.joinpath("README.md").write_text(
        "# MatchLayer Learning Docs\n\n## Phase Sub-Libraries\n\n" + bullets + "\n",
        encoding="utf-8",
    )


@st.composite
def _ldc018_violation(draw: st.DrawFn) -> tuple[list[int], list[int]]:
    """Present phase dirs with at least one omitted from the listing."""
    present = draw(_PHASE_NUMBERS)
    dropped = draw(
        st.lists(st.sampled_from(present), min_size=1, max_size=len(present), unique=True)
    )
    listed = [number for number in present if number not in dropped]
    return present, listed


class TestProperty1PhaseSubLibrariesListing:
    r"""Property 1: ``check_ldc018`` is silent when every present phase dir is listed.

    Rule-backed property test. Listing every present ``phase-<N>/`` directory in
    the Library_Index ``Phase Sub-Libraries`` section yields no findings;
    omitting any present directory yields an ``LDC018`` finding.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(present=_PHASE_NUMBERS)
    def test_all_present_phases_listed_yields_no_findings(self, present: list[int]) -> None:
        """Feature: phase-1-learning-docs, Property 1: Phase Sub-Libraries reflect on-disk."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_phase_dirs(root, present)
            _write_phase_sublib_index(root, present)
            assert check_ldc018(root) == []

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(state=_ldc018_violation())
    def test_unlisted_present_phase_is_flagged(self, state: tuple[list[int], list[int]]) -> None:
        """Feature: phase-1-learning-docs, Property 1: Phase Sub-Libraries reflect on-disk."""
        present, listed = state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_phase_dirs(root, present)
            _write_phase_sublib_index(root, listed)
            findings = check_ldc018(root)
            assert findings, f"expected an LDC018 finding when {present!r} listed as {listed!r}"
            assert all(finding.rule_id == "LDC018" for finding in findings)


# ---- Property 3: External Sources reflects every present app README (check_ldc019)

_APP_POOL = ("api", "web", "admin")
_APP_SUBSET = st.lists(st.sampled_from(_APP_POOL), min_size=0, max_size=3, unique=True)


def _required_links(app_names: list[str]) -> list[str]:
    """The relative links the Library_Index must carry for ``app_names`` plus the dirs."""
    links = [f"../../apps/{name}/README.md" for name in sorted(app_names)]
    links += ["../../.kiro/steering/", "../adr/"]
    return links


def _write_ldc019_root(
    root: Path, app_names: list[str], external_links: list[str], nongoals_links: list[str]
) -> None:
    """Build a synthetic repo with app READMEs, steering, adr, and a Library_Index."""
    for name in app_names:
        readme = root / "apps" / name / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(f"# {name}\n\nbody\n", encoding="utf-8")

    steering = root / ".kiro" / "steering"
    steering.mkdir(parents=True, exist_ok=True)
    steering.joinpath("product.md").write_text("# Product\n\nbody\n", encoding="utf-8")

    adr = root / "docs" / "adr"
    adr.mkdir(parents=True, exist_ok=True)
    adr.joinpath("0001-example.md").write_text("# ADR\n\nbody\n", encoding="utf-8")

    learning = root / "docs" / "learning"
    learning.mkdir(parents=True, exist_ok=True)
    external = "\n".join(f"- [{target}]({target})" for target in external_links)
    nongoals = "\n".join(f"- [{target}]({target})" for target in nongoals_links)
    learning.joinpath("README.md").write_text(
        "# MatchLayer Learning Docs\n\n"
        "## External Sources\n\n" + external + "\n\n"
        "## Non-goals\n\n" + nongoals + "\n",
        encoding="utf-8",
    )


@st.composite
def _ldc019_violation(draw: st.DrawFn) -> tuple[list[str], list[str], list[str]]:
    """Sources present, but the Non-goals section omits one required reference."""
    apps = draw(_APP_SUBSET)
    required = _required_links(apps)
    drop_idx = draw(st.integers(min_value=0, max_value=len(required) - 1))
    nongoals = [link for index, link in enumerate(required) if index != drop_idx]
    return apps, required, nongoals


class TestProperty3ExternalSourcesListsAppReadmes:
    r"""Property 3: ``check_ldc019`` is silent when both index sections list every source.

    Rule-backed property test. Referencing every present ``apps/*/README.md``
    plus ``.kiro/steering/`` and ``docs/adr/`` in both the ``External Sources``
    and ``Non-goals`` sections yields no findings; dropping a required reference
    from a section yields an ``LDC019`` finding.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(apps=_APP_SUBSET)
    def test_all_sources_referenced_yields_no_findings(self, apps: list[str]) -> None:
        """Feature: phase-1-learning-docs, Property 3: External Sources lists app READMEs."""
        required = _required_links(apps)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_ldc019_root(root, apps, required, required)
            assert check_ldc019(root) == []

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(state=_ldc019_violation())
    def test_missing_source_reference_is_flagged(
        self, state: tuple[list[str], list[str], list[str]]
    ) -> None:
        """Feature: phase-1-learning-docs, Property 3: External Sources lists app READMEs."""
        apps, external, nongoals = state
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_ldc019_root(root, apps, external, nongoals)
            findings = check_ldc019(root)
            assert findings, f"expected an LDC019 finding when Non-goals omits a source: {apps!r}"
            assert all(finding.rule_id == "LDC019" for finding in findings)


# ---- Properties 7 & 8: Topic_Doc listing in the Phase_1_Index (check_ldc017)


def _write_ldc017_state(
    root: Path,
    pairs: list[tuple[str, str]],
    listing: list[tuple[str, str]],
) -> None:
    """Write a phase-1 sub-library plus a Phase_1_Index listing ``listing``.

    ``pairs`` are ``(filename, h1_title)`` tuples; each becomes a Topic_Doc file
    whose H1 is ``h1_title``. ``listing`` are ``(target, link_text)`` tuples
    rendered as bullet links under the single ``Foundation and tooling``
    thematic section — the section ``check_ldc017`` scans.
    """
    phase_1 = root / "docs" / "learning" / "phase-1"
    phase_1.mkdir(parents=True, exist_ok=True)
    for filename, title in pairs:
        phase_1.joinpath(filename).write_text(f"# {title}\n\nbody\n", encoding="utf-8")
    entries = "\n".join(f"- [{text}]({target})" for target, text in listing)
    phase_1.joinpath("README.md").write_text(
        "# MatchLayer Phase 1\n\n## Foundation and tooling\n\n" + entries + "\n",
        encoding="utf-8",
    )


@st.composite
def _ldc017_pairs(draw: st.DrawFn) -> list[tuple[str, str]]:
    """1-4 ``(filename, title)`` pairs with unique filenames and valid H1 titles."""
    filenames = draw(st.lists(_conforming_filenames(), min_size=1, max_size=4, unique=True))
    pairs: list[tuple[str, str]] = []
    for filename in filenames:
        title = draw(
            st.text(
                alphabet=_TITLE_ALPHABET,
                min_size=MIN_H1_TITLE_LENGTH,
                max_size=20,
            )
        )
        pairs.append((filename, title))
    return pairs


class TestProperty7EveryTopicDocListedOnce:
    r"""Property 7: ``check_ldc017`` is silent when each Topic_Doc is listed exactly once.

    Rule-backed property test. Listing every Topic_Doc under exactly one
    thematic section with link text equal to its H1 title yields no findings;
    omitting a Topic_Doc (listed zero times) or listing it twice yields an
    ``LDC017`` finding.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(pairs=_ldc017_pairs())
    def test_each_doc_listed_once_yields_no_findings(self, pairs: list[tuple[str, str]]) -> None:
        """Feature: phase-1-learning-docs, Property 7: Every Topic_Doc listed exactly once."""
        listing = [(filename, title) for filename, title in pairs]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_ldc017_state(root, pairs, listing)
            assert check_ldc017(root) == []

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(pairs=_ldc017_pairs(), data=st.data())
    def test_unlisted_or_duplicated_doc_is_flagged(
        self, pairs: list[tuple[str, str]], data: st.DataObject
    ) -> None:
        """Feature: phase-1-learning-docs, Property 7: Every Topic_Doc listed exactly once."""
        listing = [(filename, title) for filename, title in pairs]
        target_index = data.draw(st.integers(min_value=0, max_value=len(pairs) - 1))
        if data.draw(st.booleans()):
            del listing[target_index]  # the doc is now listed zero times
        else:
            listing.append(listing[target_index])  # the doc is now listed twice
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_ldc017_state(root, pairs, listing)
            findings = check_ldc017(root)
            assert findings, f"expected an LDC017 finding for listing {listing!r}"
            assert all(finding.rule_id == "LDC017" for finding in findings)


class TestProperty8TopicDocEntryMarkupIntegrity:
    r"""Property 8: ``check_ldc017`` checks each entry's link text equals the H1 title.

    Rule-backed property test. The machine-checkable halves of Req 2.6/2.7 — the
    link target equals the filename and the link text equals the Topic_Doc's H1
    title — are enforced by ``check_ldc017``. An entry whose link text matches
    the title is accepted; an entry whose link text differs from the title is
    flagged. (The 8-30-word summary half of Req 2.7 is a reviewer concern.)
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(pairs=_ldc017_pairs())
    def test_matching_link_text_yields_no_findings(self, pairs: list[tuple[str, str]]) -> None:
        """Feature: phase-1-learning-docs, Property 8: Topic_Doc entry markup integrity."""
        listing = [(filename, title) for filename, title in pairs]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_ldc017_state(root, pairs, listing)
            assert check_ldc017(root) == []

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(pairs=_ldc017_pairs(), data=st.data())
    def test_mismatched_link_text_is_flagged(
        self, pairs: list[tuple[str, str]], data: st.DataObject
    ) -> None:
        """Feature: phase-1-learning-docs, Property 8: Topic_Doc entry markup integrity."""
        listing = [(filename, title) for filename, title in pairs]
        target_index = data.draw(st.integers(min_value=0, max_value=len(pairs) - 1))
        filename, title = listing[target_index]
        listing[target_index] = (filename, title + "X")  # link text no longer equals the H1 title
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_ldc017_state(root, pairs, listing)
            findings = check_ldc017(root)
            assert findings, f"expected an LDC017 finding for mismatched link text {listing!r}"
            assert all(finding.rule_id == "LDC017" for finding in findings)


# ---------------------------------------------------------------------------
# Structural / example tests for properties with no dedicated rule function
# ---------------------------------------------------------------------------
#
# Properties 2, 4, 5, 6, 9, 34, and 35 validate Library_Index / Phase_1_Index
# structure or filesystem invariants for which ``learning_docs_check.py`` ships
# no dedicated rule. Following the Property 26 precedent, each is exercised as a
# STRUCTURAL test: a self-contained predicate over either the real committed
# library or a synthetic state. Where a predicate is asserted against the real
# committed files it doubles as a regression check on the authored docs.


def _section_body(doc: TopicDoc, heading: str) -> str:
    """Return the body of ``doc``'s first H2 section titled ``heading`` (or '')."""
    section = next((s for s in doc.sections if s.heading == heading), None)
    return section.body if section is not None else ""


def _word_count(text: str) -> int:
    """Count whitespace-delimited word tokens in ``text``."""
    return len(re.findall(r"\S+", text))


# ---- Property 2: Phase-1 non-interference under sibling addition (invariant)
#
# No rule enforces Req 1.6 — it is a filesystem invariant about how a new phase
# sub-library is added. This is therefore a library-invariant structural test
# (not a rule test): it snapshots a synthetic ``docs/learning/phase-1/`` tree,
# adds a sibling ``docs/learning/phase-<M>/`` directory, and asserts every
# phase-1 file is byte-identical before and after.

_PHASE_1_FILE_NAMES = st.lists(_conforming_filenames(), min_size=1, max_size=4, unique=True)


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Map each file under ``directory`` to its bytes, keyed by relative POSIX path."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class TestProperty2PhaseOneNonInterference:
    r"""Property 2 (library invariant): adding a sibling phase leaves phase-1 byte-identical.

    No rule backs Req 1.6; this asserts the filesystem invariant directly.
    Snapshot a synthetic ``docs/learning/phase-1/`` tree, add a sibling
    ``docs/learning/phase-<M>/`` directory (2 <= M <= 7), and assert every file
    under ``phase-1/`` is unchanged.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        filenames=_PHASE_1_FILE_NAMES,
        contents=st.lists(st.text(max_size=80), min_size=1, max_size=4),
        sibling=st.integers(min_value=2, max_value=7),
    )
    def test_phase_1_files_unchanged_after_sibling_added(
        self, filenames: list[str], contents: list[str], sibling: int
    ) -> None:
        """Feature: phase-1-learning-docs, Property 2: Phase-1 non-interference."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            phase_1 = root / "docs" / "learning" / "phase-1"
            phase_1.mkdir(parents=True, exist_ok=True)
            for index, filename in enumerate(filenames):
                body = contents[index % len(contents)]
                phase_1.joinpath(filename).write_text(body, encoding="utf-8")

            before = _snapshot(phase_1)

            sibling_dir = root / "docs" / "learning" / f"phase-{sibling}"
            sibling_dir.mkdir(parents=True, exist_ok=True)
            sibling_dir.joinpath("README.md").write_text(
                f"# Phase {sibling}\n\nbody\n", encoding="utf-8"
            )

            after = _snapshot(phase_1)
            assert after == before


# ---- Property 4: no 50-word duplication from upstream sources (structural)
#
# No rule enforces Req 1.8 (the design defers it to reviewer judgment). This
# structural test exercises the predicate directly: (a) the real committed
# Topic_Docs share no 50-word window with any upstream source, and (b) a
# synthetic Topic_Doc that copies a 50-word window verbatim IS detected.

_DUP_WINDOW = 50


def _strip_fenced(text: str) -> str:
    """Drop fenced code-block lines so only prose participates in the dup scan."""
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if re.match(r"^[ \t]*`{3,}", line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _word_windows(text: str, size: int = _DUP_WINDOW) -> set[tuple[str, ...]]:
    """Return the set of contiguous ``size``-word windows of ``text``."""
    words = text.split()
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def _upstream_windows(root: Path) -> set[tuple[str, ...]]:
    """All 50-word windows across ``.kiro/steering/``, ``docs/adr/``, ``apps/*/README.md``."""
    sources: list[Path] = []
    steering = root / ".kiro" / "steering"
    adr = root / "docs" / "adr"
    if steering.is_dir():
        sources += list(steering.rglob("*.md"))
    if adr.is_dir():
        sources += list(adr.rglob("*.md"))
    apps = root / "apps"
    if apps.is_dir():
        sources += [p / "README.md" for p in apps.iterdir() if (p / "README.md").is_file()]
    windows: set[tuple[str, ...]] = set()
    for source in sources:
        windows |= _word_windows(_strip_fenced(source.read_text(encoding="utf-8")))
    return windows


class TestProperty4NoFiftyWordDuplication:
    r"""Property 4 (structural): no Topic_Doc reproduces a 50-word window from upstream.

    No rule backs Req 1.8. This asserts the predicate against the real committed
    library (no Topic_Doc shares a 50-word prose window with any steering doc,
    ADR, or app README) and confirms the predicate detects a deliberate copy.
    """

    def test_real_topic_docs_have_no_50_word_overlap(self) -> None:
        """Feature: phase-1-learning-docs, Property 4: No 50-word duplication."""
        root = _repo_root()
        upstream = _upstream_windows(root)
        phase_1 = root / "docs" / "learning" / "phase-1"
        offenders: list[str] = []
        for doc in sorted(phase_1.glob("*.md")):
            if doc.name == "README.md":
                continue
            prose = _strip_fenced(doc.read_text(encoding="utf-8"))
            if _word_windows(prose) & upstream:
                offenders.append(doc.name)
        assert offenders == [], f"Topic_Docs reproduce a 50-word upstream window: {offenders}"

    def test_predicate_detects_a_deliberate_copy(self) -> None:
        """Feature: phase-1-learning-docs, Property 4: No 50-word duplication."""
        root = _repo_root()
        upstream = _upstream_windows(root)
        assert upstream, "expected upstream sources to yield at least one 50-word window"
        # Reconstruct a 50-word window of real upstream prose and confirm it is caught.
        sample_window = next(iter(upstream))
        copied_prose = " ".join(sample_window)
        assert _word_windows(copied_prose) & upstream


# ---- Property 5: Phase_1_Index introduction word count and references (structural)
#
# No rule enforces Req 2.3. This structural test checks the predicate against
# the real Phase_1_Index and confirms it rejects synthetic violations.

_REQUIRED_SPEC_SUBSTRINGS = (
    ".kiro/specs/phase-1-foundation/",
    ".kiro/specs/phase-1-auth/",
    ".kiro/specs/phase-1-matching/",
)


def _intro_is_compliant(intro_body: str) -> bool:
    """Return True when the intro is 40-200 words and names all three spec paths."""
    words = _word_count(intro_body)
    if not (40 <= words <= 200):
        return False
    return all(substring in intro_body for substring in _REQUIRED_SPEC_SUBSTRINGS)


class TestProperty5PhaseOneIndexIntroduction:
    r"""Property 5 (structural): the Phase_1_Index intro is 40-200 words and names all 3 specs.

    No rule backs Req 2.3. The predicate is asserted against the real committed
    Phase_1_Index and shown to reject synthetic violations (too few words, or a
    missing spec-path substring).
    """

    def test_real_phase_1_index_introduction_is_compliant(self) -> None:
        """Feature: phase-1-learning-docs, Property 5: Phase_1_Index introduction."""
        index = _repo_root() / "docs" / "learning" / "phase-1" / "README.md"
        intro_body = _section_body(parse_topic_doc(index), "Introduction")
        assert _intro_is_compliant(intro_body)

    @settings(max_examples=100)
    @given(filler_words=st.integers(min_value=0, max_value=36))
    def test_too_short_intro_is_rejected(self, filler_words: int) -> None:
        """Feature: phase-1-learning-docs, Property 5: Phase_1_Index introduction."""
        # The three spec substrings count as three words, so capping filler at 36
        # keeps the total strictly below the 40-word floor while naming all specs.
        body = " ".join(_REQUIRED_SPEC_SUBSTRINGS) + " " + ("word " * filler_words)
        assert _word_count(body) < 40
        assert _intro_is_compliant(body) is False

    @settings(max_examples=100)
    @given(missing=st.sampled_from(_REQUIRED_SPEC_SUBSTRINGS))
    def test_intro_missing_a_spec_path_is_rejected(self, missing: str) -> None:
        """Feature: phase-1-learning-docs, Property 5: Phase_1_Index introduction."""
        present = [s for s in _REQUIRED_SPEC_SUBSTRINGS if s != missing]
        body = " ".join(present) + " " + ("word " * 60)
        assert _intro_is_compliant(body) is False


# ---- Property 6: Phase_1_Index thematic-section sequence equality (structural)
#
# No rule enforces Req 2.4 directly (``check_ldc017`` scans the sections but does
# not assert their order). This structural test checks the H2-sequence predicate
# against the real Phase_1_Index and against synthetic reorderings.

_CANONICAL_THEMATIC = list(PHASE_1_THEMATIC_SECTIONS)


def _thematic_sequence(doc: TopicDoc) -> list[str]:
    """Return the H2 headings of ``doc`` that are canonical thematic sections, in order."""
    canonical = set(_CANONICAL_THEMATIC)
    return [section.heading for section in doc.sections if section.heading in canonical]


class TestProperty6ThematicSectionSequence:
    r"""Property 6 (structural): the Phase_1_Index thematic H2 sequence equals the canon.

    No rule backs Req 2.4. The predicate is asserted against the real committed
    Phase_1_Index and shown to reject a synthetic index whose thematic sections
    are reordered.
    """

    def test_real_phase_1_index_sequence_matches_canon(self) -> None:
        """Feature: phase-1-learning-docs, Property 6: Phase_1_Index thematic sequence."""
        index = _repo_root() / "docs" / "learning" / "phase-1" / "README.md"
        assert _thematic_sequence(parse_topic_doc(index)) == _CANONICAL_THEMATIC

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(data=st.data())
    def test_reordered_sequence_is_rejected(self, data: st.DataObject) -> None:
        """Feature: phase-1-learning-docs, Property 6: Phase_1_Index thematic sequence."""
        swap = data.draw(st.integers(min_value=0, max_value=len(_CANONICAL_THEMATIC) - 2))
        reordered = list(_CANONICAL_THEMATIC)
        reordered[swap], reordered[swap + 1] = reordered[swap + 1], reordered[swap]
        markdown = "# Phase 1\n\n" + "\n\n".join(f"## {heading}\n\nbody" for heading in reordered)
        with tempfile.TemporaryDirectory() as td:
            index = Path(td) / "README.md"
            index.write_text(markdown + "\n", encoding="utf-8")
            assert _thematic_sequence(parse_topic_doc(index)) != _CANONICAL_THEMATIC


# ---- Property 9: Recommended reading order endpoints (structural)
#
# No rule enforces Req 2.8. The real Phase_1_Index leaves the reading order
# empty until task 18.x, so this predicate is exercised against a SYNTHETIC
# index: the numbered list must cover exactly the present Topic_Doc filenames,
# with its first entry in ``Foundation and tooling`` and its last in
# ``Hosting and deploy``.

_ORDERED_ENTRY_RE = re.compile(r"^\s*\d+\.\s*\[[^\]]*\]\(([^)]+)\)")


def _reading_order_targets(doc: TopicDoc) -> list[str]:
    """Return the ordered-list link targets under ``Recommended reading order``, in order."""
    body = _section_body(doc, "Recommended reading order")
    targets: list[str] = []
    for line in body.split("\n"):
        match = _ORDERED_ENTRY_RE.match(line)
        if match:
            targets.append(match.group(1).partition("#")[0])
    return targets


def _files_in_section(doc: TopicDoc, heading: str) -> set[str]:
    """Return the set of link targets listed under thematic section ``heading``."""
    body = _section_body(doc, heading)
    return {match.group(1).partition("#")[0] for match in _LINK_TARGET_RE.finditer(body)}


_LINK_TARGET_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _reading_order_ok(root: Path) -> bool:
    """Return True when the reading order is exhaustive with the right endpoints."""
    phase_1 = root / "docs" / "learning" / "phase-1"
    present = {p.name for p in phase_1.glob("*.md") if p.name != "README.md"}
    doc = parse_topic_doc(phase_1 / "README.md")
    order = _reading_order_targets(doc)
    if set(order) != present or len(order) != len(present) or not order:
        return False
    foundation = _files_in_section(doc, "Foundation and tooling")
    hosting = _files_in_section(doc, "Hosting and deploy")
    return order[0] in foundation and order[-1] in hosting


def _write_reading_order_state(
    root: Path,
    foundation_files: list[str],
    hosting_files: list[str],
    order: list[str],
) -> None:
    """Write a synthetic Phase_1_Index with the two endpoint sections and a reading order."""
    phase_1 = root / "docs" / "learning" / "phase-1"
    phase_1.mkdir(parents=True, exist_ok=True)
    for filename in foundation_files + hosting_files:
        phase_1.joinpath(filename).write_text("# Topic\n\nbody\n", encoding="utf-8")
    foundation_block = "\n".join(f"- [Topic]({name})" for name in foundation_files)
    hosting_block = "\n".join(f"- [Topic]({name})" for name in hosting_files)
    order_block = "\n".join(f"{index}. [Topic]({name})" for index, name in enumerate(order, 1))
    phase_1.joinpath("README.md").write_text(
        "# MatchLayer Phase 1\n\n"
        "## Foundation and tooling\n\n" + foundation_block + "\n\n"
        "## Hosting and deploy\n\n" + hosting_block + "\n\n"
        "## Recommended reading order\n\n" + order_block + "\n",
        encoding="utf-8",
    )


@st.composite
def _reading_order_files(draw: st.DrawFn) -> tuple[list[str], list[str]]:
    """Disjoint non-empty foundation and hosting filename lists."""
    names = draw(st.lists(_conforming_filenames(), min_size=2, max_size=6, unique=True))
    split = draw(st.integers(min_value=1, max_value=len(names) - 1))
    return names[:split], names[split:]


class TestProperty9RecommendedReadingOrder:
    r"""Property 9 (structural): the reading order is exhaustive with the right endpoints.

    No rule backs Req 2.8 and the real index defers its reading order, so the
    predicate is exercised against a synthetic Phase_1_Index: a list covering
    exactly the present files with a foundation-first, hosting-last endpoint is
    accepted, while dropping a file or using a wrong endpoint is rejected.
    """

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(files=_reading_order_files())
    def test_exhaustive_order_with_correct_endpoints_is_accepted(
        self, files: tuple[list[str], list[str]]
    ) -> None:
        """Feature: phase-1-learning-docs, Property 9: Recommended reading order endpoints."""
        foundation, hosting = files
        order = [foundation[0], *foundation[1:], *hosting[:-1], hosting[-1]]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_reading_order_state(root, foundation, hosting, order)
            assert _reading_order_ok(root) is True

    @settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(files=_reading_order_files(), data=st.data())
    def test_non_exhaustive_or_wrong_endpoint_is_rejected(
        self, files: tuple[list[str], list[str]], data: st.DataObject
    ) -> None:
        """Feature: phase-1-learning-docs, Property 9: Recommended reading order endpoints."""
        foundation, hosting = files
        complete = [*foundation, *hosting]
        mode = data.draw(st.sampled_from(("drop_file", "wrong_first", "wrong_last")))
        if mode == "drop_file":
            order = complete[:-1]  # omits the final hosting file
        elif mode == "wrong_first":
            order = [hosting[0], *[f for f in complete if f != hosting[0]]]  # starts in hosting
        else:
            # ends in a foundation file rather than a hosting one
            order = [f for f in complete if f != foundation[0]] + [foundation[0]]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_reading_order_state(root, foundation, hosting, order)
            assert _reading_order_ok(root) is False


# ---- Property 34: Library_Index Non-goals has at least four bullets (structural)
#
# No rule enforces Req 10.1. The predicate is asserted against the real
# Library_Index and shown to reject a synthetic section with fewer than four
# bullets.


def _nongoals_bullet_count(library_index_text: str) -> int:
    """Count Markdown bullet items in the Non-goals / What this is not section."""
    lines = library_index_text.split("\n")
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ("## Non-goals", "## What this is not"):
            start = index + 1
        elif start is not None and line.startswith("## "):
            end = index
            break
    if start is None:
        return 0
    return sum(1 for line in lines[start:end] if line.lstrip().startswith("- "))


class TestProperty34LibraryIndexNonGoalsBullets:
    r"""Property 34 (structural): the Library_Index Non-goals section has >= 4 bullets.

    No rule backs Req 10.1. The bullet-count predicate is asserted against the
    real committed Library_Index and shown to reject a synthetic section that
    carries fewer than four bullets.
    """

    def test_real_library_index_has_at_least_four_bullets(self) -> None:
        """Feature: phase-1-learning-docs, Property 34: Library_Index Non-goals bullets."""
        library_index = _repo_root() / "docs" / "learning" / "README.md"
        text = library_index.read_text(encoding="utf-8")
        assert _nongoals_bullet_count(text) >= 4

    @settings(max_examples=100)
    @given(
        heading=st.sampled_from(("## Non-goals", "## What this is not")),
        bullets=st.integers(min_value=0, max_value=3),
    )
    def test_fewer_than_four_bullets_is_rejected(self, heading: str, bullets: int) -> None:
        """Feature: phase-1-learning-docs, Property 34: Library_Index Non-goals bullets."""
        body = "\n".join(f"- bullet {index}" for index in range(bullets))
        text = f"# Library\n\n{heading}\n\n{body}\n"
        assert _nongoals_bullet_count(text) < 4


# ---- Property 35: Phase_1_Sub_Library does not over-discuss future phases (structural)
#
# No rule enforces Req 10.6. The predicate counts sentences mentioning a future
# phase token (case-sensitive ``Phase 2``..``Phase 7``); at most two are
# permitted per Topic_Doc. Asserted against the real Topic_Docs and shown to
# reject a synthetic doc with three such sentences.

_FUTURE_PHASE_RE = re.compile(r"Phase [2-7]")


def _future_phase_sentence_count(text: str) -> int:
    """Count sentences in ``text`` that mention a future-phase token (case-sensitive)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return sum(1 for sentence in sentences if _FUTURE_PHASE_RE.search(sentence))


class TestProperty35NoOverDiscussionOfFuturePhases:
    r"""Property 35 (structural): each Topic_Doc mentions future phases in <= 2 sentences.

    No rule backs Req 10.6. The sentence-count predicate is asserted against
    every real committed Topic_Doc and shown to reject a synthetic doc with three
    future-phase sentences. The "preserves a future option, not implementation
    guidance" half remains a reviewer responsibility.
    """

    def test_real_topic_docs_stay_within_two_future_phase_sentences(self) -> None:
        """Feature: phase-1-learning-docs, Property 35: No over-discussion of future phases."""
        phase_1 = _repo_root() / "docs" / "learning" / "phase-1"
        offenders: list[str] = []
        for doc in sorted(phase_1.glob("*.md")):
            if doc.name == "README.md":
                continue
            if _future_phase_sentence_count(doc.read_text(encoding="utf-8")) > 2:
                offenders.append(doc.name)
        assert offenders == [], f"Topic_Docs over-discuss future phases: {offenders}"

    @settings(max_examples=100)
    @given(phases=st.lists(st.integers(min_value=2, max_value=7), min_size=3, max_size=6))
    def test_three_future_phase_sentences_are_rejected(self, phases: list[int]) -> None:
        """Feature: phase-1-learning-docs, Property 35: No over-discussion of future phases."""
        sentences = " ".join(f"Phase {number} will add capability." for number in phases)
        assert _future_phase_sentence_count(sentences) > 2
