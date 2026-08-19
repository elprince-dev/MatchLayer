"""Config hygiene check (phase-4-agentic task 17.3).

Validates Requirement 16.7: every Phase 4 ``Settings`` field is listed in
the committed ``.env.example`` with a ``MATCHLAYER_``-prefixed entry, so a
fresh checkout can discover every knob the agentic subsystem reads.

Robustness choice: the Phase 4 field list is **derived programmatically**
from :class:`~matchlayer_api.config.Settings` by name prefix (``sqs_``,
``agent_``, ``otel_`` — the three namespaces task 1.1 introduced) rather
than hardcoded, so a Phase 4 setting added later under one of those
prefixes is checked automatically. A separate assertion pins the derived
set against the ten fields task 1.1 documents, guarding the derivation
itself: if the prefixes ever stop matching (a rename), that test fails
loudly instead of the coverage check silently checking nothing.

No database, Redis, or queue required — a plain unit test.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from matchlayer_api.config import Settings

# The three Settings namespaces the phase-4-agentic spec introduced
# (task 1.1): the SQS Job_Queue block, the agent execution bounds /
# rate limits / cache TTL block, and the OpenTelemetry block.
_PHASE_4_PREFIXES: Final[tuple[str, ...]] = ("sqs_", "agent_", "otel_")

# The ten fields task 1.1 enumerates. The derivation below must cover at
# least these — this set anchors the prefix-based derivation to the spec.
_DOCUMENTED_PHASE_4_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "sqs_queue_url",
        "sqs_region",
        "sqs_endpoint_url",
        "agent_node_timeout_seconds",
        "agent_max_attempts",
        "agent_analyze_rate_limit_per_minute",
        "agent_job_poll_rate_limit_per_minute",
        "agent_cache_ttl_seconds",
        "otel_exporter_otlp_endpoint",
        "otel_service_name",
    }
)

# Matches an assignment line for a MATCHLAYER_-prefixed variable at the
# start of a line (comments and blank lines fall through).
_ENV_LINE = re.compile(r"^(MATCHLAYER_[A-Z0-9_]+)=")


def _repo_root() -> Path:
    """Walk upward to the repo root, identified by the committed .env.example.

    Mirrors ``config._find_repo_root_env``'s marker-based resolution so the
    test finds the same file regardless of pytest's working directory.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".env.example").exists():
            return parent
    raise AssertionError(".env.example not found in any parent directory")


def _phase_4_fields() -> set[str]:
    """The Phase 4 Settings fields, derived from the model by prefix."""
    return {name for name in Settings.model_fields if name.startswith(_PHASE_4_PREFIXES)}


def _env_example_variable_names() -> set[str]:
    """Every MATCHLAYER_* variable name declared in .env.example."""
    text = (_repo_root() / ".env.example").read_text(encoding="utf-8")
    names: set[str] = set()
    for line in text.splitlines():
        match = _ENV_LINE.match(line.strip())
        if match:
            names.add(match.group(1))
    return names


def test_prefix_derivation_covers_the_documented_phase_4_fields() -> None:
    """The programmatic derivation captures every documented Phase 4 field.

    Guards the derivation itself: if a Phase 4 field were renamed out of
    the ``sqs_``/``agent_``/``otel_`` namespaces, the coverage check below
    would silently stop seeing it — this assertion fails first.
    """
    derived = _phase_4_fields()
    missing = _DOCUMENTED_PHASE_4_FIELDS - derived
    assert not missing, f"prefix-based derivation lost documented Phase 4 fields: {sorted(missing)}"


def test_every_phase_4_setting_appears_in_env_example() -> None:
    """Requirement 16.7: every Phase 4 Settings field is in .env.example.

    Each field must appear as ``MATCHLAYER_<FIELD_NAME_UPPERCASED>=`` — the
    exact env name ``pydantic-settings`` reads via the ``MATCHLAYER_``
    prefix (config.py ``SettingsConfigDict(env_prefix="MATCHLAYER_")``).
    """
    declared = _env_example_variable_names()
    missing = sorted(
        f"MATCHLAYER_{field.upper()}"
        for field in _phase_4_fields()
        if f"MATCHLAYER_{field.upper()}" not in declared
    )
    assert not missing, (
        ".env.example is missing Phase 4 settings (Requirement 16.7): "
        f"{missing} — add each with a placeholder value and a comment "
        "stating its unit and effect"
    )
