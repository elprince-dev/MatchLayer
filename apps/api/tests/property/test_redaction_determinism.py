"""Feature: phase-3-llm-layer — Property 3.

# Feature: phase-3-llm-layer, Property 3: Redaction determinism

Property 3: Redaction determinism.

    *For any* input text, two invocations of the PII_Redactor under the
    same version produce byte-identical output.

**Validates: Requirements 3.4**

``services/llm/redaction.redact`` is documented as deterministic and pure —
identical arguments must yield byte-identical output under the same
``REDACTOR_VERSION`` so Phase 5 evaluation replay can reproduce the exact
redacted input from an LLM_Invocation_Log entry.

Two input strategies exercise the property:

* **Fully arbitrary text** — Unicode text with no structural constraint, so
  the property holds even on inputs that never trigger a detector.
* **Resume-shaped documents** — synthetic documents assembled from header
  lines (capitalized name candidates, emails, phone numbers), recognized
  section headings (including employment-history headings that engage the
  Redaction_Exception segmentation), and body lines that re-embed the same
  PII values. This drives the detection, overlap-resolution, exemption, and
  placeholder-indexing code paths where nondeterminism (e.g. iteration over
  an unordered container) would realistically hide.

Each case invokes the redactor twice with identical arguments and asserts
the outputs — redacted text and reported version — are identical, for every
``kind`` (``resume``, ``job_description``, ``bullet``).
"""

from __future__ import annotations

from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.services.llm.redaction import (
    EMPLOYMENT_SECTION_HEADINGS,
    SECTION_HEADINGS,
    redact,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_KINDS: tuple[Literal["resume", "job_description", "bullet"], ...] = (
    "resume",
    "job_description",
    "bullet",
)

_kind = st.sampled_from(_KINDS)

# Fully arbitrary input text — the property quantifies over *any* input.
_arbitrary_text = st.text(max_size=400)

_FIRST_NAMES = ("Jordan", "Alex", "Sam", "Casey", "Riley", "Morgan")
_LAST_NAMES = ("Rivera", "Chen", "Okafor", "Novak", "Silva", "Dupont")

_name = st.builds(
    lambda first, last: f"{first} {last}",
    st.sampled_from(_FIRST_NAMES),
    st.sampled_from(_LAST_NAMES),
)

_email = st.builds(
    lambda local, domain: f"{local}@{domain}.example.com",
    st.text(alphabet="abcdefghij0123456789._", min_size=1, max_size=10).filter(
        lambda s: s.strip("._") == s and s != ""
    ),
    st.sampled_from(("mail", "corp", "dev", "hire")),
)

_phone = st.builds(
    lambda area, mid, last: f"({area}) {mid}-{last}",
    st.integers(min_value=200, max_value=999),
    st.integers(min_value=200, max_value=999),
    st.integers(min_value=1000, max_value=9999),
)

_body_words = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz ,.",
    max_size=40,
)


@st.composite
def _resume_documents(draw: st.DrawFn) -> str:
    """Assemble a synthetic resume-shaped document that engages the detectors.

    Header region: a name candidate plus contact lines carrying emails and
    phone numbers. Sections: drawn from the committed heading lexicon (some
    employment-history, some not), with body lines that may repeat the same
    PII values — covering placeholder index reuse and the exemption regions.
    """
    name = draw(_name)
    emails = draw(st.lists(_email, min_size=0, max_size=2))
    phones = draw(st.lists(_phone, min_size=0, max_size=2))

    lines: list[str] = [name]
    for value in [*emails, *phones]:
        lines.append(f"Contact: {value}")

    section_headings = draw(
        st.lists(
            st.sampled_from(sorted(SECTION_HEADINGS | EMPLOYMENT_SECTION_HEADINGS)),
            min_size=0,
            max_size=3,
        )
    )
    reusable_values = [name, *emails, *phones]
    for heading in section_headings:
        lines.append(heading.title())
        body = draw(_body_words)
        lines.append(body)
        # Optionally re-embed a PII value inside the section body — inside an
        # employment-history section this lands in an exempt region.
        embed_pii = draw(st.booleans())
        if reusable_values and embed_pii:
            lines.append(f"Worked with {draw(st.sampled_from(reusable_values))} on {body}")

    return "\n".join(lines)


_input_text = st.one_of(_arbitrary_text, _resume_documents())

# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(text=_input_text, kind=_kind)
def test_redaction_is_deterministic(
    text: str,
    kind: Literal["resume", "job_description", "bullet"],
) -> None:
    """Determinism (Requirement 3.4): two invocations of the PII_Redactor with
    identical arguments produce byte-identical redacted output under the same
    redactor version."""
    first = redact(text, kind=kind)
    second = redact(text, kind=kind)

    assert first.text == second.text
    assert first.redactor_version == second.redactor_version
