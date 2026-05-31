"""Feature: phase-1-matching — Property 19.

Property 19: Per-user scoping never leaks another user's rows.

    *For any* two distinct User_Accounts each owning an arbitrary set of resumes
    and matches, every list and get performed as one user returns only rows
    owned by that user — no query result ever includes a row owned by the other
    user.

**Validates: Requirements 1.4**

Why this is a *static* (no-DB) property test
---------------------------------------------
Requirement 1.4 mandates that the ``Resume_Service`` (``services/resumes.py``)
and the ``Scoring_Service`` (``services/matching.py``) "scope every read and
write to the ``user_id`` derived from the Access_Token's ``sub`` claim, so query
results never include rows owned by a different User_Account." The *runtime*,
end-to-end proof of that guarantee — that a request as user A receives a 404 for
user B's resource and never sees B's rows in a list — is exercised by the
integration tests against a real Postgres (the other-owner 404 cases in tasks
10.7 and 11.5). Those tests require a database; this property test deliberately
does **not** (the task forbids Postgres/Redis here), and the design's Testing
Strategy says "never leaks"-type guarantees are validated by property tests
"where feasible and integration tests otherwise."

The feasible, DB-free formulation of "no query ever returns another user's
rows" is the structural invariant the runtime behavior rests on: **every
SQLAlchemy query the two services issue against the ``resumes`` /
``match_results`` tables is scoped by a ``Model.user_id == <current user>``
predicate.** A query that omitted that predicate is *exactly* the regression
Property 19 guards against — it could return rows owned by a different user. So
this module parses the two service modules with :mod:`ast` and proves that every
``select`` / ``update`` / ``delete`` whose target is ``Resume`` or
``MatchResult`` carries a ``user_id`` equality predicate. This mirrors the
AST-walk style of ``tests/unit/test_import_boundaries.py`` (which proves a
different structural invariant the same way) and composes with the integration
tests that prove the runtime behavior end to end. It is intentionally analogous
to — not a replacement for — those DB-backed tests.

A purely enumerative assertion over the discovered query sites would be strong
but would only ever exercise the handful of queries that exist today; a broken
analyzer (one that silently matched nothing, or one whose predicate detector was
too lax to notice a *missing* scope) could let a real regression through. To
keep the property genuinely meaningful and non-vacuous, this module therefore
has two complementary halves, both driven by Hypothesis (>=100 examples each):

1. **The analyzer is proven sound on generated inputs.** Hypothesis synthesizes
   query-predicate sets and whole query chains — inline and variable-built, with
   the scope predicate sometimes present, sometimes absent, sometimes placed in
   a chained ``.where(...)`` continuation, and always salted with adversarial
   distractors (the *wrong* model's ``user_id``, the right model's ``id`` /
   ``created_at`` / ``deleted_at``, a non-equality ``user_id`` comparison, a
   tuple keyset comparison, an ``is_(None)`` call) — and asserts the analyzer
   reports "scoped" *iff* a genuine ``Model.user_id == X`` predicate for the
   queried model is present. This proves the detector both catches an unscoped
   query and is not fooled into a false pass by a look-alike predicate.

2. **The proven analyzer is applied exhaustively to the real code.** Every query
   site discovered in ``services/resumes.py`` and ``services/matching.py`` must
   be user-scoped; a floor on the number of discovered sites guards against the
   walker silently finding nothing. If an unscoped query is ever introduced,
   this test fails and names the offending file, function, line, and model — the
   real per-user-isolation defect Requirement 1.4 forbids.

No FastAPI app, database, network, or Redis is touched: the modules are read
from disk and parsed, never imported or executed.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Package / service-module resolution.
#
# This file lives at apps/api/tests/property/test_per_user_scoping.py and the
# package source lives at apps/api/src/matchlayer_api/. We resolve the two
# service modules by walking up from __file__ rather than importing
# matchlayer_api so that a syntax error introduced into a service by a bad
# refactor surfaces as a focused parse failure here instead of a collection
# error elsewhere (matching test_import_boundaries.py's rationale).
# ---------------------------------------------------------------------------
_PROPERTY_DIR = Path(__file__).resolve().parent
_API_ROOT = _PROPERTY_DIR.parent.parent  # apps/api/
_PACKAGE_ROOT = _API_ROOT / "src" / "matchlayer_api"
_SERVICES_DIR = _PACKAGE_ROOT / "services"

# The two modules under analysis and the table-backed ORM models each one is the
# sole writer of (Components and Interfaces import-boundary table; Requirement
# 1.4). ``Resume`` is *read* by the Scoring_Service too (the ownership check in
# ``_load_owned_resume``), so both models can legitimately appear in either file.
_RESUME_SERVICE = _SERVICES_DIR / "resumes.py"
_MATCHING_SERVICE = _SERVICES_DIR / "matching.py"
_SERVICE_FILES: tuple[Path, ...] = (_RESUME_SERVICE, _MATCHING_SERVICE)

# The ORM models whose every query MUST be user-scoped (Requirement 1.4).
MODELS: frozenset[str] = frozenset({"Resume", "MatchResult"})

# The SQLAlchemy statement constructors that begin a table query.
_QUERY_CONSTRUCTORS: frozenset[str] = frozenset({"select", "update", "delete"})

# Chain methods that contribute filter predicates to a query.
_WHERE_METHODS: frozenset[str] = frozenset({"where", "filter"})

# Chain methods that name a query's target table after the constructor.
_TARGET_METHODS: frozenset[str] = frozenset({"select_from", "join", "outerjoin"})

# The scoping column. A query is user-scoped iff it carries ``Model.user_id ==
# <anything>`` — the literal ``WHERE user_id = :current_user`` the design and
# Requirement 1.4 prescribe. Equality is required on purpose: a ``>=`` / ``<``
# comparison, an ``is_(None)``, or a tuple keyset comparison on ``user_id`` is
# NOT a per-user scope and must not be accepted.
_SCOPE_COLUMN = "user_id"

# Anti-vacuity floor: the analyzer must discover at least this many user-scoped
# query sites across the two services, so a walker that silently matches nothing
# fails loudly instead of passing. The services issue nine such sites today
# (resumes.py: quota count, list, get, soft-delete = 4 Resume sites;
# matching.py: list, get, soft-delete, quota count = 4 MatchResult sites, plus
# the read-only ownership check = 1 Resume site). The floor sits safely below
# that real count so a benign refactor that merges a couple of queries still
# passes, while a broken analyzer cannot.
_MIN_DISCOVERED_SITES = 6


@dataclass(frozen=True, slots=True)
class QuerySite:
    """One discovered query against ``Resume`` or ``MatchResult``.

    Attributes:
        model: The queried ORM model name (``"Resume"`` or ``"MatchResult"``).
        scoped: ``True`` iff the query's effective ``.where(...)`` predicates
            include a ``model.user_id == <anything>`` equality comparison.
        func_name: The enclosing function/method name (for failure messages).
        lineno: The 1-based source line of the query's outermost chain call.
    """

    model: str
    scoped: bool
    func_name: str
    lineno: int


@dataclass(frozen=True, slots=True)
class _Chain:
    """A select/update/delete-rooted SQLAlchemy chain found in a function."""

    outermost: ast.Call
    models: frozenset[str]
    predicates: tuple[ast.expr, ...]
    lineno: int


# ---------------------------------------------------------------------------
# AST chain helpers.
# ---------------------------------------------------------------------------


def _unwind(call: ast.Call) -> tuple[ast.expr, list[tuple[str, list[ast.expr]]]]:
    """Unwind a method chain into its base expression and its links.

    Walks from the outermost ``Call`` inward along ``.func.value`` while each
    node is a method call (``Call`` whose ``func`` is an ``Attribute``),
    collecting ``(attr_name, args)`` for every link from outermost to innermost.
    Stops at the chain's base — the innermost expression, which for a query is a
    ``select(...)`` / ``update(...)`` / ``delete(...)`` call (base func is a
    ``Name``) or a query variable (a bare ``Name`` for a chained reassignment).
    """
    links: list[tuple[str, list[ast.expr]]] = []
    node: ast.expr = call
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        links.append((node.func.attr, list(node.args)))
        node = node.func.value
    return node, links


def _base_constructor(base: ast.expr) -> tuple[str, list[ast.expr]] | None:
    """Return ``(constructor_name, args)`` if ``base`` is a query constructor call.

    A query constructor is ``select(...)`` / ``update(...)`` / ``delete(...)`` —
    a ``Call`` whose ``func`` is a plain ``Name`` in :data:`_QUERY_CONSTRUCTORS`.
    Returns ``None`` for anything else (a bare ``Name`` continuation base, a
    ``session.execute(...)`` call, ``func.count()``, etc.).
    """
    if (
        isinstance(base, ast.Call)
        and isinstance(base.func, ast.Name)
        and base.func.id in _QUERY_CONSTRUCTORS
    ):
        return base.func.id, list(base.args)
    return None


def _target_models(
    base_args: list[ast.expr], links: list[tuple[str, list[ast.expr]]]
) -> frozenset[str]:
    """Return the set of ORM models a chain targets.

    A target comes from the constructor's first positional argument
    (``select(Resume)`` / ``update(MatchResult)`` / ``delete(Resume)``) or from a
    ``select_from`` / ``join`` link (``select(func.count()).select_from(Resume)``).
    Only names in :data:`MODELS` are reported; ``select(func.count())`` with no
    model ``select_from`` contributes nothing.
    """
    found: set[str] = set()
    if base_args:
        first = base_args[0]
        if isinstance(first, ast.Name) and first.id in MODELS:
            found.add(first.id)
    for attr, args in links:
        if attr in _TARGET_METHODS and args:
            arg0 = args[0]
            if isinstance(arg0, ast.Name) and arg0.id in MODELS:
                found.add(arg0.id)
    return frozenset(found)


def _where_predicates(links: list[tuple[str, list[ast.expr]]]) -> list[ast.expr]:
    """Return every positional argument passed to a ``.where(...)`` / ``.filter(...)`` link."""
    predicates: list[ast.expr] = []
    for attr, args in links:
        if attr in _WHERE_METHODS:
            predicates.extend(args)
    return predicates


def _strip_await(node: ast.expr | None) -> ast.expr | None:
    """Return ``node`` with any wrapping ``await`` removed."""
    while isinstance(node, ast.Await):
        node = node.value
    return node


def _has_user_scope(predicates: list[ast.expr], model: str) -> bool:
    """Return ``True`` iff some predicate is ``model.user_id == <anything>``.

    Walks each predicate AST looking for an equality :class:`ast.Compare` whose
    left operand is the attribute ``user_id`` accessed on the ``Name`` *model*.
    Equality is required (a single :class:`ast.Eq` op): a ``user_id`` compared
    with ``>=`` / ``<`` / ``!=``, an ``is_(None)`` call, or a tuple keyset
    comparison is not a per-user scope. The *model* must match: a
    ``Resume.user_id == x`` predicate does not scope a ``MatchResult`` query.
    """
    for predicate in predicates:
        for node in ast.walk(predicate):
            if not isinstance(node, ast.Compare):
                continue
            if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
                continue
            left = node.left
            if (
                isinstance(left, ast.Attribute)
                and left.attr == _SCOPE_COLUMN
                and isinstance(left.value, ast.Name)
                and left.value.id == model
            ):
                return True
    return False


def _select_rooted_chains(scope: ast.AST) -> list[_Chain]:
    """Return every select/update/delete-rooted query chain within ``scope``.

    Finds the *outermost* ``Call`` of each method chain (one that is not itself
    the ``.func.value`` of an enclosing chain call) whose unwound base is a query
    constructor and whose target is a model in :data:`MODELS`. Each chain records
    its own ``.where(...)`` predicates; predicates added later via a chained
    reassignment (``stmt = stmt.where(...)``) are associated separately by
    :func:`_analyze_function`.
    """
    inner: set[int] = set()
    calls: list[ast.Call] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            calls.append(node)
            if isinstance(node.func, ast.Attribute):
                inner.add(id(node.func.value))

    chains: list[_Chain] = []
    for call in calls:
        if id(call) in inner:
            continue  # not the outermost call of its chain
        base, links = _unwind(call)
        constructor = _base_constructor(base)
        if constructor is None:
            continue
        _, base_args = constructor
        models = _target_models(base_args, links)
        if not (models & MODELS):
            continue
        chains.append(
            _Chain(
                outermost=call,
                models=models & MODELS,
                predicates=tuple(_where_predicates(links)),
                lineno=call.lineno,
            )
        )
    return chains


def _assignment_targets(stmt: ast.Assign | ast.AnnAssign) -> list[str]:
    """Return the simple ``Name`` targets bound by an assignment statement."""
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target.id] if isinstance(stmt.target, ast.Name) else []
    names: list[str] = []
    for target in stmt.targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _analyze_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[QuerySite]:
    """Return the user-scoping verdict for every model query site in ``func``.

    Handles both query-construction patterns the services use:

    * **Inline** — ``await session.execute(select(Model).where(...))``: the whole
      chain (including its scope predicate) lives in one expression.
    * **Variable-built** — ``stmt = select(Model).where(...)`` followed by
      ``stmt = stmt.where(...)`` continuations: the scope predicate may live in
      the initial assignment or in a later ``.where(...)`` on the same variable.
      Continuation predicates are accumulated per variable and merged into the
      select-rooted chain bound to that variable.
    """
    chains = _select_rooted_chains(func)

    chain_for_var: dict[str, _Chain] = {}
    continuation_predicates: dict[str, list[ast.expr]] = defaultdict(list)

    for stmt in ast.walk(func):
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        value = _strip_await(stmt.value)
        if value is None:
            continue
        targets = _assignment_targets(stmt)
        target_name = targets[0] if len(targets) == 1 else None

        if isinstance(value, ast.Call):
            base, links = _unwind(value)
            if _base_constructor(base) is not None:
                # The RHS is itself a select-rooted chain: bind it to the target
                # variable so later ``var = var.where(...)`` continuations attach.
                if target_name is not None:
                    for chain in chains:
                        if chain.outermost is value:
                            chain_for_var[target_name] = chain
                            break
            elif isinstance(base, ast.Name):
                # A chained reassignment (``stmt = stmt.where(...)``): its
                # predicates belong to whichever select chain that variable holds.
                extra = _where_predicates(links)
                if extra:
                    continuation_predicates[base.id].extend(extra)

    sites: list[QuerySite] = []
    for chain in chains:
        effective = list(chain.predicates)
        for var, bound in chain_for_var.items():
            if bound is chain:
                effective.extend(continuation_predicates.get(var, []))
                break
        for model in sorted(chain.models):
            sites.append(
                QuerySite(
                    model=model,
                    scoped=_has_user_scope(effective, model),
                    func_name=func.name,
                    lineno=chain.lineno,
                )
            )
    return sites


def analyze_source(source: str) -> list[QuerySite]:
    """Parse ``source`` and return every model query site with its scoping verdict."""
    tree = ast.parse(source)
    sites: list[QuerySite] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sites.extend(_analyze_function(node))
    return sites


# ===========================================================================
# Half 1 — the analyzer is proven SOUND on Hypothesis-generated query chains.
#
# We synthesize SQLAlchemy-shaped query source (as text), then assert the
# analyzer's "scoped" verdict matches the ground truth we constructed: the chain
# is reported user-scoped *iff* we actually emitted a ``Model.user_id == X``
# equality predicate for the queried model. The generated chains include
# adversarial distractor predicates that a too-lax detector would mistake for a
# scope, plus both the inline and variable-built construction patterns the real
# services use — so a passing analyzer cannot be fooled by a look-alike, and
# cannot miss a genuinely-missing scope.
# ===========================================================================

# Distractor predicates that are NOT a per-user equality scope. A correct
# analyzer must treat every one of these as "unscoped" on its own. They mirror
# the real predicates the services use alongside the scope: the row id, the
# soft-delete filter, the keyset cursor comparison, the quota's ``created_at``
# lower bound, and a non-equality / wrong-model ``user_id`` comparison.
_DISTRACTORS: dict[str, str] = {
    "id_eq": "{model}.id == resume_id",
    "deleted_is_none": "{model}.deleted_at.is_(None)",
    "created_ge": "{model}.created_at >= day_start",
    "keyset": "tuple_({model}.created_at, {model}.id) < (cursor_created_at, cursor_id)",
    "user_id_ge": "{model}.user_id >= floor",
    "user_id_ne": "{model}.user_id != other_id",
}

# The genuine per-user scope predicate, in the two equivalent spellings the
# services use (the bound value is either the User object's ``.id`` attribute or
# the already-resolved ``user_id`` parameter). Both are ``Model.user_id == X``.
_SCOPE_SPELLINGS: tuple[str, ...] = (
    "{model}.user_id == user.id",
    "{model}.user_id == user_id",
)


def _wrong_model(model: str) -> str:
    """Return the *other* model name, for cross-model distractor predicates."""
    return "MatchResult" if model == "Resume" else "Resume"


@st.composite
def _query_scenario(draw: st.DrawFn) -> tuple[str, str, bool]:
    """Generate ``(source, model, expected_scoped)`` for one query chain.

    Builds a syntactically valid function whose body issues a select-rooted
    query against a randomly chosen model, with:

    * a random subset of non-scope distractor predicates (including, sometimes,
      the *wrong* model's ``user_id == x`` — which must NOT count as scoping the
      queried model),
    * optionally the genuine ``Model.user_id == X`` scope predicate, and
    * a randomly chosen construction style (inline ``select(...).where(...)`` vs
      variable-built with a chained ``stmt = stmt.where(...)`` continuation) and
      constructor (``select`` / ``update`` / ``delete``).

    ``expected_scoped`` is the ground truth: ``True`` iff a genuine scope
    predicate for the queried model was emitted.
    """
    model = draw(st.sampled_from(sorted(MODELS)))
    constructor = draw(st.sampled_from(sorted(_QUERY_CONSTRUCTORS)))
    include_scope = draw(st.booleans())
    # Sometimes emit the WRONG model's user_id == x as a distractor: it must not
    # be mistaken for a scope on the queried model.
    include_wrong_model_scope = draw(st.booleans())
    style = draw(st.sampled_from(["inline", "variable", "continuation"]))

    chosen_distractors = draw(
        st.lists(
            st.sampled_from(sorted(_DISTRACTORS)),
            min_size=0,
            max_size=len(_DISTRACTORS),
            unique=True,
        )
    )

    predicates: list[str] = [_DISTRACTORS[name].format(model=model) for name in chosen_distractors]
    if include_wrong_model_scope:
        predicates.append(f"{_wrong_model(model)}.user_id == other_id")

    scope_predicate = draw(st.sampled_from(_SCOPE_SPELLINGS)).format(model=model)
    if include_scope:
        # Place the scope at a random position so the analyzer must scan all
        # predicates, not just the first.
        insert_at = draw(st.integers(min_value=0, max_value=len(predicates)))
        predicates.insert(insert_at, scope_predicate)

    # Build the target expression: select(Model) / update(Model) / delete(Model),
    # except a count-style select that names its target via .select_from(Model).
    use_select_from = constructor == "select" and draw(st.booleans())
    if use_select_from:
        target_expr = f"{constructor}(func.count()).select_from({model})"
    else:
        target_expr = f"{constructor}({model})"

    if style == "inline" or not predicates:
        where_clause = f".where({', '.join(predicates)})" if predicates else ""
        body = f"    result = await session.execute({target_expr}{where_clause})\n"
    elif style == "variable":
        where_clause = f".where({', '.join(predicates)})" if predicates else ""
        body = f"    stmt = {target_expr}{where_clause}\n    result = await session.execute(stmt)\n"
    else:  # "continuation": initial chain, then a chained ``stmt = stmt.where(...)``
        first, *rest = predicates
        cont = "".join(f"    stmt = stmt.where({predicate})\n" for predicate in rest)
        body = (
            f"    stmt = {target_expr}.where({first})\n"
            f"{cont}"
            f"    result = await session.execute(stmt)\n"
        )

    signature = (
        "async def _q(session, user, user_id, resume_id, day_start, "
        "cursor_created_at, cursor_id, floor, other_id):\n"
    )
    source = signature + body
    return source, model, include_scope


@settings(max_examples=300, deadline=None)
@given(scenario=_query_scenario())
def test_analyzer_reports_scoped_iff_user_id_equality_present(
    scenario: tuple[str, str, bool],
) -> None:
    """The analyzer flags a query scoped *iff* a genuine ``user_id ==`` is present.

    Property 19 (analyzer-soundness half): across generated select/update/delete
    chains — inline, variable-built, and continuation styles, salted with
    non-scope distractors and the wrong model's ``user_id`` — the analyzer's
    ``scoped`` verdict equals the ground truth. This proves the detector both
    *catches* an unscoped query (the regression Requirement 1.4 forbids) and is
    *not fooled* by a look-alike predicate (``id ==``, ``created_at >=``, a
    keyset tuple comparison, ``user_id >=/!=``, or another model's
    ``user_id ==``).
    """
    source, model, expected_scoped = scenario
    sites = analyze_source(source)

    # Exactly one query site is generated, and it targets the chosen model.
    relevant = [site for site in sites if site.model == model]
    assert relevant, f"analyzer found no {model} query site in:\n{source}"
    assert len(relevant) == 1, f"expected one {model} site, got {len(relevant)} in:\n{source}"

    assert relevant[0].scoped is expected_scoped, (
        f"scoped verdict {relevant[0].scoped} != expected {expected_scoped} for:\n{source}"
    )


@settings(max_examples=200, deadline=None)
@given(
    model=st.sampled_from(sorted(MODELS)),
    distractors=st.lists(
        st.sampled_from(sorted(_DISTRACTORS)), min_size=1, max_size=len(_DISTRACTORS), unique=True
    ),
    constructor=st.sampled_from(sorted(_QUERY_CONSTRUCTORS)),
)
def test_distractor_only_query_is_never_reported_scoped(
    model: str, distractors: list[str], constructor: str
) -> None:
    """A query carrying ONLY non-scope predicates is always reported unscoped.

    Property 19 (false-positive guard): no combination of the look-alike
    predicates the services legitimately use (``id ==``, ``deleted_at.is_(None)``,
    ``created_at >=``, the keyset tuple comparison, ``user_id >=`` / ``!=``)
    counts as a per-user scope. Only a genuine ``Model.user_id == X`` equality
    does — so a query missing that predicate can never slip past as "scoped".
    """
    predicates = [_DISTRACTORS[name].format(model=model) for name in distractors]
    where_clause = ", ".join(predicates)
    source = (
        "async def _q(session, resume_id, day_start, cursor_created_at, "
        "cursor_id, floor, other_id):\n"
        f"    result = await session.execute({constructor}({model}).where({where_clause}))\n"
    )

    sites = [site for site in analyze_source(source) if site.model == model]
    assert sites, f"analyzer found no {model} query site in:\n{source}"
    assert all(not site.scoped for site in sites), (
        f"a distractor-only query was wrongly reported scoped:\n{source}"
    )


# ===========================================================================
# Half 2 — the proven analyzer is applied EXHAUSTIVELY to the real services.
#
# Every Resume / MatchResult query site discovered in resumes.py and matching.py
# must be user-scoped. This is the direct assertion of Requirement 1.4 against
# the shipping code; an unscoped query introduced by a future change fails here
# with the file, function, line, and model named.
# ===========================================================================


def _discover_real_sites() -> dict[Path, list[QuerySite]]:
    """Return the discovered query sites for each service module, keyed by path."""
    discovered: dict[Path, list[QuerySite]] = {}
    for path in _SERVICE_FILES:
        assert path.is_file(), f"expected service module at {path}"
        discovered[path] = analyze_source(path.read_text(encoding="utf-8"))
    return discovered


def test_service_modules_exist() -> None:
    """Sanity check: both service modules resolve to real files on disk.

    If the layout moves or the harness runs from an unexpected cwd, this fails
    with a focused message rather than letting the exhaustive check below no-op
    past missing files.
    """
    for path in _SERVICE_FILES:
        assert path.is_file(), (
            f"expected service module at {path}; the per-user-scoping check has nothing to scan."
        )


def test_every_real_service_query_is_user_scoped() -> None:
    """Every ``Resume`` / ``MatchResult`` query in the two services is user-scoped.

    Property 19 (real-code half; Requirement 1.4): walking ``services/resumes.py``
    and ``services/matching.py``, every ``select`` / ``update`` / ``delete``
    targeting ``Resume`` or ``MatchResult`` carries a ``Model.user_id == X``
    equality predicate. An unscoped query is exactly the leak Requirement 1.4
    forbids — a list/get/delete that could touch another user's rows — so its
    presence fails this test with the offending file, function, line, and model
    named. The DB-backed end-to-end proof (a request as user A getting a 404 for
    user B's row) lives in the integration tests (tasks 10.7, 11.5).
    """
    discovered = _discover_real_sites()

    unscoped: list[str] = []
    total_sites = 0
    for path, sites in discovered.items():
        for site in sites:
            total_sites += 1
            if not site.scoped:
                unscoped.append(
                    f"{path.name}:{site.lineno} in {site.func_name}() — "
                    f"{site.model} query missing `{site.model}.user_id == <current user>`"
                )

    assert not unscoped, (
        "Per-user scoping leak (Requirement 1.4): every Resume/MatchResult query "
        "in the Resume_Service and Scoring_Service must be scoped by "
        "`Model.user_id == <current user>`; found unscoped query site(s):\n  "
        + "\n  ".join(unscoped)
    )

    # Anti-vacuity floor: the walker must actually find query sites, so a silent
    # "matched nothing" failure can never masquerade as a pass.
    assert total_sites >= _MIN_DISCOVERED_SITES, (
        f"expected at least {_MIN_DISCOVERED_SITES} Resume/MatchResult query "
        f"sites across the two services, found {total_sites}; the analyzer may "
        f"have stopped discovering queries (a refactor of the query shape?)."
    )


def test_both_models_and_both_services_are_covered() -> None:
    """The discovered sites span both models and both service modules.

    A guard that the exhaustive check is genuinely exercising the surface
    Requirement 1.4 governs: ``resumes.py`` queries ``Resume`` (and is the sole
    writer of it), ``matching.py`` queries ``MatchResult`` (sole writer) and also
    *reads* ``Resume`` for the ownership check in ``_load_owned_resume`` — so
    both models appear, and both files contribute scoped sites. If any of these
    disappears, the query shape changed materially and the analyzer assumptions
    should be revisited.
    """
    discovered = _discover_real_sites()

    resume_sites = discovered[_RESUME_SERVICE]
    matching_sites = discovered[_MATCHING_SERVICE]

    assert any(site.model == "Resume" for site in resume_sites), (
        "expected resumes.py to issue at least one Resume query"
    )
    assert any(site.model == "MatchResult" for site in matching_sites), (
        "expected matching.py to issue at least one MatchResult query"
    )
    # matching.py reads Resume for the ownership check (Requirement 8.4) — that
    # read must be user-scoped too, so it should be discovered and scoped.
    matching_resume_sites = [site for site in matching_sites if site.model == "Resume"]
    assert matching_resume_sites, "expected matching.py to issue a Resume ownership-check query"
    assert all(site.scoped for site in matching_resume_sites), (
        "matching.py's Resume ownership-check query must be user-scoped (Requirement 1.4)"
    )
