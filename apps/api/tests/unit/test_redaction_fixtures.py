"""Exact-equality tests over the committed synthetic redaction fixtures (task 5.2).

Each JSON file under ``tests/fixtures/redaction/`` pairs a synthetic input
text (no real personal data) and input ``kind`` with the byte-exact output
the ``PII_Redactor`` must produce under the current ``REDACTOR_VERSION``.
The fixtures are the committed regression contract for the redaction policy
documented in ``docs/redaction-policy.md``: any behavioral change to the
patterns, lexicons, name heuristic, or boundary rule shows up here as an
exact-string diff and requires a deliberate fixture update plus a
``REDACTOR_VERSION`` bump.

Coverage across the fixture set (Requirement 3.9):

* repeated occurrences of the same value receive the same placeholder index
  (``resume_repeated_email``);
* multiple distinct values of the same type receive distinct indices in
  first-occurrence order (``resume_multiple_distinct_values``);
* Redaction_Exception cases — spans inside an employment-history section of
  a resume preserved verbatim while the same values are redacted in the
  header and Summary (``resume_employment_exception``);
* ``job_description`` kind — whole-text email/phone redaction, no
  exemption for its "Experience" section, no name heuristic
  (``job_description_no_exemption``);
* ``bullet`` kind — whole-text email/phone redaction only
  (``bullet_email_and_phone``).

References:
* Requirements: 3.9 (committed fixtures verified by exact equality).
* Design section "PII_Redactor" — fixtures are the mandated example tests
  complementing Properties 1, 2, and 3.
* Policy: ``docs/redaction-policy.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

import pytest

from matchlayer_api.services.llm.redaction import REDACTOR_VERSION, redact

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "redaction"

_FIXTURE_PATHS = sorted(_FIXTURES_DIR.glob("*.json"))


def _fixture_id(path: Path) -> str:
    return path.stem


def test_fixture_directory_is_populated() -> None:
    """The committed fixture set exists and is non-empty (guards the glob)."""
    assert _FIXTURE_PATHS, f"no redaction fixtures found under {_FIXTURES_DIR}"


@pytest.mark.parametrize("path", _FIXTURE_PATHS, ids=_fixture_id)
def test_redactor_output_matches_fixture_exactly(path: Path) -> None:
    """Redactor output equals the committed expected output byte-for-byte (Req 3.9)."""
    fixture = json.loads(path.read_text(encoding="utf-8"))
    kind = cast("Literal['resume', 'job_description', 'bullet']", fixture["kind"])

    result = redact(fixture["input"], kind=kind)

    assert result.text == fixture["expected"], (
        f"redactor output diverged from committed fixture {path.name}; "
        "a deliberate policy change requires updating the fixture AND "
        "bumping REDACTOR_VERSION"
    )
    assert result.redactor_version == REDACTOR_VERSION
