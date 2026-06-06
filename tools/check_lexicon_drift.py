#!/usr/bin/env python3
"""Detect drift between the Skill_Lexicon source of truth and its API copy.

This script is part of the phase-1-matching CI pipeline (Requirement 10.3,
Design "Source of truth vs runtime artifact"). Like ``check_env_drift.py`` and
the OpenAPI codegen-drift gate, it is intentionally **stdlib-only** so it can
run before any project dependencies are installed and from any CI image that
ships a recent Python interpreter.

What it checks
--------------
The ``Skill_Lexicon`` has a single source of truth under ``ml/`` and a
committed copy shipped as API package data (``structure.md``: "the API imports
trained artifacts, not training code"):

* **Source**  — ``ml/lexicon/skill_lexicon.v1.json``
* **Copy**    — ``apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json``

``ml/pipelines/build_skill_lexicon.py`` regenerates both from one curated
dataset, so in a healthy repo they are **byte-identical**. This gate fails the
build if that invariant is broken, in either of two ways:

1. **Divergence** — the copy differs from the source (someone edited one file
   by hand, or regenerated without committing both).
2. **Staleness** — neither file matches what the build pipeline would emit
   today (the curated data in ``build_skill_lexicon.py`` changed but the
   artifacts were not regenerated). This second check delegates to the
   pipeline's own ``--check`` mode so the two stay in lockstep without this
   tool duplicating the lexicon's serialization rules.

A clean run prints a one-line confirmation and exits 0. Any drift exits 1 with
a human-readable error and the exact remediation command.

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
SOURCE_ARTIFACT: Path = REPO_ROOT / "ml" / "lexicon" / "skill_lexicon.v1.json"
PACKAGE_ARTIFACT: Path = (
    REPO_ROOT
    / "apps"
    / "api"
    / "src"
    / "matchlayer_api"
    / "scoring"
    / "data"
    / "skill_lexicon.v1.json"
)
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


def check_copy_matches_source() -> list[str]:
    """Return error lines if the API copy diverges from the ``ml/`` source."""
    errors: list[str] = []

    source = _read_bytes(SOURCE_ARTIFACT)
    if source is None:
        errors.append(f"missing source artifact: {SOURCE_ARTIFACT.relative_to(REPO_ROOT)}")

    copy = _read_bytes(PACKAGE_ARTIFACT)
    if copy is None:
        errors.append(f"missing package copy: {PACKAGE_ARTIFACT.relative_to(REPO_ROOT)}")

    # Only compare when both are present; a missing file is already reported.
    if source is not None and copy is not None and source != copy:
        errors.append(
            "package copy diverges from the ml/ source "
            f"({PACKAGE_ARTIFACT.relative_to(REPO_ROOT)} != "
            f"{SOURCE_ARTIFACT.relative_to(REPO_ROOT)})"
        )
    return errors


def check_artifacts_are_current() -> list[str]:
    """Return error lines if either artifact is stale vs the build pipeline.

    Delegates to ``build_skill_lexicon.py --check`` (executed in-process via
    ``runpy``) so this tool never duplicates the lexicon's serialization rules.
    The pipeline's ``--check`` exits 0 when both committed files match its
    deterministic output and 1 otherwise.
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
    errors = check_copy_matches_source()
    errors += check_artifacts_are_current()

    if not errors:
        print("OK: skill_lexicon source and API package copy agree (byte-identical, current).")
        return 0

    sys.stderr.write("error: skill_lexicon drift detected\n\n")
    for err in errors:
        sys.stderr.write(f"    - {err}\n")
    sys.stderr.write("\n" + REMEDIATION)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
