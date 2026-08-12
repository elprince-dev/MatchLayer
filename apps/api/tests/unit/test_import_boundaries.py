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
import re
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

# The permitted third-party top-level modules inside scoring/: ML libraries
# only. Phase 1 permits scikit-learn (imports under the ``sklearn`` name,
# Requirement 5.8 / 10.1); phase-2-nlp-embeddings task 4.1 adds spaCy for the
# Skill_Extractor (Phase 2 Requirement 4.8 / 12.1 — the boundary bans
# frameworks/storage/web modules, not ML libraries). The fuller Phase 2
# boundary extension (pgvector, env reads, ml/ workspace) is task 12.1.
_PERMITTED_ML_TOP_LEVEL = frozenset({"sklearn", "spacy"})
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
    if top_level in _PERMITTED_ML_TOP_LEVEL:
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


# ===========================================================================
# phase-2-nlp-embeddings, task 12.1 — Phase 2 boundary extensions.
#
# The Phase 1 allowlist check above already rejects anything that is not
# sklearn/spaCy/stdlib/scoring-sibling, which *implicitly* bans FastAPI,
# SQLAlchemy, pgvector, ``matchlayer_api.config``, and every storage/web
# module. The checks below make the phase-2 rules EXPLICIT and traceable:
#
#   * an explicit named-forbidden-modules assertion for ``scoring/`` — so a
#     future loosening of the allowlist (adding a permitted top-level) can
#     never silently re-admit a framework/storage/config import
#     (phase-2 Requirements 12.1, 12.3, 3.7, 4.8);
#   * ``scoring/`` reads no environment variables — the scoring core is
#     configured exclusively through constructor injection, so ``os.environ``
#     / ``os.getenv`` (and importing ``environ``/``getenv`` from ``os``)
#     never appear (phase-2 Requirements 12.1, 12.6);
#   * the repo-root ``ml/`` workspace guard is re-validated for Phase 2 —
#     the Eval_Runner imports FROM ``matchlayer_api.scoring``, never the
#     reverse (phase-2 Requirements 12.3, 11.4). The runtime check is
#     ``test_api_never_imports_repo_root_ml_tree`` above; it is asserted
#     here to hold over the Phase 2 module set too (same walk, one line).
#
# Validates: phase-2 Requirements 12.1, 12.3, 12.6, 3.7, 4.8, 11.4.
# ===========================================================================

# Frameworks, ORMs, storage, web, and config modules the scoring core must
# never import (phase-2 Requirement 12.1 names FastAPI, SQLAlchemy, pgvector,
# and matchlayer_api.config explicitly; the rest are the storage/web modules
# the Phase 1 rule already listed).
_EXPLICITLY_FORBIDDEN_SCORING_TOP_LEVEL = frozenset(
    {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "pgvector",
        "alembic",
        "asyncpg",
        "psycopg",
        "redis",
        "boto3",
        "botocore",
        "httpx",
        "structlog",
    }
)

_CONFIG_MODULE = "matchlayer_api.config"

# ``os`` members whose access constitutes an environment read. ``environ`` /
# ``environb`` are the mapping objects; ``getenv`` is the accessor function.
_ENV_READ_MEMBERS = frozenset({"environ", "environb", "getenv"})


def test_scoring_package_never_imports_frameworks_storage_or_config() -> None:
    """Explicit named ban: no FastAPI/SQLAlchemy/pgvector/config/storage in scoring/.

    Redundant with the allowlist check above by construction — and that is
    the point: if the allowlist is ever loosened, this named denylist still
    fails loudly for the modules the phase-2 design bans by name.

    Validates: phase-2 Requirements 12.1, 12.3, 3.7, 4.8.
    """
    offenders: dict[str, list[str]] = {}
    for path in _iter_scoring_sources():
        tree = _parse(path)
        bad: set[str] = set()
        for module, level in _imported_modules(tree):
            if level > 0 or module is None:
                continue
            if module == _CONFIG_MODULE or module.startswith(f"{_CONFIG_MODULE}."):
                bad.add(module)
                continue
            if module.split(".", 1)[0] in _EXPLICITLY_FORBIDDEN_SCORING_TOP_LEVEL:
                bad.add(module)
        if bad:
            offenders[_relpath(path)] = sorted(bad)

    assert not offenders, (
        "matchlayer_api.scoring.* must never import FastAPI, SQLAlchemy, "
        "pgvector, matchlayer_api.config, or any storage/web module "
        "(phase-2 Requirements 12.1, 12.3); found: "
        + "; ".join(f"{file}: {modules}" for file, modules in offenders.items())
    )


def _environment_read_sites(tree: ast.Module) -> list[int]:
    """Line numbers of every environment-variable read in ``tree``.

    Detects the three read shapes:

    * ``os.environ[...]`` / ``os.environ.get(...)`` / ``os.environb`` —
      an :class:`ast.Attribute` access of a banned member on the name ``os``
      (subscripts and ``.get`` calls both contain that attribute node);
    * ``os.getenv(...)`` — the same attribute shape;
    * ``from os import environ`` / ``from os import getenv`` — an
      :class:`ast.ImportFrom` of a banned member, which would let the module
      read the environment without the ``os.`` prefix.

    Prose in docstrings or comments that merely *mentions* ``os.environ``
    cannot match: the walk inspects attribute and import nodes, not text.
    """
    sites: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ENV_READ_MEMBERS:
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                sites.append(node.lineno)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "os"
            and any(alias.name in _ENV_READ_MEMBERS for alias in node.names)
        ):
            sites.append(node.lineno)
    return sorted(sites)


def test_scoring_package_reads_no_environment_variables() -> None:
    """No ``scoring/`` module reads environment variables.

    The scoring core is configured exclusively through constructor
    injection (weights, caps, lexicon, encoder — all explicit arguments):
    an environment read would smuggle configuration past the injection
    boundary and break isolated unit-testability.

    Validates: phase-2 Requirements 12.1, 12.6.
    """
    offenders: dict[str, list[int]] = {}
    for path in _iter_scoring_sources():
        tree = _parse(path)
        sites = _environment_read_sites(tree)
        if sites:
            offenders[_relpath(path)] = sites

    assert not offenders, (
        "matchlayer_api.scoring.* must read no environment variables "
        "(phase-2 Requirements 12.1, 12.6); found os.environ/os.getenv "
        f"access in: {offenders}"
    )


def test_phase2_modules_never_import_repo_root_ml_tree() -> None:
    """The repo-root ``ml/`` guard holds across the Phase 2 module set.

    Identical walk to ``test_api_never_imports_repo_root_ml_tree`` — kept
    as a separate named test so the phase-2 requirement (the Eval_Runner
    imports FROM ``matchlayer_api.scoring``, never the reverse) has its own
    traceable assertion over the tree that now includes the Phase 2
    modules (semantic adapter, vector store, skills, semantic, versioning).

    Validates: phase-2 Requirements 12.3, 11.4.
    """
    offenders = [
        _relpath(path)
        for path in _iter_package_sources()
        if _imports_module(_parse(path), _REPO_ROOT_ML_TOP_LEVEL)
    ]
    assert not offenders, (
        "No matchlayer_api module may import the repo-root ml/ workspace "
        f"(phase-2 Requirement 12.3); found: {offenders}"
    )


# ===========================================================================
# phase-3-llm-layer, task 1.3 — the redis import boundary.
#
# Design decision D6: Redis client construction moves to ``core/redis.py``,
# the single module allowed to import ``redis``. Every consumer
# (``core/rate_limit.py``'s RateLimiter, the idempotency store in
# ``core/dependencies.py``, and the Phase 3 DailyQuota / LLMCache) receives
# an injected client and annotates it via the ``core/redis.py`` re-exports
# (``Redis``, ``AsyncScript``) — never by importing ``redis`` itself.
#
# ``from matchlayer_api.core.redis import ...`` does NOT trip this check:
# its module string is ``matchlayer_api.core.redis``, which neither equals
# ``redis`` nor starts with ``redis.`` — the same distinction
# ``test_api_never_imports_repo_root_ml_tree`` relies on for the in-package
# ``matchlayer_api.ml`` adapter.
#
# Design reference: phase-3-llm-layer design.md, decision D6.
# Validates: Requirement 13.1 (Rate_Limiter reuse), design decision D6.
# ===========================================================================

_REDIS_ALLOWED = "core/redis.py"


def test_redis_imported_only_in_core_redis_module() -> None:
    """``import redis`` / ``from redis import`` must appear only in core/redis.py.

    Validates: phase-3 Requirement 13.1, design decision D6.
    """
    offenders: list[str] = []
    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel == _REDIS_ALLOWED:
            continue
        tree = _parse(path)
        if _imports_module(tree, "redis"):
            offenders.append(rel)
    assert not offenders, (
        f"redis must only be imported by {_REDIS_ALLOWED}; found imports in: {offenders}"
    )


# ===========================================================================
# phase-3-llm-layer, task 3.5 — OpenRouter confinement, hardcoded-model
# guard, and the prompt-instruction-literal ban.
#
# Three static rules locked down by the phase-3-llm-layer design ("Testing
# Strategy" → boundary/static tests):
#
#   * 1.1 — all OpenRouter-specific code is confined to the single provider
#     adapter module ``ml/llm/openrouter.py``. Two named allowances exist by
#     design: ``ml/llm/availability.py`` is the composition root that
#     constructs the adapter at startup (it may import the adapter module
#     and name ``OpenRouterClient``, but carries no wire-format code), and
#     ``config.py`` holds the provider base-URL *default*
#     (``https://openrouter.ai/api/v1``) — a configuration value, exactly
#     like the model default of Requirement 1.3. Nothing else — no service,
#     router, schema, or the provider-neutral ``ml/llm/client.py`` protocol
#     itself — may reference OpenRouter by literal or identifier.
#   * 1.3 — the LLM model identifier is configuration. No source file
#     outside ``config.py`` may contain a hardcoded ``vendor/model`` string
#     (e.g. ``anthropic/claude-haiku-4.5``) usable for LLM calls.
#   * 2.1 — every prompt instruction text sent to the provider is loaded
#     from a versioned template file under ``ml/prompts/``; no prompt
#     instruction text is assembled from string literals embedded in
#     service or router code. Statically enforced with a sentinel-phrase
#     scan: the instruction phrases the committed templates use (and the
#     generic markers of system-prompt authorship) must never appear in a
#     string literal under ``services/``, ``api/``, ``auth/``, or ``dev/``.
#
# Like every check in this file, these walk the AST rather than grepping
# text: docstrings and comments that merely *describe* the rules (this
# comment block names the adapter, the base URL, and the model default)
# can never produce a false positive, because docstring constants are
# excluded and comments do not exist in the AST at all.
#
# The redis half of task 3.5 ("only core/redis.py imports redis") is
# already enforced above by ``test_redis_imported_only_in_core_redis_module``
# (task 1.3).
#
# Design reference: phase-3-llm-layer design.md "Testing Strategy".
# Validates: phase-3 Requirements 1.1, 1.3, 2.1.
# ===========================================================================

# The single provider adapter module and its one sanctioned constructor site.
_OPENROUTER_ADAPTER = "ml/llm/openrouter.py"
_OPENROUTER_COMPOSITION_ROOT = "ml/llm/availability.py"
_OPENROUTER_ADAPTER_MODULE = "matchlayer_api.ml.llm.openrouter"

# The configuration module and the one provider-URL literal it may carry.
_CONFIG_FILE = "config.py"
_OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"

# Case-insensitive marker for any OpenRouter reference.
_OPENROUTER_MARKER = "openrouter"

# Hardcoded-model guard (Requirement 1.3): a string literal shaped like an
# OpenRouter ``vendor/model`` identifier. The vendor list covers the model
# families plausibly reachable through OpenRouter; a new vendor added here
# widens the guard, never the allowance (config.py stays the only allowed
# location). ``sentence-transformers/...`` (the Phase 2 embedding model) is
# deliberately NOT in this list — it is not an LLM identifier.
_MODEL_ID_PATTERN = re.compile(
    r"^(anthropic|openai|google|meta-llama|mistralai|qwen|deepseek"
    r"|cohere|amazon|x-ai|microsoft|nvidia|perplexity)/[A-Za-z0-9][\w.:-]*$",
    re.IGNORECASE,
)

# The service/router surface Requirement 2.1 bans instruction literals from:
# every module under these top-level package directories.
_SERVICE_AND_ROUTER_DIRS = ("services", "api", "auth", "dev")

# Sentinel instruction phrases (lowercase). Drawn from the committed
# templates' own wording plus the generic markers of system-prompt
# authorship — a prompt instruction pasted into a service or router would
# almost certainly carry at least one of these.
_PROMPT_INSTRUCTION_SENTINELS = (
    "you are matchlayer",
    "you are a resume",
    "you are an ai",
    "your task is to",
    "never reveal",
    "ignore previous instructions",
    "data to analyze, never instructions",
    "instructions to follow",
    "never instructions to follow",
    "do not invent",
    "these system instructions",
    "respond with a single json object",
)


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """Return ``id()``s of every docstring constant node in ``tree``.

    A docstring is the leading ``ast.Expr``-wrapped string constant of a
    module, class, or (async) function body. Collecting their node ids
    lets the literal scans below skip prose that merely *describes* a
    forbidden pattern.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _non_docstring_string_literals(tree: ast.Module) -> list[tuple[int, str]]:
    """Return ``(lineno, value)`` for every non-docstring string literal.

    Covers plain literals and the constant fragments of f-strings (both
    surface as ``ast.Constant`` under :func:`ast.walk`). Docstrings are
    excluded via :func:`_docstring_constant_ids`; comments never appear
    in the AST.
    """
    doc_ids = _docstring_constant_ids(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_ids
    ]


def _identifier_names(tree: ast.Module) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` for every identifier used in ``tree``.

    Collects variable/parameter names, attribute accesses, class and
    function definition names, and import aliases — the shapes through
    which provider-specific code (``OpenRouterClient``, an
    ``openrouter``-named helper) could leak outside the adapter.
    """
    names: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            names.append((node.lineno, node.attr))
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append((node.lineno, node.name))
        elif isinstance(node, ast.arg):
            names.append((node.lineno, node.arg))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.append((node.lineno, alias.asname or alias.name))
    return names


def test_openrouter_referenced_only_in_adapter_module() -> None:
    """OpenRouter literals and identifiers stay inside the sanctioned files.

    * String literals containing ``openrouter`` (case-insensitive, outside
      docstrings) may appear only in ``ml/llm/openrouter.py`` — with the
      single exception of the exact base-URL default literal in
      ``config.py`` (a configuration value, not provider code).
    * Identifiers containing ``openrouter`` (``OpenRouterClient``, etc.)
      may appear only in the adapter itself and in the
      ``ml/llm/availability.py`` composition root that constructs it.

    Validates: phase-3 Requirement 1.1.
    """
    literal_offenders: dict[str, list[int]] = {}
    identifier_offenders: dict[str, list[int]] = {}

    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel == _OPENROUTER_ADAPTER:
            continue
        tree = _parse(path)

        bad_literal_lines = [
            lineno
            for lineno, value in _non_docstring_string_literals(tree)
            if _OPENROUTER_MARKER in value.lower()
            and not (rel == _CONFIG_FILE and value == _OPENROUTER_BASE_URL_DEFAULT)
        ]
        if bad_literal_lines:
            literal_offenders[rel] = sorted(set(bad_literal_lines))

        if rel != _OPENROUTER_COMPOSITION_ROOT:
            bad_name_lines = [
                lineno
                for lineno, name in _identifier_names(tree)
                if _OPENROUTER_MARKER in name.lower()
            ]
            if bad_name_lines:
                identifier_offenders[rel] = sorted(set(bad_name_lines))

    assert not literal_offenders, (
        f"OpenRouter string literals must live only in {_OPENROUTER_ADAPTER} "
        f"(plus the base-URL default in {_CONFIG_FILE}); found (file: lines): "
        f"{literal_offenders}"
    )
    assert not identifier_offenders, (
        f"OpenRouter-named identifiers must live only in {_OPENROUTER_ADAPTER} "
        f"and the {_OPENROUTER_COMPOSITION_ROOT} composition root "
        f"(Requirement 1.1); found (file: lines): {identifier_offenders}"
    )


def test_openrouter_adapter_imported_only_by_composition_root() -> None:
    """Only ``ml/llm/availability.py`` may import the OpenRouter adapter.

    Feature services, routers, and the provider-neutral protocol module
    depend on the ``LLMClient`` protocol, never on the concrete adapter —
    that single import site is what makes the Phase 6 Bedrock swap a
    configuration change plus one new adapter.

    Validates: phase-3 Requirement 1.1.
    """
    offenders: list[str] = []
    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel in (_OPENROUTER_ADAPTER, _OPENROUTER_COMPOSITION_ROOT):
            continue
        tree = _parse(path)
        if _imports_module(tree, _OPENROUTER_ADAPTER_MODULE):
            offenders.append(rel)
    assert not offenders, (
        f"{_OPENROUTER_ADAPTER_MODULE} may be imported only by "
        f"{_OPENROUTER_COMPOSITION_ROOT} (Requirement 1.1); found imports in: "
        f"{offenders}"
    )


def test_no_hardcoded_model_identifier_outside_config_defaults() -> None:
    """No source file outside ``config.py`` hardcodes an LLM model identifier.

    The model is configuration (``MATCHLAYER_LLM_MODEL``, default
    ``anthropic/claude-haiku-4.5``): changing it between deployments must
    require no code change, so a ``vendor/model``-shaped string literal
    anywhere outside the config defaults is a boundary violation.

    Validates: phase-3 Requirement 1.3.
    """
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in _iter_package_sources():
        rel = _relpath(path)
        if rel == _CONFIG_FILE:
            continue
        tree = _parse(path)
        bad = [
            (lineno, value)
            for lineno, value in _non_docstring_string_literals(tree)
            if _MODEL_ID_PATTERN.match(value)
        ]
        if bad:
            offenders[rel] = bad
    assert not offenders, (
        f"LLM model identifiers are configuration ({_CONFIG_FILE} defaults "
        f"only, Requirement 1.3); found hardcoded model-identifier literals: "
        f"{offenders}"
    )


def test_no_prompt_instruction_literals_in_services_or_routers() -> None:
    """No prompt instruction text is embedded in service or router code.

    Every instruction sent to the provider must come from a versioned
    template file under ``ml/prompts/`` — that is what makes the recorded
    prompt version plus input hash fully determine the transmitted prompt
    for Phase 5 replay. A string literal in ``services/``, ``api/``,
    ``auth/``, or ``dev/`` carrying a sentinel instruction phrase means
    instruction text has leaked out of the template files.

    Validates: phase-3 Requirement 2.1.
    """
    scanned = [
        path
        for path in _iter_package_sources()
        if _relpath(path).split("/", 1)[0] in _SERVICE_AND_ROUTER_DIRS
    ]
    assert scanned, (
        "No service/router sources found to scan; expected modules under "
        f"{_SERVICE_AND_ROUTER_DIRS} relative to {_PACKAGE_ROOT}."
    )

    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in scanned:
        tree = _parse(path)
        bad = [
            (lineno, sentinel)
            for lineno, value in _non_docstring_string_literals(tree)
            for sentinel in _PROMPT_INSTRUCTION_SENTINELS
            if sentinel in value.lower()
        ]
        if bad:
            offenders[_relpath(path)] = sorted(set(bad))

    assert not offenders, (
        "Prompt instruction text must live only in versioned template files "
        "under ml/prompts/, never in service or router string literals "
        f"(Requirement 2.1); found sentinel phrases (file: (line, phrase)): "
        f"{offenders}"
    )
