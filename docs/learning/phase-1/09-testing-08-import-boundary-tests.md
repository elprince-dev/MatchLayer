# Import-boundary tests

## Introduction

An import-boundary test is an automated test that checks _which modules are allowed to import which other modules_ and fails the build when a forbidden dependency appears. A module is a single source file, and an import is the statement one file uses to pull in code from another. Most tests check what code _does_ when it runs; an import-boundary test checks how code is _wired together_ before it runs, by reading the source and inspecting its import statements. This document explains why a codebase deliberately forbids some imports, how a test can enforce those rules mechanically, and where this project keeps such a test.

This Topic_Doc is written for a reader who has never seen a test that examines source code instead of running it, so every term is introduced from scratch.

Learning outcomes — after reading this document you will be able to:

- Explain what a module-dependency boundary is and why a project forbids certain imports on purpose.
- Describe the difference between checking import rules by reading text and checking them by parsing the code into a tree structure, and say why the tree approach avoids false alarms.
- Explain how an import-boundary test fails the build with a message that names the offending file.
- Locate, in this project, the test that enforces the apps-versus-packages and model-versus-serving separations, and name the specific rules it locks down.

Prerequisites — read these Topic_Docs first:

- [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md) — explains the top-level directory split this test defends.
- [Separating model code from serving code and the Scorer_Version identifier](08-matching-09-ml-vs-api-separation-and-scorer-version.md) — explains the one-way boundary between model-building code and request-serving code that this test enforces.

## Problem it solves

A large codebase is held together by thousands of import statements. Left unmanaged, those imports tend to tangle: any file imports any other file because doing so is convenient in the moment. The tangle creates concrete, costly problems.

Consider a project with two kinds of code kept apart on purpose. One kind is a small, dependency-light core that should stay testable in isolation. The other kind is the heavy web-and-storage layer with many third-party dependencies. The whole point of separating them is that the core never reaches for the web framework, the database client, or application configuration. The first time someone adds `import` of the web framework into a core module to grab a convenient helper, that separation quietly dies. Nothing breaks immediately, so nobody notices — until the core can no longer be unit-tested without spinning up the whole application, or until a security-sensitive library leaks into a module that was supposed to never touch it.

The common prior approach is to write the rule down in a style guide and rely on human code review to catch violations. That approach fails in predictable ways:

- Reviewers are inconsistent. A forbidden import slips through whenever the reviewer is busy, unfamiliar with the rule, or looking at a large diff.
- The rule is invisible at the moment it matters. A developer adding an import has no immediate signal that the import crosses a forbidden boundary.
- A naive automated fix — searching the text of each file for a banned word like the library name — produces false alarms, because the same word appears in comments, in documentation strings, and in string literals that merely _describe_ the rule.

An import-boundary test removes the guesswork. It encodes the dependency rules as code, runs on every change, and fails with a precise message naming the file that broke a rule. The rule stops being a hopeful guideline and becomes an enforced invariant.

## Mental model

Think of a building with a strict one-way security checkpoint between two wings. The **research wing** holds sensitive equipment and may send finished reports out to the **operations wing**, but operations staff may never walk back into research mid-shift. A guard at the checkpoint does not read the _contents_ of what people carry; the guard only checks the _direction_ each person is walking and which badge they hold. Someone walking the wrong way is stopped at the door, by name, before they cause harm.

An import-boundary test is that guard. It does not judge what your functions compute. It inspects the "doorways" — the import statements — and stops any import that walks in a forbidden direction or into a restricted room.

Here is the walkthrough the test performs, step by step:

1. It finds every source file in the area it is responsible for guarding.
2. It reads each file and parses it into a structured form that exposes the import statements precisely, rather than treating the file as a flat block of text.
3. For each file, it asks: does this file import something it is not allowed to import, or does a restricted file appear outside its single permitted home?
4. It collects every violation into a list, recording the offending file and the construct found there.
5. If the list is non-empty, the test fails and prints the offenders by name so the regression explains itself; if the list is empty, the boundary held and the test passes.

The key insight in step 2 is that the guard inspects _real_ import statements, not text that merely looks like one. A line in a comment that says "do not import the web framework here" mentions the framework by name but is not an import — and the test must not flag it.

## How it works

The technique rests on three ideas: a dependency graph, an allowlist of edges, and static analysis of source code.

**The dependency graph.** Every module that imports another module creates a directed edge from the importer to the imported. Taken together, all those edges form a graph. A healthy architecture has a _shape_ to this graph: lower-level building blocks at the bottom, higher-level features at the top, and edges that point in one consistent direction. A dependency boundary is a rule that says "edges of this kind are forbidden" — for example, "nothing in the core layer may point at the framework layer," or "the serving side may read finished data files produced by the training side, but no serving module may import a training module." Forbidding an edge is how an architect keeps two areas decoupled so they can change, deploy, and be tested independently.

**The allowlist of edges.** Some restricted dependencies are not banned outright; they are confined to exactly one place. A sensitive third-party library — say, one that signs security tokens — might be allowed in one designated module and forbidden everywhere else, so that all use of it funnels through a single audited file. The rule then has two halves: the designated file _may_ import the library, and every other file _may not_. The test encodes both halves.

**Static analysis instead of text search.** To check imports reliably, the test reads the source _without running it_ and examines its structure. The weak way to do this is a text search: scan each file's characters for the library's name. That is fast but wrong, because the name also appears in comments, in documentation strings, and in plain strings that describe the rule. The strong way is to parse each file into an Abstract Syntax Tree (AST) — a tree-shaped representation of the code's grammar, where an import statement is a distinct kind of node separate from comments and string literals. Walking the tree, the test can ask "is this node an actual import of the forbidden library?" and ignore every mention that is only prose. Standard programming languages ship a parser that produces this tree, so the test does not need to invent its own.

A second subtlety the tree handles cleanly is telling apart two names that look similar. A top-level package and a same-named sub-package living inside another package are different things. A text search sees the shared name and conflates them; a tree walk reads the full dotted path of each import and distinguishes "the top-level training package" from "an internal adapter that happens to share a word in its name." Forbidding one while permitting the other is therefore precise.

Putting it together: the test enumerates the files it guards, parses each into a tree, walks the tree to find the real import statements (and, where relevant, specific function calls), compares each against the allowlist, and aggregates violations. Because it runs as an ordinary test, it executes automatically on every change and blocks a merge when a boundary is crossed. The failure message names the file and the construct, so the person who introduced the bad import learns exactly what to move and where.

## MatchLayer Phase 1 usage

In this project the import-boundary checks live in `apps/api/tests/unit/test_import_boundaries.py`. The file walks every Python source file under the backend package and asserts a set of exclusivity rules using the standard-library `ast` module — the Application Programming Interface (API) that parses Python source into a tree — rather than a plain text search, so a docstring that names a forbidden library cannot trip a false alarm.

The first group of rules confines three sensitive concerns to one file each. Each rule names the single module allowed to touch the concern:

Source: `apps/api/tests/unit/test_import_boundaries.py`

```python
_JWT_ALLOWED = "core/security/jwt.py"
_ARGON2_ALLOWED = "core/security/passwords.py"
_COOKIES_ALLOWED = "core/security/cookies.py"
```

The token-signing library may appear only in the `jwt.py` module, the password-hashing library only in `passwords.py`, and the code that sets the authentication cookies only in `cookies.py`. The check for the token library walks every package source, skips the one allowed file, and records any other file that imports it — then asserts the offender list is empty with a message that names the offenders:

Source: `apps/api/tests/unit/test_import_boundaries.py`

```python
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
```

The second group enforces the model-versus-serving separation described in the prerequisite Topic_Doc. The serving application lives under `apps/api/` and the model-building and data code lives under the top-level `ml/` tree (which holds the build pipeline `ml/pipelines/build_skill_lexicon.py` and the source-of-truth artifact `ml/lexicon/skill_lexicon.v1.json`). The rule is that no module in the serving package may import that top-level training tree at runtime. The constant the test guards against is the bare top-level package name:

Source: `apps/api/tests/unit/test_import_boundaries.py`

```python
_REPO_ROOT_ML_TOP_LEVEL = "ml"
```

The guard walks every package source and flags any file that imports it. Note that the in-package adapter (whose dotted import path starts with the backend package name) is a different module and is deliberately _not_ caught, which is exactly the look-alike distinction the tree-based check makes precise:

Source: `apps/api/tests/unit/test_import_boundaries.py`

```python
    offenders: list[str] = []
    for path in _iter_package_sources():
        tree = _parse(path)
        if _imports_module(tree, _REPO_ROOT_ML_TOP_LEVEL):
            offenders.append(_relpath(path))
```

A third rule keeps the framework-free scoring core (under the `scoring/` package, whose engine is `apps/api/src/matchlayer_api/scoring/scorer.py`) decoupled from the web and configuration layers. The test marks which top-level names a scoring module is allowed to import: the numerical library and the scoring package's own siblings, and nothing else.

Source: `apps/api/tests/unit/test_import_boundaries.py`

```python
_FIRST_PARTY_TOP_LEVEL = "matchlayer_api"
_SCORING_PACKAGE = "matchlayer_api.scoring"
```

Because these are unit tests in the backend suite, they run with the rest of the suite under the project's test runner and block a merge whenever a forbidden import is introduced.

## Common pitfalls

- **Mistake:** Enforcing the import rules with a text search (a plain substring scan of each file) instead of parsing the source into a tree.
  **Symptom:** The test fails on a file that only _mentions_ the forbidden library in a comment, a documentation string, or a descriptive string literal, even though the file never actually imports it — a false alarm that erodes trust in the check.
  **Recovery:** Parse each file into an Abstract Syntax Tree and inspect import nodes (and, where needed, call nodes) rather than raw characters, so prose that merely names a library is ignored.

- **Mistake:** Confusing a forbidden top-level package with a permitted in-package module that shares part of its name.
  **Symptom:** The guard either misses a real violation or flags a legitimate import, because it compares on a shared word rather than the full dotted import path.
  **Recovery:** Compare the complete dotted module path: forbid the bare top-level name (and its dotted sub-paths) while explicitly permitting the in-package module whose path begins with the backend package name.

- **Mistake:** Adding a convenient import that crosses a boundary — for example, importing the web framework or application configuration into the framework-free core — to save a few lines.
  **Symptom:** The import-boundary test fails in continuous integration naming the offending file, or the core can no longer be imported or unit-tested on its own without standing up the whole application.
  **Recovery:** Remove the boundary-crossing import and pass the value the core needed in as an explicit argument from the adapter layer, restoring the one-way dependency direction.

- **Mistake:** Treating a failing boundary test as noise and weakening the rule (deleting the assertion or adding the offender to the allowlist) instead of fixing the architecture.
  **Symptom:** The test passes again but the decoupling it protected is gone, and the original problem — a bloated, untestable, or insecure module — quietly returns.
  **Recovery:** Revert the rule change, move the offending code so the import is no longer needed, and keep the allowlist limited to the genuinely sanctioned single home for each restricted concern.

## External reading

- [Python `ast` — Abstract Syntax Trees](https://docs.python.org/3/library/ast.html)
- [Python language reference: the import system](https://docs.python.org/3/reference/import.html)
- [Python `sys.stdlib_module_names`](https://docs.python.org/3/library/sys.html#sys.stdlib_module_names)
- [pytest documentation](https://docs.pytest.org/en/stable/)
