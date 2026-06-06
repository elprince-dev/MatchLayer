# uv — a fast Python package and project manager

## Introduction

This document explains uv, the tool the project uses to install and manage the
Python code that powers its backend. A package manager (a program that reads a
list of the libraries your project depends on, works out exactly which versions
fit together, downloads them, and installs them into an isolated location) is
the part of the toolchain that turns "this project needs FastAPI and SQLAlchemy"
into a working set of installed code. uv is one such package manager, written in
the Rust programming language for speed, that also manages the project's Python
version and its virtual environment so a single tool covers the whole job.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a Python package manager does and why a project needs one.
- Describe the difference between the human-edited dependency manifest and the machine-generated lockfile, and why both are committed to the repository.
- Read the project-metadata table and the pinned dependency ranges that uv resolves.
- Recognise the most common uv mistakes and recover from them.

Prerequisites: this document builds on the repository layout, so read
[Monorepo layout](01-foundations-01-monorepo-layout.md) first to understand where the backend
application lives inside the wider repository. No other prerequisites are
assumed.

## Problem it solves

A Python project is almost never self-contained. It pulls in third-party
libraries, those libraries pull in further libraries of their own, and every one
of them has a range of versions that may or may not work together. The concrete
problem is: given a short list of the libraries you actually care about, how do
you arrive at one exact, repeatable set of installed versions that works the
same on your machine, on a teammate's machine, and on a continuous-integration
runner?

The common prior approach stacked several single-purpose tools on top of each
other. You created an isolated environment with one tool (`venv` or
`virtualenv`), installed packages with `pip` reading a hand-maintained
`requirements.txt`, and — if you wanted reproducibility — layered a third tool
such as `pip-tools` or Poetry on top to "compile" loose version ranges into
exact pins. That arrangement had real friction:

- Each tool was separate, so a newcomer had to learn and install several programs before the project would run at all.
- A plain `requirements.txt` records what you asked for, not the full resolved tree, so two installs weeks apart could quietly pick up different transitive versions.
- Resolving a large dependency tree with the older tooling was slow, sometimes minutes, which made routine installs and clean continuous-integration runs painful.

uv collapses that stack into one fast program: it creates the virtual
environment, resolves the full dependency tree, writes an exact lockfile, and
installs from it — and it does the resolution in a fraction of the time the
older tools took.

## Mental model

Think of two documents you already understand: a shopping list and an itemised
receipt. The shopping list is what you wrote before going to the store — "milk,
bread, eggs" — loose and human-friendly, with no brands or prices. The receipt
is what you get on the way out — every exact product, its exact price, the store,
the date — a precise record of what actually happened that anyone could use to
reproduce the same basket later.

A package manager works with the same two documents:

1. You write a short "shopping list" of the direct libraries your project needs, each with an allowed range of acceptable versions.
2. The package manager reads that list, then solves a puzzle: it finds one exact version of every library — including the libraries those libraries need — such that all the version ranges are satisfied at once.
3. It writes the solved result to a precise "receipt": a lockfile naming every package, its exact version, and a cryptographic hash of the files it downloaded.
4. It installs that exact set into an isolated environment so the project's libraries never collide with other projects or with the system Python.
5. Later, on any machine, it can skip the puzzle entirely and install straight from the receipt, guaranteeing an identical result.

Hold onto that shopping-list-versus-receipt picture. Everything below is a more
precise version of those five steps.

## How it works

A package manager sits between two inputs and produces one installed
environment. The first input is a **manifest**: a human-edited file, named
`pyproject.toml`, that declares the project's direct dependencies as version
_ranges_ (for example, "version 2 or newer, but less than version 3"). The
manifest is written in Tom's Obvious, Minimal Language (TOML), a text-based
configuration format designed to be easy for people to read and edit. The
project's identity and dependency table follow Python Enhancement Proposal (PEP)
621, the agreed standard for describing a Python project's metadata inside
`pyproject.toml`, so any standards-compliant tool can read it.

The second artifact is a **lockfile** (a machine-generated file that records the
exact resolved version of every package, direct and indirect, together with a
hash of its contents). Its job is reproducibility: the manifest says what is
_acceptable_, while the lockfile says what was _chosen_. Because the lockfile
pins exact versions and hashes, an install that reads it produces a
byte-identical environment every time, and the hashes let the tool detect a
tampered or corrupted download.

Between manifest and lockfile sits the **resolver**. Resolution is a constraint
problem: the tool must pick one version of every required package so that every
declared range is satisfied simultaneously, including the ranges declared by
dependencies of dependencies. This is the slow, puzzle-solving step, and it is
where a Rust implementation pays off — the work is parallelised and heavily
optimised, so resolving a large tree takes a moment rather than minutes.

The installed code lives in a **virtual environment** (an isolated directory
holding one project's Python interpreter link and its installed libraries, kept
separate from the system-wide Python and from other projects). Isolation is what
stops one project's "version 2 of a library" from breaking another project that
needs "version 1". uv creates and manages this environment for you, and it keeps
a single global cache of downloaded packages so that installing the same version
again is near-instant and uses links instead of recopying files.

A few recurring operations round out the model. A **sync** brings the
environment into exact agreement with the lockfile, adding what is missing and
removing what no longer belongs. A **frozen** install is a stricter sync that
refuses to change the lockfile at all and fails if the manifest and lockfile
have drifted apart — exactly the behaviour you want on a continuous-integration
runner, where a surprise dependency change should stop the build rather than be
silently accepted. The `requires-python` field in the manifest records which
interpreter versions the project supports, so the resolver only ever picks
package versions compatible with that floor.

## MatchLayer Phase 1 usage

The backend application's dependencies are declared in its manifest at
`apps/api/pyproject.toml`. The opening project table names the package, its
version, and — importantly — the `requires-python` floor that uv enforces during
resolution:

Source: `apps/api/pyproject.toml`

```text
[project]
name = "matchlayer-api"
version = "0.0.0"
requires-python = ">=3.13"
readme = "README.md"
license = { text = "MIT" }
```

The direct runtime dependencies follow as a list of version ranges. Each entry
gives a lower bound (a known-good current minor release) and an exclusive upper
bound at the next major release, which pins the major version while still
allowing compatible bug-fix updates:

Source: `apps/api/pyproject.toml`

```text
dependencies = [
    "fastapi>=0.133,<0.140,!=0.136.3",
    "uvicorn[standard]>=0.30,<0.40",
    "pydantic>=2.9,<3.0",
    "pydantic-settings>=2.6,<3.0",
]
```

Development-only tools live in a separate group so they are installed for
contributors but never shipped as runtime dependencies. uv reads this
`[dependency-groups]` table by default when it syncs the environment:

Source: `apps/api/pyproject.toml`

```text
[dependency-groups]
dev = [
    "ruff>=0.7,<1.0",
    "mypy>=1.13,<2.0",
    "pytest>=8.3,<9.0",
]
```

The resolved "receipt" for all of the above is committed alongside the manifest
at `apps/api/uv.lock`. Its header records the lockfile format version and the
same Python floor as the manifest, and the body lists every package — direct and
transitive — at one exact version:

Source: `apps/api/uv.lock`

```text
version = 1
revision = 3
requires-python = ">=3.13"
```

Because both `apps/api/pyproject.toml` and `apps/api/uv.lock` are committed,
anyone can reproduce the backend's exact environment with a frozen sync, and
continuous-integration runs install from the lockfile rather than re-resolving,
so a build only ever uses the versions that were reviewed and committed.

## Common pitfalls

- **Mistake:** Editing the manifest to add or bump a dependency but forgetting to update and commit the lockfile in the same change.
  **Symptom:** A frozen install fails complaining that the manifest and lockfile are out of sync, or a teammate's environment differs from yours because their install still reads the old lockfile.
  **Recovery:** Re-run the lock/sync step so the lockfile reflects the manifest, then commit both files together; treat the manifest and lockfile as a single inseparable pair in every change.

- **Mistake:** Installing packages straight into the environment by hand instead of declaring them in the manifest, so the dependency exists on your machine but nowhere in the committed files.
  **Symptom:** The code runs locally but fails with an import error in continuous integration or on a teammate's machine, because the package was never recorded as a dependency.
  **Recovery:** Add the dependency to the manifest with an appropriate version range, regenerate the lockfile, and commit both; let the manifest be the single source of truth for what the project depends on.

- **Mistake:** Hand-editing the lockfile to change a pinned version, treating it like an ordinary text file.
  **Symptom:** The hashes no longer match the named versions, and the next resolve or verified install reports a hash mismatch or overwrites your edit.
  **Recovery:** Never edit the lockfile by hand; change the version range in the manifest instead and let the tool re-resolve, which updates the version and its hash together.

- **Mistake:** Depending on a Python version below the project's declared `requires-python` floor.
  **Symptom:** Resolution fails to find a compatible set of versions, or the environment will not build because the interpreter is too old for the pinned packages.
  **Recovery:** Install a Python interpreter that satisfies the declared floor (or raise the floor deliberately in the manifest and re-resolve), then sync the environment again.

## External reading

- [uv documentation](https://docs.astral.sh/uv/)
- [uv: working on projects](https://docs.astral.sh/uv/guides/projects/)
- [uv: locking and syncing dependencies](https://docs.astral.sh/uv/concepts/projects/sync/)
- [Python tutorial: virtual environments and packages](https://docs.python.org/3/tutorial/venv.html)
