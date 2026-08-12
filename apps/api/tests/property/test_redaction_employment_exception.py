"""Feature: phase-3-llm-layer — Property 2.

# Feature: phase-3-llm-layer, Property 2: Redaction exception preserves employment-history spans

Property 2: Redaction exception preserves employment-history spans.

    *For any* synthetic resume text with PII-pattern-matching values
    planted inside employment-history sections (per the committed
    boundary rule), the PII_Redactor preserves those spans byte-for-byte
    while still redacting occurrences of the same values outside exempt
    regions.

**Validates: Requirements 3.3**

The strategy assembles a synthetic resume from committed building blocks
(``docs/redaction-policy.md``):

* a contact/header region carrying a heuristic-detectable full name, an
  email, and a phone number — guaranteeing at least one *redactable*
  occurrence of each value outside any exempt region;
* an employment-history section opened by a heading drawn from the
  committed ``EMPLOYMENT_SECTION_HEADINGS`` lexicon (in varied casing and
  decoration forms that the boundary rule's normalization must accept),
  whose body plants the same name/email/phone values plus one extra email
  that occurs *only* inside the section;
* optional non-employment sections before and after it (headings drawn
  from the rest of ``SECTION_HEADINGS``), planting further redactable
  occurrences of the same values.

The employment-history block — heading line through to the start of the
next recognized heading, or end of text — is recorded during construction
exactly as the boundary rule delimits it. The property then asserts:

* the block appears **byte-for-byte** in the redacted output (exempt
  spans are preserved verbatim and stay contiguous);
* for every planted value, the redacted output contains exactly as many
  occurrences as were planted inside the employment block — i.e. every
  occurrence outside the exempt region was redacted, and every occurrence
  inside it survived;
* the header occurrences (always outside any exempt region) really were
  replaced with their typed placeholders.

All filler prose is lowercase and letters-only so generated values (a
capitalized name, ``@example.com`` emails, digit-and-dash phones) can
never collide with surrounding text, keeping ``str.count`` an exact
occurrence census.
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.services.llm.redaction import (
    _NAME_EXCLUSION_WORDS,
    EMPLOYMENT_SECTION_HEADINGS,
    SECTION_HEADINGS,
    redact,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_LOWER = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"

_EMPLOYMENT_HEADINGS = sorted(EMPLOYMENT_SECTION_HEADINGS)
_NON_EMPLOYMENT_HEADINGS = sorted(SECTION_HEADINGS - EMPLOYMENT_SECTION_HEADINGS)

# Decoration/casing forms the committed heading normalization must accept.
_HEADING_FORMS = ("{h}", "{h}:", "## {h}", "{h} ==", "**{h}**")

# A capitalized word token eligible for the committed name heuristic:
# letters only, first letter uppercase, not in the exclusion lexicon.
_name_token = (
    st.text(alphabet=_LOWER, min_size=2, max_size=8)
    .map(str.capitalize)
    .filter(lambda token: token.lower() not in _NAME_EXCLUSION_WORDS)
)


@dataclass(frozen=True)
class _Case:
    """A generated resume plus the exact exempt block and planted values."""

    text: str
    employment_block: str
    name: str
    email: str
    phone: str
    employment_only_email: str


@st.composite
def _heading_line(draw: st.DrawFn, headings: list[str]) -> str:
    heading = draw(st.sampled_from(headings))
    styled = draw(st.sampled_from((heading, heading.title(), heading.upper())))
    return draw(st.sampled_from(_HEADING_FORMS)).format(h=styled)


@st.composite
def _resume_cases(draw: st.DrawFn) -> _Case:
    # --- planted PII values (mutually collision-free by construction) ---
    name = " ".join(draw(st.lists(_name_token, min_size=2, max_size=4)))
    locals_ = draw(
        st.lists(
            st.text(alphabet=_LOWER, min_size=3, max_size=10), min_size=2, max_size=2, unique=True
        )
    )
    email = f"{locals_[0]}@example.com"
    employment_only_email = f"{locals_[1]}@example.com"
    digits = draw(st.text(alphabet=_DIGITS, min_size=10, max_size=10))
    phone = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"

    # Lines planting redactable occurrences in non-exempt regions.
    outside_value_lines = [
        f"reach out via {email} today",
        f"call {phone} anytime",
        f"{name} built the matching platform",
    ]

    # Employment-history body: at least one occurrence of each value, an
    # email that exists only here, plus optional duplicate occurrences.
    base_employment_lines = [
        f"{name} founded the consultancy",
        f"managed the {email} support inbox",
        f"kept the {phone} hotline running",
        f"forward escalations to {employment_only_email} internally",
    ]
    duplicates = draw(st.lists(st.sampled_from(base_employment_lines[:3]), max_size=3))
    employment_body = list(draw(st.permutations(base_employment_lines + duplicates)))

    # --- document assembly ---
    lines: list[str] = [name, f"{email} | {phone}", ""]

    if draw(st.booleans()):  # optional non-exempt section before employment
        pre_body = draw(st.lists(st.sampled_from(outside_value_lines), min_size=1, max_size=3))
        lines += [draw(_heading_line(_NON_EMPLOYMENT_HEADINGS)), *pre_body, ""]

    employment_start = len(lines)
    lines += [draw(_heading_line(_EMPLOYMENT_HEADINGS)), *employment_body]

    post_heading_index: int | None = None
    if draw(st.booleans()):  # optional non-exempt section after employment
        lines.append("")
        post_heading_index = len(lines)
        post_body = draw(st.lists(st.sampled_from(outside_value_lines), min_size=1, max_size=3))
        lines += [draw(_heading_line(_NON_EMPLOYMENT_HEADINGS)), *post_body]

    text = "\n".join(lines) + "\n"

    def offset(line_index: int) -> int:
        return sum(len(line) + 1 for line in lines[:line_index])

    # The exempt block exactly as the committed boundary rule delimits it:
    # heading line start → next recognized heading start, or end of text.
    end = offset(post_heading_index) if post_heading_index is not None else len(text)
    employment_block = text[offset(employment_start) : end]

    return _Case(
        text=text,
        employment_block=employment_block,
        name=name,
        email=email,
        phone=phone,
        employment_only_email=employment_only_email,
    )


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(case=_resume_cases())
def test_employment_history_spans_preserved_and_outside_occurrences_redacted(
    case: _Case,
) -> None:
    """Requirement 3.3: spans inside an employment-history section survive
    byte-for-byte; occurrences of the very same values outside any exempt
    region are still redacted."""
    redacted = redact(case.text, kind="resume").text

    # Exempt spans byte-for-byte: the whole employment-history block —
    # heading through section end — appears verbatim (and contiguous).
    assert case.employment_block in redacted

    # Exact occurrence census: every planted value survives exactly as many
    # times as it was planted inside the exempt block — so every occurrence
    # outside it (header and optional pre/post sections) was redacted.
    for value in (case.name, case.email, case.phone, case.employment_only_email):
        assert redacted.count(value) == case.employment_block.count(value)

    # The header occurrences (always non-exempt) were replaced with their
    # indexed typed placeholders, not merely deleted.
    assert "[NAME_1]" in redacted
    assert "[EMAIL_1]" in redacted
    assert "[PHONE_1]" in redacted
