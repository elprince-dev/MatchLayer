#!/usr/bin/env python3
"""Detect drift between the Skill_Lexicon sources of truth and their API copies.

This script is part of the phase-1-matching CI pipeline (Requirement 10.3,
Design "Source of truth vs runtime artifact"), extended by
phase-2-nlp-embeddings (Requirement 5.7) to gate the v2 artifact alongside
v1. Like ``check_env_drift.py`` and the OpenAPI codegen-drift gate, it is
intentionally **stdlib-only** so it can run before any project dependencies
are installed and from any CI image that ships a recent Python interpreter.

What it checks
--------------
Each ``Skill_Lexicon`` version has a single source of truth under ``ml/`` and
a committed copy shipped as API package data (``structure.md``: "the API
imports trained artifacts, not training code"):

* **v1 source** — ``ml/lexicon/skill_lexicon.v1.json``
* **v1 copy**   — ``apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json``
* **v2 source** — ``ml/lexicon/skill_lexicon.v2.json``
* **v2 copy**   — ``apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v2.json``

In a healthy repo every source/copy pair is **byte-identical**. This gate
fails the build if that invariant is broken, in either of two ways:

1. **Divergence** — a package copy differs from its ``ml/`` source (someone
   edited one file by hand, or regenerated without committing both). Checked
   for every committed lexicon version.
2. **Staleness** — the artifacts the build pipeline currently emits (v2) do
   not match what ``ml/pipelines/build_skill_lexicon.py`` would produce
   today (the curated data changed but the artifacts were not regenerated).
   This check delegates to the pipeline's own ``--check`` mode so the two
   stay in lockstep without this tool duplicating the lexicon's
   serialization rules. The v1 artifacts are frozen history — the pipeline
   no longer regenerates them, so they get only the divergence check; the
   Phase 1 fallback path still loads v1 at runtime, so both v1 files must
   remain committed and identical.

A clean run prints a one-line confirmation and exits 0. Any drift exits 1
with a human-readable error and the exact remediation command.

Usage
-----
::

    python3 tools/check_lexicon_drift.py

Run from anywhere; paths are resolved relative to this file's location, but the
canonical CI invocation is from the repo root.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# This file lives at ``tools/check_lexicon_drift.py``; the repo root is its parent.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
_LEXICON_DIR: Path = REPO_ROOT / "ml" / "lexicon"
_PACKAGE_DATA_DIR: Path = REPO_ROOT / "apps" / "api" / "src" / "matchlayer_api" / "scoring" / "data"

# Every committed lexicon version whose source/copy pair must stay
# byte-identical. v1 is frozen (Phase 1 fallback still loads it at runtime);
# v2 is the version the build pipeline currently regenerates.
LEXICON_VERSIONS: tuple[str, ...] = ("v1", "v2")

BUILD_SCRIPT: Path = REPO_ROOT / "ml" / "pipelines" / "build_skill_lexicon.py"

REMEDIATION = "Run: python3 ml/pipelines/build_skill_lexicon.py  (then commit both files)\n"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def check_copy_matches_source(version: str) -> list[str]:
    """Return error lines if the API copy of ``version`` diverges from its source."""
    source_path = _LEXICON_DIR / f"skill_lexicon.{version}.json"
    package_path = _PACKAGE_DATA_DIR / f"skill_lexicon.{version}.json"
    errors: list[str] = []

    source = _read_bytes(source_path)
    if source is None:
        errors.append(f"missing source artifact: {source_path.relative_to(REPO_ROOT)}")

    copy = _read_bytes(package_path)
    if copy is None:
        errors.append(f"missing package copy: {package_path.relative_to(REPO_ROOT)}")

    # Only compare when both are present; a missing file is already reported.
    if source is not None and copy is not None and source != copy:
        errors.append(
            "package copy diverges from the ml/ source "
            f"({package_path.relative_to(REPO_ROOT)} != "
            f"{source_path.relative_to(REPO_ROOT)})"
        )
    return errors


def check_artifacts_are_current() -> list[str]:
    """Return error lines if the pipeline's artifacts are stale vs the build.

    Delegates to ``build_skill_lexicon.py --check`` (executed in-process via
    ``runpy``) so this tool never duplicates the lexicon's serialization rules.
    The pipeline's ``--check`` exits 0 when both committed files of the
    version it currently emits (v2) match its deterministic output and 1
    otherwise.
    """
    if not BUILD_SCRIPT.exists():
        return [f"missing build pipeline: {BUILD_SCRIPT.relative_to(REPO_ROOT)}"]

    argv_backup = sys.argv[:]
    sys.argv = [str(BUILD_SCRIPT), "--check"]
    try:
        runpy.run_path(str(BUILD_SCRIPT), run_name="__main__")
    except SystemExit as exc:  # the pipeline calls raise SystemExit(main())
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            return ["committed artifacts are stale vs ml/pipelines/build_skill_lexicon.py"]
    finally:
        sys.argv = argv_backup
    return []


def main() -> int:
    errors: list[str] = []
    for version in LEXICON_VERSIONS:
        errors += check_copy_matches_source(version)
    errors += check_artifacts_are_current()

    if not errors:
        print(
            "OK: skill_lexicon sources and API package copies agree "
            f"(byte-identical, current) for versions: {', '.join(LEXICON_VERSIONS)}."
        )
        return 0

    sys.stderr.write("error: skill_lexicon drift detected\n\n")
    for err in errors:
        sys.stderr.write(f"    - {err}\n")
    sys.stderr.write("\n" + REMEDIATION)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
