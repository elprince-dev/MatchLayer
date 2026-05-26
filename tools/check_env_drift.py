#!/usr/bin/env python3
"""Detect drift between ``.env.example`` and the codebase.

This script is part of the phase-1-foundation CI pipeline (Requirements 3.5
and 3.6, Design §9.5). It is intentionally stdlib-only so it can run
before any project dependencies are installed and from any CI image that
ships a recent Python interpreter.

What it checks
--------------
The drift check compares two sets:

* **Declared** — variable names parsed out of the repo-root ``.env.example``.
* **Referenced** — variable names the codebase actually consumes:
    1. Direct ``os.environ[...]`` / ``os.environ.get(...)`` / ``os.getenv(...)``
       calls under ``apps/api/src``.
    2. Pydantic ``BaseSettings`` field names under ``apps/api/src``,
       transformed to env-var form via the class's declared
       ``env_prefix`` (uppercased field name appended to the prefix).
       This is how the API actually reads its config — see
       ``apps/api/src/matchlayer_api/config.py`` and ``conventions.md``
       which forbids direct ``os.environ`` access.
    3. ``process.env.<NAME>`` and ``process.env["<NAME>"]`` references
       under ``apps/web/src``.

Both directions of disagreement are failures:

* **Missing** — referenced in code but absent from ``.env.example``. The
  app will boot with an unset variable; the operator never knew to set it.
* **Stale** — declared in ``.env.example`` but unused. The committed
  contract claims a var the code no longer consumes; operators waste time
  setting it. (Or: the var was added in anticipation of a feature that
  hasn't shipped yet — surface the gap so the team can decide.)

A clean run prints a one-line confirmation and exits 0. Any drift exits
1 with a human-readable error naming each variable.

Usage
-----
::

    python3 tools/check_env_drift.py

Run from the repo root. The script discovers paths relative to its own
location, so other working directories work too, but the canonical CI
invocation is from the repo root.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# This file lives at ``tools/check_env_drift.py``; the repo root is its parent.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_EXAMPLE: Path = REPO_ROOT / ".env.example"
API_SRC: Path = REPO_ROOT / "apps" / "api" / "src"
WEB_SRC: Path = REPO_ROOT / "apps" / "web" / "src"

# Only env vars under one of these prefixes are part of the contract. Any
# other ``process.env.*`` or ``os.environ[...]`` access is either a generic
# Node/Python convenience (``NODE_ENV``, ``PATH``, ``HOME``) that doesn't
# belong in ``.env.example`` or, for ``MATCHLAYER_*`` typos in code, will
# show up as a "missing" entry — exactly what we want to catch.
ALLOWED_PREFIXES: tuple[str, ...] = ("MATCHLAYER_", "NEXT_PUBLIC_")

# File extensions to walk per language.
PYTHON_EXTS: frozenset[str] = frozenset({".py"})
JS_EXTS: frozenset[str] = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

# Directory names to skip during traversal. ``apps/web/src`` and
# ``apps/api/src`` shouldn't contain any of these, but we still defend
# against future contributors dropping caches inside the source tree.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".next",
        "__pycache__",
        ".venv",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Direct env-var lookups in Python source. Matches:
#
#   os.environ["FOO"]
#   os.environ.get("FOO")
#   os.environ.get("FOO", "default")
#   os.getenv("FOO")
#   os.getenv("FOO", "default")
#
# The opening ``[`` or ``(`` is matched so a bare reference to ``os.environ``
# (e.g. inside a docstring discussing the convention) does not produce a
# false positive.
PY_DIRECT_ENV_RE: re.Pattern[str] = re.compile(
    r"""
    \b os \s* \. \s*
    (?:
        environ \s* (?: \. \s* get \s* \( | \[ )
      | getenv \s* \(
    )
    \s* (?P<q> ['"] ) (?P<name> [A-Z][A-Z0-9_]* ) (?P=q)
    """,
    re.VERBOSE,
)

# ``process.env.FOO`` / ``process.env["FOO"]`` / ``process.env['FOO']`` /
# ``process.env[\`FOO\`]``. Only one of ``dot`` or ``bracket`` populates per
# match; the consumer ``or``s them.
JS_PROCESS_ENV_RE: re.Pattern[str] = re.compile(
    r"""
    \b process \s* \. \s* env
    (?:
        \s* \. \s* (?P<dot> [A-Z][A-Z0-9_]* )
      | \s* \[ \s* (?P<q> ['"`] ) (?P<bracket> [A-Z][A-Z0-9_]* ) (?P=q) \s* \]
    )
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# .env.example parsing
# ---------------------------------------------------------------------------


def parse_env_example(path: Path) -> set[str]:
    """Return the set of variable names declared in an ``.env``-style file.

    Handles:

    * Blank lines and ``#`` comments.
    * Optional ``export`` prefix (some teams use it; we don't, but tolerate it).
    * Both quoted and unquoted values (we only care about the LHS of ``=``).

    Lines without ``=`` are silently skipped — they're either malformed or
    a header comment that wasn't prefixed with ``#``.
    """
    if not path.exists():
        sys.stderr.write(f"error: {path.relative_to(REPO_ROOT)} not found\n")
        sys.exit(1)

    keys: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, _ = line.partition("=")
        key = key.strip()
        if key:
            keys.add(key)
    return keys


# ---------------------------------------------------------------------------
# File traversal
# ---------------------------------------------------------------------------


def iter_files(root: Path, exts: frozenset[str]) -> Iterator[Path]:
    """Yield every file under ``root`` whose suffix is in ``exts``.

    Skips any file whose path includes one of the entries in ``SKIP_DIRS``
    (caches, node_modules, build outputs).
    """
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in exts:
            continue
        if SKIP_DIRS.intersection(path.parts):
            continue
        yield path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Python: direct os.environ / os.getenv references
# ---------------------------------------------------------------------------


def find_python_direct_refs(root: Path) -> set[str]:
    """Find env-var names accessed via ``os.environ`` / ``os.getenv``."""
    found: set[str] = set()
    for path in iter_files(root, PYTHON_EXTS):
        text = _read_text(path)
        if text is None:
            continue
        for match in PY_DIRECT_ENV_RE.finditer(text):
            name = match.group("name")
            if name.startswith(ALLOWED_PREFIXES):
                found.add(name)
    return found


# ---------------------------------------------------------------------------
# Python: Pydantic Settings field-name extraction
# ---------------------------------------------------------------------------


def find_pydantic_settings_refs(root: Path) -> set[str]:
    """Find env vars implied by ``BaseSettings`` subclasses.

    For each ``class Foo(BaseSettings):`` (or ``BaseSettings`` referenced via
    attribute access) in the tree, read the literal ``env_prefix`` declared
    on ``model_config`` and emit one env-var name per annotated field
    (uppercased and prefixed). Classes without a literal ``env_prefix`` are
    skipped — we cannot know the prefix at static-analysis time, so we
    conservatively decline to invent variables for them.
    """
    found: set[str] = set()
    for path in iter_files(root, PYTHON_EXTS):
        text = _read_text(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not _inherits_from_basesettings(node):
                continue
            prefix = _extract_env_prefix(node)
            if prefix is None:
                # No literal env_prefix declared — refuse to guess.
                continue
            for field_name in _iter_annotated_fields(node):
                found.add(f"{prefix}{field_name.upper()}")
    return found


def _inherits_from_basesettings(cls: ast.ClassDef) -> bool:
    """Return True iff one of ``cls``'s base expressions is ``BaseSettings``.

    Recognizes both forms:

    * ``class Settings(BaseSettings):`` — direct name.
    * ``class Settings(pydantic_settings.BaseSettings):`` — attribute access.

    The check is intentionally syntactic; we don't try to resolve aliases
    (``import BaseSettings as B``) because the codebase doesn't do that and
    static resolution would balloon the script's complexity for no benefit.
    """
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id == "BaseSettings":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseSettings":
            return True
    return False


def _extract_env_prefix(cls: ast.ClassDef) -> str | None:
    """Return the literal ``env_prefix`` value declared on ``cls``.

    Recognizes the two pydantic-settings v2 forms used in the wild:

    * ``model_config = SettingsConfigDict(env_prefix="X_", ...)``
    * ``model_config = {"env_prefix": "X_", ...}``

    Returns ``None`` if no literal prefix is found (including the case of a
    computed/imported prefix value, which we conservatively decline to
    follow).
    """
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not (
            len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "model_config"
        ):
            continue
        value = stmt.value

        # SettingsConfigDict(env_prefix="X_", ...) — the canonical form.
        if isinstance(value, ast.Call):
            for kw in value.keywords:
                if (
                    kw.arg == "env_prefix"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    return kw.value.value

        # {"env_prefix": "X_", ...} — also valid in pydantic-settings v2.
        if isinstance(value, ast.Dict):
            for key, val in zip(value.keys, value.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "env_prefix"
                    and isinstance(val, ast.Constant)
                    and isinstance(val.value, str)
                ):
                    return val.value
    return None


def _iter_annotated_fields(cls: ast.ClassDef) -> Iterable[str]:
    """Yield the names of ``name: Type [= default]`` declarations on ``cls``.

    Filters out:

    * Dunder names (``__private``, ``__init__``).
    * ``model_config`` (Pydantic itself, not a user field).
    """
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            name = stmt.target.id
            if name.startswith("_") or name == "model_config":
                continue
            yield name


# ---------------------------------------------------------------------------
# JS/TS: process.env references
# ---------------------------------------------------------------------------


def find_js_refs(root: Path) -> set[str]:
    """Find ``process.env.<NAME>`` references in JS/TS source under ``root``."""
    found: set[str] = set()
    for path in iter_files(root, JS_EXTS):
        text = _read_text(path)
        if text is None:
            continue
        for match in JS_PROCESS_ENV_RE.finditer(text):
            name = match.group("dot") or match.group("bracket")
            if name and name.startswith(ALLOWED_PREFIXES):
                found.add(name)
    return found


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_list(names: Iterable[str]) -> str:
    return "\n".join(f"    - {name}" for name in names)


def main() -> int:
    declared = parse_env_example(ENV_EXAMPLE)

    referenced: set[str] = set()
    referenced |= find_python_direct_refs(API_SRC)
    referenced |= find_pydantic_settings_refs(API_SRC)
    referenced |= find_js_refs(WEB_SRC)

    missing = sorted(referenced - declared)  # in code, not in .env.example
    stale = sorted(declared - referenced)  # in .env.example, not in code

    if not missing and not stale:
        plural = "s" if len(declared) != 1 else ""
        print(
            f"OK: .env.example and codebase agree ({len(declared)} variable{plural})."
        )
        return 0

    sys.stderr.write("error: .env.example drift detected\n")
    if missing:
        sys.stderr.write(
            "\n  Variables referenced in code but missing from .env.example:\n"
        )
        sys.stderr.write(_format_list(missing) + "\n")
    if stale:
        sys.stderr.write(
            "\n  Variables in .env.example but not referenced in code (stale):\n"
        )
        sys.stderr.write(_format_list(stale) + "\n")
    sys.stderr.write(
        "\n"
        "Resolution:\n"
        "  - Missing: add the variable(s) to .env.example with placeholder values.\n"
        "  - Stale: either remove the entry from .env.example, or add a code\n"
        "    reference (e.g. process.env.NEXT_PUBLIC_FOO) in the consuming app.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
