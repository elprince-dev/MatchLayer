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
import sys
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


# ===========================================================================
# phase-1-matching, task 5.2 — scoring import boundary, repo-root ml/ guard,
# and the static "adapter does no scoring arithmetic" check.
#
# These checks extend the phase-1-auth boundaries above with the rules locked
# down by the phase-1-matching design ("Components and Interfaces" import-
# boundary rule) and Requirement 10:
#
#   * 10.1 / 5.8 — every module under ``matchlayer_api/scoring/`` imports ONLY
#     scikit-learn (top-level ``sklearn``), the Python standard library, and its
#     sibling ``matchlayer_api.scoring`` modules. It never imports FastAPI,
#     SQLAlchemy, ``matchlayer_api.config``, redis, boto3, or any other web /
#     storage / config module — so the scoring logic is unit-testable in
#     isolation.
#   * 10.3 — the regeneration script lives under the repo-root ``ml/pipelines``
#     tree and is NEVER imported by the API at runtime. No module anywhere under
#     ``matchlayer_api`` may import the repo-root ``ml`` package (distinct from
#     the in-package ``matchlayer_api.ml`` adapter).
#   * 10.2 — the ``ml/scorer_adapter`` performs NO scoring arithmetic of its own
#     (the static half: it imports no sklearn and contains no arithmetic
#     operators). The runtime delegation behavior is asserted in
#     ``test_scorer_adapter_delegation.py``.
#
# Like the checks above, these walk the AST rather than grepping text, so prose
# in a docstring that merely *names* a forbidden module (this file, and the
# scoring package docstrings, list ``fastapi``/``sqlalchemy``/``config`` by name)
# cannot produce a false positive.
#
# Design reference: design.md "Components and Interfaces" (import-boundary rule).
# Validates: Requirements 5.8, 10.1, 10.2, 10.3.
# ===========================================================================

# The scoring subpackage, relative to the package root, POSIX-style.
_SCORING_SUBDIR = "scoring"

# The single permitted third-party top-level module inside scoring/ (scikit-learn
# imports under the ``sklearn`` name). Requirement 5.8 / 10.1.
_SKLEARN_TOP_LEVEL = "sklearn"

# First-party roots. Inside scoring/, the ONLY first-party imports allowed are
# the sibling ``matchlayer_api.scoring`` modules — never ``matchlayer_api.config``
# or any other matchlayer_api subpackage.
_FIRST_PARTY_TOP_LEVEL = "matchlayer_api"
_SCORING_PACKAGE = "matchlayer_api.scoring"

# The repo-root training tree. ``import ml`` / ``from ml.pipelines import ...``
# reach the top-level ``ml/`` package that holds ``ml/pipelines`` (Requirement
# 10.3). This is NOT ``matchlayer_api.ml`` (the in-package adapter), which is a
# different, permitted module — the AST checks below distinguish them.
_REPO_ROOT_ML_TOP_LEVEL = "ml"

# The adapter module that must contain no scoring arithmetic (Requirement 10.2).
_SCORER_ADAPTER = "ml/scorer_adapter.py"

# Arithmetic binary operators. Their presence in the adapter would mean it is
# doing scoring math rather than pure marshalling.
_ARITHMETIC_BINOPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.MatMult,
)


def _iter_scoring_sources() -> list[Path]:
    """Return every ``.py`` file under ``matchlayer_api/scoring/`` (sorted)."""
    scoring_root = _PACKAGE_ROOT / _SCORING_SUBDIR
    return sorted(path for path in scoring_root.rglob("*.py") if "__pycache__" not in path.parts)


def _imported_modules(tree: ast.Module) -> list[tuple[str | None, int]]:
    """Return ``(module, level)`` for every import in ``tree``.

    * ``import a.b.c [as x]`` contributes ``("a.b.c", 0)`` per alias.
    * ``from a.b import c`` contributes ``("a.b", 0)``.
    * ``from . import c`` / ``from .sib import c`` contribute ``(module, level)``
      with ``level > 0``; ``module`` may be ``None`` for a bare ``from . import``.

    The ``level`` is preserved so a relative import (which can only ever reach a
    sibling within the same package) is recognized as in-package and allowed.
    """
    refs: list[tuple[str | None, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.extend((alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            refs.append((node.module, node.level))
    return refs


def _scoring_import_is_violation(module: str | None, level: int) -> bool:
    """True if an import ``(module, level)`` breaks the scoring import boundary.

    Allowed: relative (in-package) imports, ``matchlayer_api.scoring[.*]``,
    top-level ``sklearn[.*]``, and any standard-library module
    (``sys.stdlib_module_names``, which includes ``__future__``). Everything
    else — notably ``matchlayer_api.config`` and any other ``matchlayer_api``
    subpackage, ``fastapi``, ``sqlalchemy``, ``redis``, ``boto3`` — is a
    violation (Requirements 5.8, 10.1).
    """
    # Relative imports stay inside the scoring package (a sibling module).
    if level > 0:
        return False
    # ``from __future__`` etc. always carry a module at level 0; a None module
    # at level 0 cannot occur, but treat it as benign rather than crash.
    if module is None:
        return False
    # First-party: only the scoring subpackage itself is permitted.
    if module == _FIRST_PARTY_TOP_LEVEL or module.startswith(f"{_FIRST_PARTY_TOP_LEVEL}."):
        return not (module == _SCORING_PACKAGE or module.startswith(f"{_SCORING_PACKAGE}."))
    top_level = module.split(".", 1)[0]
    if top_level == _SKLEARN_TOP_LEVEL:
        return False
    return top_level not in sys.stdlib_module_names


def test_scoring_package_imports_only_sklearn_stdlib_and_siblings() -> None:
    """Every ``scoring/`` module imports only sklearn, stdlib, and scoring siblings.

    The scoring core is the framework-free heart of Phase 1: it must never reach
    for FastAPI, SQLAlchemy, ``matchlayer_api.config``, redis, boto3, or any
    other web/storage/config module, so it stays unit-testable in isolation
    (design "Components and Interfaces"; Requirements 5.8, 10.1).

    Validates: Requirements 5.8, 10.1.
    """
    scoring_sources = _iter_scoring_sources()
    assert scoring_sources, (
        f"No .py files found under {_PACKAGE_ROOT / _SCORING_SUBDIR}; the scoring "
        f"import-boundary check has nothing to scan."
    )

    offenders: dict[str, list[str]] = {}
    for path in scoring_sources:
        tree = _parse(path)
        bad = sorted(
            {
                module
                for module, level in _imported_modules(tree)
                if _scoring_import_is_violation(module, level) and module is not None
            }
        )
        if bad:
            offenders[_relpath(path)] = bad

    assert not offenders, (
        "matchlayer_api.scoring.* may import ONLY scikit-learn (sklearn), the "
        "Python standard library, and sibling matchlayer_api.scoring modules "
        "(Requirements 5.8, 10.1); found forbidden imports: "
        + "; ".join(f"{file}: {modules}" for file, modules in offenders.items())
    )


def test_api_never_imports_repo_root_ml_tree() -> None:
    """No ``matchlayer_api`` module imports the repo-root ``ml`` package.

    The repo-root ``ml/`` tree (``ml/pipelines``, ``ml/lexicon``, ``ml/evals``)
    is training / build / exploration code that must never be imported by the
    running API (Requirement 10.3, ``structure.md``). A bare ``import ml`` or
    ``from ml.pipelines import ...`` reaches that tree; the in-package
    ``matchlayer_api.ml`` adapter is a different module and is NOT flagged by
    :func:`_imports_module` (its module string is ``matchlayer_api.ml...``,
    which neither equals ``ml`` nor starts with ``ml.``).

    Validates: Requirement 10.3.
    """
    offenders: list[str] = []
    for path in _iter_package_sources():
        tree = _parse(path)
        if _imports_module(tree, _REPO_ROOT_ML_TOP_LEVEL):
            offenders.append(_relpath(path))
    assert not offenders, (
        "The API must never import the repo-root ml/ tree (which holds "
        "ml/pipelines); found imports of the top-level `ml` package in: "
        f"{offenders}"
    )


def test_scorer_adapter_contains_no_scoring_arithmetic() -> None:
    """The ``ml/scorer_adapter`` does pure marshalling — no scoring math.

    Requirement 10.2: the adapter "performs no scoring arithmetic of its own
    beyond marshalling inputs and outputs." This is the static half of that
    guarantee:

    * the adapter imports no scikit-learn (all TF-IDF / cosine work lives in
      ``scoring/``), and
    * the adapter's module body contains no arithmetic binary operators
      (``+``, ``-``, ``*``, ``/``, ``//``, ``%``, ``**``, ``@``).

    The runtime delegation behavior (``score`` forwards to the cached
    ``Match_Scorer`` and returns its result unchanged) is asserted in
    ``test_scorer_adapter_delegation.py``.

    Validates: Requirement 10.2.
    """
    adapter_path = _PACKAGE_ROOT / _SCORER_ADAPTER
    assert adapter_path.is_file(), f"expected the scorer adapter at {adapter_path}"
    tree = _parse(adapter_path)

    # No scikit-learn import: the adapter never vectorizes or computes
    # similarity; it only constructs and calls the scorer.
    assert not _imports_module(tree, _SKLEARN_TOP_LEVEL), (
        f"{_SCORER_ADAPTER} must not import scikit-learn; all scoring math lives "
        f"in matchlayer_api.scoring (Requirement 10.2)."
    )

    # No arithmetic operators anywhere in the module body.
    arithmetic_sites = sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITHMETIC_BINOPS)
    )
    assert not arithmetic_sites, (
        f"{_SCORER_ADAPTER} must perform no scoring arithmetic of its own "
        f"(Requirement 10.2); found arithmetic operator(s) at line(s): "
        f"{arithmetic_sites}"
    )
