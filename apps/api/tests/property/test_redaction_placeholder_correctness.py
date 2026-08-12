"""Feature: phase-3-llm-layer — Property 1.

# Feature: phase-3-llm-layer, Property 1: Redaction placeholder correctness

Property 1: Redaction placeholder correctness.

    *For any* synthetic input text containing planted email addresses,
    phone numbers, and a header-region full name occurring anywhere
    outside employment-history sections, the PII_Redactor output contains
    no planted value, and every occurrence is replaced by an indexed
    typed placeholder from ``[EMAIL_n]``, ``[PHONE_n]``, ``[NAME_n]``
    such that indices start at 1 per type, are assigned in
    first-occurrence order, every occurrence of the same value receives
    the same placeholder, and distinct values of the same type receive
    distinct indices.

**Validates: Requirements 3.1, 3.2, 3.5**

The generator builds a synthetic resume as an explicit interleaving of
inert filler and planted PII tokens, so the exact redacted output can be
computed independently while walking the plant order:

* **Filler** is lowercase vowel-free words — it can never match the email
  or phone regexes (no ``@``, no digits), never forms a capitalized name
  run, and never normalizes to a recognized section heading (every
  heading in the committed lexicon contains a vowel), so the document has
  no employment-history sections and the whole text is redactable.
* **Names** are runs of 2-4 unique capitalized vowel-free tokens (never
  exclusion-lexicon words), each planted on its own line inside the
  header region so the committed heuristic detects them, with optional
  extra occurrences anywhere in the body (Requirement 3.2:
  every-occurrence redaction).
* **Emails and phones** are letter-only / fixed-format values planted at
  1-3 occurrences each, anywhere in the document.
* Consecutive PII tokens are always separated by filler, so detections
  can never merge or overlap and each planted occurrence maps to exactly
  one placeholder.

Asserting ``redact(...).text == expected`` proves simultaneously: no
planted value survives, every occurrence is replaced by its type's
placeholder, indices start at 1 per type in first-occurrence order,
repeated values share one index, and distinct values of a type get
distinct indices (Requirements 3.1, 3.5). A derived check re-reads the
output and confirms each type's placeholder indices first appear in
strictly increasing order from 1.
"""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from matchlayer_api.services.llm.redaction import redact

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Vowel-free lowercase alphabet: words drawn from it cannot be section
# headings or name-exclusion words (all of which contain a vowel), cannot
# match the email/phone regexes, and cannot form capitalized name runs.
_CONSONANTS = "bcdfghjklmnpqrstvwxz"

_filler_word = st.text(alphabet=_CONSONANTS, min_size=2, max_size=8)

# A name token: capitalized, letters only, vowel-free (so never in the
# exclusion lexicon), 2-8 characters.
_name_token = st.builds(
    lambda first, rest: first.upper() + rest,
    st.sampled_from(_CONSONANTS),
    st.text(alphabet=_CONSONANTS, min_size=1, max_size=7),
)

# Letter-only email values: no digits, so the phone regex can never fire
# inside them; distinctness enforced at the list level.
_email_value = st.builds(
    lambda local, domain: f"{local}@{domain}.com",
    st.text(alphabet=_CONSONANTS, min_size=1, max_size=8),
    st.text(alphabet=_CONSONANTS, min_size=1, max_size=8),
)

# Fixed-format US-style phone values: the committed phone regex matches
# each exactly and, at equal length, no value can be a substring of another.
_phone_value = st.builds(
    lambda digits: f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}",
    st.text(alphabet="0123456789", min_size=10, max_size=10),
)

_Atom = tuple[str, str]  # (label, text); label is "LIT" | "EMAIL" | "PHONE" | "NAME"


@st.composite
def _redaction_documents(draw: st.DrawFn) -> tuple[str, str, dict[str, list[str]]]:
    """Build ``(input text, expected redacted output, planted values by type)``.

    The document is a header region (one line per planted name, so the
    committed heuristic detects each) followed by body lines carrying the
    remaining planted occurrences, PII tokens always filler-separated.
    """
    # 1-2 distinct names of 2-4 tokens each; tokens globally unique so no
    # name can occur (word-bounded) inside another.
    name_sizes = draw(st.lists(st.integers(2, 4), min_size=1, max_size=2))
    tokens = draw(
        st.lists(
            _name_token,
            min_size=sum(name_sizes),
            max_size=sum(name_sizes),
            unique=True,
        )
    )
    names: list[str] = []
    cursor = 0
    for size in name_sizes:
        names.append(" ".join(tokens[cursor : cursor + size]))
        cursor += size

    emails = draw(st.lists(_email_value, min_size=1, max_size=3, unique=True))
    phones = draw(st.lists(_phone_value, min_size=1, max_size=3, unique=True))

    # Every email/phone occurs 1-3 times; each name may recur 0-2 times
    # beyond its mandatory header-region occurrence. Order is shuffled so
    # first-occurrence order is arbitrary across types and values.
    occurrences: list[_Atom] = []
    for email in emails:
        occurrences.extend(("EMAIL", email) for _ in range(draw(st.integers(1, 3))))
    for phone in phones:
        occurrences.extend(("PHONE", phone) for _ in range(draw(st.integers(1, 3))))
    for name in names:
        occurrences.extend(("NAME", name) for _ in range(draw(st.integers(0, 2))))
    occurrences = list(draw(st.permutations(occurrences)))

    # Header region: one line per name (well within the 10-line cap), the
    # name optionally flanked by filler that cannot join the token run.
    lines: list[list[_Atom]] = []
    for name in names:
        atoms: list[_Atom] = []
        if draw(st.booleans()):
            atoms.append(("LIT", draw(_filler_word)))
        atoms.append(("NAME", name))
        if draw(st.booleans()):
            atoms.append(("LIT", draw(_filler_word)))
        lines.append(atoms)

    # Body: chunk the shuffled occurrences into lines of 1-3 PII tokens,
    # with mandatory filler between consecutive tokens so detections can
    # never merge or overlap.
    index = 0
    while index < len(occurrences):
        chunk = occurrences[index : index + draw(st.integers(1, 3))]
        index += len(chunk)
        atoms = []
        if draw(st.booleans()):
            atoms.append(("LIT", draw(_filler_word)))
        for position, atom in enumerate(chunk):
            if position:
                atoms.append(("LIT", draw(_filler_word)))
            atoms.append(atom)
        if draw(st.booleans()):
            atoms.append(("LIT", draw(_filler_word)))
        lines.append(atoms)

    # Render the document and compute the expected output independently:
    # walk atoms in text order, assigning each distinct value of a type
    # the next index starting at 1 (Requirements 3.1, 3.5).
    indices: dict[str, dict[str, int]] = {"EMAIL": {}, "PHONE": {}, "NAME": {}}
    text_lines: list[str] = []
    expected_lines: list[str] = []
    for line_atoms in lines:
        raw_parts: list[str] = []
        expected_parts: list[str] = []
        for label, value in line_atoms:
            raw_parts.append(value)
            if label == "LIT":
                expected_parts.append(value)
            else:
                per_type = indices[label]
                if value not in per_type:
                    per_type[value] = len(per_type) + 1
                expected_parts.append(f"[{label}_{per_type[value]}]")
        text_lines.append(" ".join(raw_parts))
        expected_lines.append(" ".join(expected_parts))

    planted = {"EMAIL": emails, "PHONE": phones, "NAME": names}
    return "\n".join(text_lines), "\n".join(expected_lines), planted


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\[(EMAIL|PHONE|NAME)_(\d+)\]")


@settings(max_examples=100, deadline=None)
@given(case=_redaction_documents())
def test_redaction_placeholder_correctness(
    case: tuple[str, str, dict[str, list[str]]],
) -> None:
    """Placeholder correctness (Requirements 3.1, 3.2, 3.5): the redacted
    output is byte-for-byte the input with every planted occurrence —
    including every non-header occurrence of a header-detected name —
    replaced by its indexed typed placeholder, indices per type starting
    at 1 in first-occurrence order, same value → same placeholder,
    distinct values → distinct indices."""
    text, expected, planted = case

    result = redact(text, kind="resume")

    # The independently computed expectation pins every clause at once.
    assert result.text == expected

    # No planted value survives anywhere in the output.
    for values in planted.values():
        for value in values:
            assert value not in result.text

    # Derived check straight off the output: per type, placeholder indices
    # first appear in strictly increasing order starting at 1.
    first_seen: dict[str, list[int]] = {"EMAIL": [], "PHONE": [], "NAME": []}
    for match in _PLACEHOLDER_RE.finditer(result.text):
        label, index = match.group(1), int(match.group(2))
        if index not in first_seen[label]:
            first_seen[label].append(index)
    for order in first_seen.values():
        assert order == list(range(1, len(order) + 1))
