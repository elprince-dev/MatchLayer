# Corepack and the packageManager version pin

## Introduction

This document explains how a project guarantees that everyone working on it uses
the exact same version of its package manager (the tool that installs and manages
a project's dependencies), without each person installing that tool by hand. The
mechanism has two halves: a `packageManager` field in the root manifest that
records the one approved version, and Corepack (a tool shipped inside Node.js
that reads that field and runs the matching package-manager version on demand).
Together they turn "please install the right pnpm yourself" into something the
toolchain enforces automatically.

**Learning outcomes** — after reading this document you will be able to:

- Explain what the `packageManager` field declares and why a single pinned version matters.
- Describe what Corepack is and how it activates the declared package-manager version.
- Read the pinned version string and identify the package manager and exact version it names.
- Recognise the common version-mismatch mistakes and recover from them.

Prerequisites: this document builds on
[pnpm and pnpm workspaces](01-foundations-02-pnpm-and-workspaces.md), which introduces the package
manager being pinned, and
[The root package.json and shared tsconfig.base.json](01-foundations-05-root-package-and-tsconfig.md),
which introduces the root manifest that carries the pin.

## Problem it solves

Different versions of a package manager can resolve and install dependencies
slightly differently, write the lockfile in a different format, or change the
behavior of a command. The concrete problem is that if two contributors run two
different versions of the same package manager, they can produce different
installs from identical project files — the classic "works on my machine"
failure that is hard to trace because nothing in the repository changed.

The common prior approach was to write "please use version X of the package
manager" in a setup guide and rely on each person to install that version
globally on their own machine. That approach has real weaknesses:

- A globally installed tool is shared across every project on a machine, so upgrading it for one project silently changes it for all the others.
- Nothing checks that the installed version matches what the project expects, so a mismatch is discovered only as a confusing difference in install results.
- New contributors must perform a manual install step correctly before they can even begin, and an automated build environment needs the same careful setup.

A declared version pin plus an activation tool solve this by making the toolchain
itself responsible for running the correct version. The project states the
version once, and the tool supplies it on demand.

## Mental model

Think of the version pin as a note taped to a shared machine that reads "this job
must be run with tool model 9.15.9", and Corepack as an attendant who reads that
note and hands you exactly that model before you start — fetching it from the
supply room if it is not already on the bench. You never choose the version
yourself; the note decides and the attendant enforces.

When you run a package-manager command in a Corepack-enabled environment, the
steps are:

1. You invoke the package manager by name (for example, running its install command) without specifying any version.
2. Corepack intercepts the call and reads the `packageManager` field from the nearest project manifest to learn which version is required.
3. If that exact version is not already available locally, Corepack downloads it and caches it.
4. Corepack runs your command using that pinned version, regardless of any other version installed globally on the machine.
5. Every contributor and every automated build that runs the same command therefore uses the identical package-manager version.

That interception-and-enforcement in steps 2 through 4 is the whole idea. The
version lives in the project, not in each person's global setup.

## How it works

The `packageManager` field is a single line in a project's root manifest that
names one package manager and one exact version, written as `name@version` (for
example `somepm@1.2.3`). It is a declaration of intent: it does not install
anything by itself, it states which package-manager version this project is meant
to be used with.

Corepack is the piece that acts on that declaration. It is a small program
distributed inside Node.js (the runtime that executes JavaScript outside a
browser) whose job is to stand in front of the supported package managers. When
Corepack is enabled and you run one of those package managers, Corepack reads the
`packageManager` field, ensures the exact named version is present (downloading
and caching it the first time), and then runs your command with that version. A
different version installed globally on the machine is ignored, because Corepack
shims the command — it inserts itself as the thing that runs when you type the
package manager's name.

Pinning to an exact version, rather than a loose range, is deliberate. Because
the package manager is the very tool that produces the lockfile and resolves
dependencies, allowing it to vary would reintroduce exactly the
non-reproducibility the lockfile exists to prevent. A single exact version means
the tool that writes the lockfile is itself fixed, so the lockfile format and the
resolution behavior are stable across machines and over time. Upgrading is then a
deliberate, reviewable change: someone edits the one version string, and from
that commit onward everyone's Corepack activates the new version.

## MatchLayer Phase 1 usage

In MatchLayer the version pin lives in the `packageManager` field of the root
manifest, `package.json`, alongside the `engines` field that records the minimum
Node.js version:

Source: `package.json`

```json
  "engines": {
    "node": ">=24"
  },
  "packageManager": "pnpm@9.15.9",
```

The field names pnpm as the package manager and pins it to the exact version
`9.15.9`. Because Node.js 24 (required by the `engines` field above) ships
Corepack, a contributor enables Corepack once on their machine and then every
pnpm command they run inside the repository is served by version `9.15.9` — even
if they have a different pnpm installed globally. The same applies to the
automated build, which enables Corepack and therefore resolves dependencies with
the identical pinned version.

This pin is what makes the lockfile reproducible at the tool level: the lockfile
`pnpm-lock.yaml` is written in the format that pnpm `9.15.9` produces, and a
frozen-lockfile install in the automated build runs under that same version, so
there is no version-skew between the tool that wrote the lockfile and the tool
that reads it. Upgrading pnpm for the whole project is a one-line change to this
field, reviewed like any other change.

## Common pitfalls

- **Mistake:** Assuming a globally installed package manager will be used and never enabling Corepack, so the `packageManager` pin is ignored.
  **Symptom:** Commands run under whatever version happens to be installed globally, and two contributors with different global versions produce different installs despite the pin.
  **Recovery:** Enable Corepack once on the machine so it activates the pinned version, and rely on the in-repository pin rather than a global install.

- **Mistake:** Bumping the pinned version in `package.json` without regenerating the lockfile or telling the team.
  **Symptom:** The new package-manager version writes the lockfile in a slightly different way, producing lockfile churn or install differences that surprise other contributors.
  **Recovery:** Treat a version bump as a deliberate change: update the pin, regenerate the lockfile under the new version, and commit both together so everyone moves at once.

- **Mistake:** Specifying a loose range instead of an exact `name@version` in the `packageManager` field.
  **Symptom:** Different machines activate different patch or minor versions, reintroducing the tool-version drift the pin was meant to remove.
  **Recovery:** Pin one exact version string; upgrade by editing that single string in a reviewed commit rather than allowing a range to float.

- **Mistake:** Editing the `packageManager` field to a version that does not match the one the lockfile was generated with.
  **Symptom:** The mismatched tool version rewrites or rejects the committed lockfile, and frozen installs in the automated build fail.
  **Recovery:** Keep the pinned version and the lockfile in step — regenerate the lockfile whenever you intentionally change the pin, and never hand-edit either to paper over the mismatch.

## External reading

- [Node.js: Corepack](https://nodejs.org/api/corepack.html)
- [pnpm: installation via Corepack](https://pnpm.io/installation#using-corepack)
- [Node.js: the package.json manifest and packages](https://nodejs.org/api/packages.html)
