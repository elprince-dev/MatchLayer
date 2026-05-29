"""Static import-boundary checks for the ``matchlayer_api`` package.

Walks every Python source file under ``apps/api/src/matchlayer_api/``
and asserts the three exclusivity rules locked down by Components
and Interfaces (import-boundary rules) in the phase-1-auth design:

1. ``import jwt`` and ``from jwt import ...`` appear only in
   ``core/security/jwt.py``.
2. ``import argon2`` and ``from argon2 import ...`` appear only in
   ``core/security/passwords.py``.
3. A ``Response.set_cookie(...)`` call referencing the literal cookie
   names ``matchlayer_refresh`` or ``matchlayer_csrf`` appears only
   in ``core/security/cookies.py``.

The implementation uses :mod:`ast` rather than a plain text grep so
the checks are immune to false positives in docstrings, comments,
or string literals that merely *describe* the rule (the package's
``core/security/__init__.py`` docstring, for example, lists every
forbidden pattern by name). A textual grep that flagged those
descriptions as violations would defeat the purpose; the AST walk
inspects the actual import statements and call expressions.

A boundary violation fails the build with a message that names the
offending file and the construct found there, so the regression is
self-explanatory in CI output.

Design reference: design.md "Components and Interfaces", import-boundary rules.
Validates: Requirements 7.1, 1.10.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Package root resolution.
#
# This file lives at apps/api/tests/unit/test_import_boundaries.py and the
# package source lives at apps/api/src/matchlayer_api/. We resolve the source
# tree by walking up from __file__ rather than importing matchlayer_api so a
# violation that breaks the import (e.g. a syntax error introduced by a bad
# refactor) still surfaces as a focused failure here instead of a collection
# error elsewhere.
# ---------------------------------------------------------------------------
_TESTS_UNIT_DIR = Path(__file__).resolve().parent
_API_ROOT = _TESTS_UNIT_DIR.parent.parent  # apps/api/
_PACKAGE_ROOT = _API_ROOT / "src" / "matchlayer_api"

# Files allowed to import each restricted library / call set_cookie with the
# auth cookie names. Stored as POSIX-style paths relative to _PACKAGE_ROOT
# so the assertions are platform-agnostic.
_JWT_ALLOWED = "core/security/jwt.py"
_ARGON2_ALLOWED = "core/security/passwords.py"
_COOKIES_ALLOWED = "core/security/cookies.py"

# The protected cookie names. These literals are what the design says must
# only ever be passed to Response.set_cookie from cookies.py.
_PROTECTED_COOKIE_NAMES = ("matchlayer_refresh", "matchlayer_csrf")


def _iter_package_sources() -> list[Path]:
    """Return every ``.py`` file under the ``matchlayer_api`` package.

    Excludes ``__pycache__`` directories. Sorted for stable failure messages.
    """
    return sorted(path for path in _PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _relpath(path: Path) -> str:
    """Return the file's path relative to the package root, POSIX-style."""
    return path.relative_to(_PACKAGE_ROOT).as_posix()


def _parse(path: Path) -> ast.Module:
    """Parse a source file into an AST module, surfacing parse errors clearly."""
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def _imports_module(tree: ast.Module, target: str) -> bool:
    """Return True if ``tree`` contains an import of ``target`` (or a submodule).

    Detects both forms:

    * ``import target`` and ``import target.submodule [as alias]`` — caught
      via :class:`ast.Import` whose alias name equals ``target`` or starts
      with ``target + "."``.
    * ``from target import name`` and ``from target.sub import name`` — caught
      via :class:`ast.ImportFrom` whose ``module`` attribute equals ``target``
      or starts with ``target + "."``.

    Relative imports (``ImportFrom`` with ``level > 0``) are skipped: the
    package has no submodule named ``jwt`` or ``argon2``, so a relative
    import can't reach the third-party libraries we're guarding.
    """
    target_prefix = f"{target}."
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target or alias.name.startswith(target_prefix):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module is None:
                continue
            if node.module == target or node.module.startswith(target_prefix):
                return True
    return False


def _set_cookie_calls_with_protected_names(tree: ast.Module, source: str) -> list[tuple[int, str]]:
    """Find every ``set_cookie`` call whose source segment names a protected cookie.

    Walks every :class:`ast.Call` whose function is an :class:`ast.Attribute`
    access with ``attr == "set_cookie"`` (the call shape we care about — the
    test does NOT match attribute lookups that aren't actually invoked, so a
    type annotation referring to ``Response.set_cookie`` would not trip it).

    For each match, retrieves the original source segment of the call via
    :func:`ast.get_source_segment` and checks whether it contains either
    protected cookie name as a literal substring. The segment is the call
    expression text only — it does NOT include surrounding docstrings or
    comments — so descriptive prose elsewhere in the file cannot produce
    a false positive.

    Returns a list of ``(lineno, segment)`` tuples for every offending
    call site so a violation message can quote the exact code that must move.
    """
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "set_cookie":
            continue
        segment = ast.get_source_segment(source, node)
        if segment is None:
            # Defensive: ast.get_source_segment can return None for nodes
            # without complete location info. Treat as no match — the call
            # is opaque, but it can't have textually contained the cookie
            # name without a source segment to read.
            continue
        if any(name in segment for name in _PROTECTED_COOKIE_NAMES):
            violations.append((node.lineno, segment))
    return violations


def test_package_root_exists() -> None:
    """Sanity check: the package source tree resolves to a real directory.

    If the test harness ever runs from an unexpected cwd or the layout
    moves, this fails with a clear, focused message instead of letting
    the rest of the file no-op past a missing tree.
    """
    assert _PACKAGE_ROOT.is_dir(), (
        f"Expected matchlayer_api package at {_PACKAGE_ROOT}; "
        f"the import-boundary checks have nothing to scan."
    )
    sources = _iter_package_sources()
    assert sources, f"No .py files found under {_PACKAGE_ROOT}."


def test_jwt_imported_only_in_security_jwt_module() -> None:
    """``import jwt`` / ``from jwt import`` must appear only in core/security/jwt.py.

    Validates: Requirements 7.1, 1.10.
    """
    offenders: list[str] = []
    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel == _JWT_ALLOWED:
            continue
        tree = _parse(path)
        if _imports_module(tree, "jwt"):
            offenders.append(rel)
    assert not offenders, (
        f"PyJWT must only be imported by {_JWT_ALLOWED}; found imports in: {offenders}"
    )


def test_argon2_imported_only_in_security_passwords_module() -> None:
    """``import argon2`` / ``from argon2 import`` must appear only in core/security/passwords.py.

    Validates: Requirements 7.1, 1.10.
    """
    offenders: list[str] = []
    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel == _ARGON2_ALLOWED:
            continue
        tree = _parse(path)
        if _imports_module(tree, "argon2"):
            offenders.append(rel)
    assert not offenders, (
        f"argon2-cffi must only be imported by {_ARGON2_ALLOWED}; found imports in: {offenders}"
    )


def test_protected_set_cookie_calls_only_in_cookies_module() -> None:
    """Auth cookies must only be set in core/security/cookies.py.

    ``Response.set_cookie(...)`` for ``matchlayer_refresh`` or
    ``matchlayer_csrf`` is allowed only in ``core/security/cookies.py``.

    Validates: Requirements 7.1, 1.10.
    """
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel == _COOKIES_ALLOWED:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        matches = _set_cookie_calls_with_protected_names(tree, source)
        if matches:
            offenders[rel] = matches
    assert not offenders, (
        f"Response.set_cookie(...) for {list(_PROTECTED_COOKIE_NAMES)} must "
        f"only appear in {_COOKIES_ALLOWED}; found in: "
        + ", ".join(
            f"{file}@{','.join(str(line) for line, _ in calls)}"
            for file, calls in offenders.items()
        )
    )
