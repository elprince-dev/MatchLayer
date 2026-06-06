# Lockfiles and frozen-lockfile installs

## Introduction

This document explains the generated files that record the exact versions of
every dependency a project installs, and the install mode that refuses to change
them. A dependency is an external code library a project relies on; a lockfile
(a generated file that records the precise resolved version of every dependency,
direct and indirect, so the same set can be reinstalled later) is how a project
remembers exactly what was installed. A frozen-lockfile install is an install run
in a mode that installs precisely what the lockfile records and fails instead of
updating it. Together they are what makes an install reproducible — meaning the
same dependency versions appear on every machine and in every automated run.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a lockfile is and how it differs from the human-edited list of dependencies.
- Describe why a frozen-lockfile install protects automated builds from silent version drift.
- Read the top of a real lockfile and the install commands that consume it in frozen mode.
- Recognise the common lockfile mistakes and recover from them.

Prerequisites: this document builds on
[pnpm and pnpm workspaces](01-foundations-02-pnpm-and-workspaces.md), which introduces the
JavaScript package manager and its lockfile, and
[uv, a fast Python package manager](01-foundations-03-uv-python-package-manager.md), which
introduces the Python package manager and its lockfile. It also assumes the
single-repository layout from
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md).

## Problem it solves

A project's human-written list of dependencies usually records loose version
ranges — "version 7 or any newer compatible release" — rather than one exact
version. That looseness is convenient when you first add a library, but it
creates a concrete problem: two installs of the same project, run at different
times or on different machines, can resolve those ranges to different actual
versions. Code that worked yesterday breaks today because a dependency released a
new version overnight, and the failure is hard to trace because nothing in the
project's own files changed.

The common prior approach was to install from the loose ranges every time and
hope the resolved versions stayed stable, sometimes writing exact versions into
the dependency list by hand. That approach has three weaknesses:

- Indirect dependencies — the libraries your libraries depend on — are not listed anywhere you control, so their versions drift freely even when your direct versions are pinned.
- Pinning every version by hand in the dependency list is tedious and erases the useful information about which range was actually intended.
- Nothing forces an automated build to install the same versions a developer tested with, so "works on my machine" differences slip through.

A lockfile solves this by recording the exact resolved version of every
dependency — direct and indirect — the moment they are resolved. A
frozen-lockfile install then guarantees that every later install reproduces that
exact set, or fails loudly if it cannot.

## Mental model

Think of the loose dependency list as a shopping list that says "a dozen eggs,
any brand", and the lockfile as the itemised receipt from the one shopping trip
that recorded the exact brand, size, and lot number of everything actually
bought. Anyone handed the receipt can buy the identical items again. A
frozen-lockfile install is the rule "buy exactly what the receipt says, and if
any item is unavailable or the receipt disagrees with the shopping list, stop and
report it rather than substituting something else".

When an automated build runs a frozen install, the steps are:

1. The package manager reads the lockfile to learn the exact version of every dependency that should be installed.
2. It checks that the lockfile is consistent with the human-written dependency list — that the locked versions still satisfy the declared ranges.
3. If they disagree, the install stops immediately with an error instead of resolving fresh versions or rewriting the lockfile.
4. If they agree, it installs precisely the versions the lockfile names, fetching each from storage or a registry as needed.
5. The result is byte-for-byte the same dependency set the lockfile recorded, so the build runs against exactly what was tested.

That stop-on-disagreement behavior in step 3 is the entire point of frozen mode.
A normal, non-frozen install would instead quietly update the lockfile, which is
the right behavior for a developer adding a dependency but the wrong behavior for
an automated build.

## How it works

A lockfile is generated, not hand-written. When you add or update a dependency,
the package manager resolves every version constraint into one concrete version,
walks the full tree of indirect dependencies, resolves those too, and writes the
entire resolved set into the lockfile along with integrity information such as a
content hash for each package. The lockfile therefore captures far more than the
human-written list does: it records the indirect dependencies the list never
mentions, and it pins each to a single version.

Two install modes use that file differently:

- A **normal install** treats the lockfile as a starting point. If the human-written dependency list has changed so that the lockfile no longer satisfies it, the package manager resolves the difference, installs the result, and rewrites the lockfile to match. This is what you want while developing, because adding a library should update the lockfile.
- A **frozen install** treats the lockfile as authoritative and read-only. It installs exactly what the lockfile records and, if the lockfile and the dependency list disagree, it exits with an error instead of changing anything. This is what you want in an automated build, because the build should fail rather than silently install untested versions.

The reason to commit the lockfile to version control is that it is the shared
record every environment installs from. A developer commits both the changed
dependency list and the regenerated lockfile together, so a reviewer sees exactly
which versions changed, and every later frozen install reproduces that reviewed
set. Because the lockfile includes integrity hashes, a frozen install can also
detect if a package's published contents changed unexpectedly, which is a basic
defence against tampering in the supply chain (the path a dependency travels from
its author to your build).

Each package manager has its own lockfile format and its own flag that selects
frozen mode, but the idea is identical across ecosystems: one generated file
records the exact resolved set, and one install mode reproduces it without
changes.

## MatchLayer Phase 1 usage

MatchLayer uses two package managers, so it has two lockfiles, each committed at
the location its package manager expects. The JavaScript and TypeScript side uses
pnpm, whose lockfile is `pnpm-lock.yaml` at the repository root. Its header
records the lockfile format version and the resolver settings:

Source: `pnpm-lock.yaml`

```yaml
lockfileVersion: "9.0"

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false
```

The Python side uses uv, whose lockfile is `apps/api/uv.lock`, committed
alongside the back-end service it locks. Its header records the format version
and the Python version the lock was resolved for:

Source: `apps/api/uv.lock`

```text
version = 1
revision = 3
requires-python = ">=3.13"
```

Both files are generated by their package managers and committed to version
control, never edited by hand. The repository's continuous-integration pipeline
(the automated checks that run on every change, defined in
`.github/workflows/ci.yml`) installs from both in frozen mode so a build can
never silently use versions other than the committed ones. The JavaScript
install step runs pnpm's frozen flag:

Source: `.github/workflows/ci.yml`

```yaml
- name: Install JS deps (frozen)
  run: pnpm install --frozen-lockfile
```

and the Python install step runs uv's frozen flag:

Source: `.github/workflows/ci.yml`

```yaml
- name: Install Python deps (frozen)
  working-directory: apps/api
  run: uv sync --frozen
```

`pnpm install --frozen-lockfile` installs exactly what `pnpm-lock.yaml` records
and fails if the manifest and lockfile disagree; `uv sync --frozen` does the same
for `apps/api/uv.lock`. Because both run in the automated pipeline, a change that
updates a dependency without committing the regenerated lockfile is caught there
rather than reaching anyone else's machine.

## Common pitfalls

- **Mistake:** Changing the human-written dependency list and committing it without committing the regenerated lockfile in the same change.
  **Symptom:** The automated build's frozen install fails because the lockfile no longer satisfies the dependency list, reporting a mismatch between the two.
  **Recovery:** Run a normal install locally so the package manager regenerates the lockfile, then commit the updated dependency list and lockfile together.

- **Mistake:** Running a plain, non-frozen install in an automated build, letting it rewrite the lockfile on the fly.
  **Symptom:** The build resolves different versions than were tested and reviewed, so a problem appears only in the build and "works on my machine" drift creeps in between runs.
  **Recovery:** Use the frozen flag in automation (`pnpm install --frozen-lockfile`, `uv sync --frozen`) so the install reproduces the committed versions or fails loudly.

- **Mistake:** Editing the lockfile by hand to bump or pin a version.
  **Symptom:** The file's integrity information no longer matches its contents, and the next install either rejects the file or undoes the manual edit.
  **Recovery:** Never edit a lockfile directly; change the human-written dependency list and let the package manager regenerate the lockfile, then commit the result.

- **Mistake:** Adding a lockfile to the ignore list so it is not committed, treating it as a disposable build artifact.
  **Symptom:** Each environment resolves its own versions, frozen installs have nothing authoritative to reproduce, and reproducibility is lost.
  **Recovery:** Commit the lockfile to version control so it is the shared record every install reproduces, and review its changes like any other code.

## External reading

- [pnpm install (and the --frozen-lockfile flag)](https://pnpm.io/cli/install)
- [The pnpm-lock.yaml lockfile](https://pnpm.io/git#lockfiles)
- [uv: locking and syncing an environment](https://docs.astral.sh/uv/concepts/projects/sync/)
- [uv: the uv.lock lockfile](https://docs.astral.sh/uv/concepts/projects/layout/#the-lockfile)
- [GitHub Actions: workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions)
