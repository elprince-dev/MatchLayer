# pnpm and pnpm workspaces

## Introduction

This document explains the package manager the project uses for all of its
JavaScript and TypeScript code, and the feature of that package manager that
ties many sub-projects together inside one repository. A package manager is a
tool that downloads, installs, and tracks the external code libraries a project
depends on. The package manager here is pnpm (a fast, disk-efficient package
manager for Node.js whose name stands for "performant npm"). pnpm adds a feature
called workspaces, where a workspace (a named project folder, listed in a
configuration file, that the package manager links into one shared dependency
graph) lets a single repository hold many projects that install and build
together.

**Learning outcomes** — after reading this document you will be able to:

- Explain what pnpm is and how its on-disk storage makes installs fast and space-efficient.
- Describe what a pnpm workspace is and how one configuration file turns a set of folders into a single dependency graph.
- Read the workspace declaration and the root scripts that fan one command out across every project.
- Recognize the most common workspace mistakes and recover from them.

Prerequisites: this document assumes you have read
[Monorepo layout](01-foundations-01-monorepo-layout.md), which introduces the single-repository
layout and the split between deployable applications and shared libraries that
workspaces are built to support.

## Problem it solves

A repository that holds many projects has to answer two awkward questions: how
do you install each project's dependencies without wasting time and disk space
re-downloading the same libraries over and over, and how do you keep two
projects in the same repository depending on each other without a slow
publish-and-reinstall cycle?

The common prior approach is a package manager that gives every project its own
flat, independent dependency folder. That arrangement has three concrete costs:

- The same library version is copied in full into every project that uses it, so disk usage balloons and installs are slow because identical files are downloaded and written many times over.
- A flat dependency folder lets code import a library it never declared (a "phantom dependency"), because everything a sibling pulled in is visible. The code works locally and then breaks elsewhere once that undeclared library is gone.
- Reproducing an install on another machine is unreliable unless a lockfile (a generated file that records the exact resolved version of every dependency) is honored exactly, and older tools made it easy to drift from it.

pnpm addresses all three: it stores each library version once on disk and links
it into every project that needs it, it refuses imports of undeclared
dependencies, and it writes a lockfile that a frozen install reproduces
byte-for-byte.

## Mental model

Think of pnpm's storage as a single shared warehouse for parts, with each
project getting a wiring diagram that points at the parts it ordered, rather
than each project keeping its own full copy of every part in its own garage. The
warehouse holds exactly one copy of each distinct part; many projects can point
at the same copy at the same time.

When you run an install in a workspace-enabled repository, the steps are:

1. The package manager reads the workspace configuration file to learn which folders in the repository are member projects.
2. For each member, it reads that member's dependency list and resolves every dependency to an exact version, recording the result in one shared lockfile.
3. It downloads any version not already in the global store (the shared warehouse), writing each file exactly once no matter how many projects want it.
4. It creates each member's local dependency folder out of links that point into the global store, so the files are shared on disk instead of copied.
5. When one member depends on another member of the same repository, it links the two directly, so an edit in one is visible to the other with no download step.

After those steps every project has the dependencies it declared, intra-repository
dependencies are wired together live, and the same versions can be reinstalled
anywhere from the lockfile.

## How it works

pnpm is built around a content-addressable store: a single directory on the
machine where every version of every downloaded package is kept exactly once,
addressed by a hash of its contents. When a project needs a package, pnpm does
not copy the package into the project; it creates a link from the project's
local dependency folder into that one stored copy. Because storage is keyed by
content, two projects that need the same version share the same files on disk,
and installing a version already present in the store needs no download at all.
This is what makes pnpm both fast and space-efficient.

pnpm also lays out a project's local dependency folder strictly. Older package
managers flatten every direct and indirect dependency into one level, which lets
code import libraries it never declared. pnpm instead keeps only a project's
declared dependencies directly reachable and tucks the rest away behind links.
Code can import what the project actually declared and nothing else, so an
undeclared dependency fails fast during development rather than mysteriously in
another environment.

Workspaces extend this model across a single repository. A workspace-aware
package manager reads a small configuration file that lists, usually as glob
patterns, which folders are member projects. It treats every listed project as
part of one dependency graph and produces a single lockfile for the whole
repository. Two behaviors follow from that:

- **Local linking.** When one member declares a dependency on another member of the same repository, the package manager links them directly on disk instead of fetching a published copy. A change in a shared library is visible to its consumers immediately, with no publish-and-reinstall step.
- **Recursive commands.** Because the package manager knows every member, it can run a named script in all members at once. A single command at the repository root can lint, type-check, test, or build every project, optionally in parallel.

The repository root also carries one manifest that names the repository, pins
the package-manager version so everyone uses the same one, and defines the
root-level scripts that fan out across the members. That manifest plus the
workspace configuration file are the entire setup; everything else is convention.

## MatchLayer Phase 1 usage

In MatchLayer the set of workspaces is declared in `pnpm-workspace.yaml` at the
repository root. It lists two glob patterns, so every folder under the
applications area and every folder under the packages area becomes a workspace
member.

Source: `pnpm-workspace.yaml`

```yaml
packages:
  - "apps/*"
  - "packages/*"
```

The `apps/*` glob makes each deployable application a member (the Next.js web
front end in Phase 1), and the `packages/*` glob makes each shared library a
member (the generated shared TypeScript types). pnpm reads this file, resolves
all members' dependencies into one `pnpm-lock.yaml` lockfile at the root, and
links every member's dependencies out of the shared store. When the web
application depends on the shared-types library, pnpm wires that link directly
on disk rather than fetching a published package.

The repository root also carries `package.json`, the root manifest. It pins the
exact pnpm version through the `packageManager` field so that every contributor
and every continuous-integration run resolves dependencies with the same tool,
and its scripts use pnpm's recursive flag to run one command across every
member. Here is the excerpt that does both:

Source: `package.json`

```json
  "packageManager": "pnpm@9.15.9",
  "scripts": {
    "lint": "pnpm -r --parallel run lint",
    "typecheck": "pnpm -r --parallel run typecheck",
    "test": "pnpm -r --parallel run test",
    "build": "pnpm -r --parallel run build",
    "codegen": "node packages/shared-types/scripts/codegen.mjs",
    "format": "prettier --check .",
    "format:write": "prettier --write ."
  },
```

The `-r` flag is pnpm's recursive switch: it runs the named script in every
workspace member that defines it. The `--parallel` flag lets those per-member
runs happen at the same time instead of one after another. So a single
`pnpm lint` at the root lints the front end and the shared library together,
and `pnpm test` runs every member's tests in one command. The `packageManager`
field is what corepack reads to activate the declared pnpm version, which keeps
the whole team on `pnpm@9.15.9` rather than whatever version each person happens
to have installed.

## Common pitfalls

- **Mistake:** Adding a new project folder somewhere the workspace globs in `pnpm-workspace.yaml` do not cover (the globs match only the applications and packages areas).
  **Symptom:** pnpm never installs the new project's dependencies, the project is missing from recursive runs, and an intra-repository dependency on it fails to resolve.
  **Recovery:** Place the project under one of the declared workspace areas, or extend the glob list in `pnpm-workspace.yaml` to include the new location, then reinstall so pnpm rebuilds the dependency graph.

- **Mistake:** Importing a library that a project never declared in its own dependency list, relying on it being present because another project pulled it in.
  **Symptom:** The import works on one machine but throws a "module not found" error elsewhere or after a clean install, because pnpm's strict layout does not expose undeclared dependencies.
  **Recovery:** Add the library to that project's own dependency list and reinstall, so the dependency is declared where it is used instead of borrowed from a sibling.

- **Mistake:** Installing with plain `pnpm install` in an automated build and letting it update the lockfile when the manifest and lockfile disagree.
  **Symptom:** Continuous-integration builds resolve different versions than a teammate did, and "works on my machine" version drift appears between runs.
  **Recovery:** Use `pnpm install --frozen-lockfile` in automation so the install fails loudly on any mismatch instead of silently rewriting `pnpm-lock.yaml`; commit lockfile changes deliberately.

- **Mistake:** Running each project's scripts by hand, one directory at a time, instead of the recursive root scripts.
  **Symptom:** A project is forgotten in a lint or test pass, so a problem slips through locally and is caught only later in a shared check.
  **Recovery:** Run the root scripts that use `pnpm -r` (for example `pnpm lint` or `pnpm test`) so every workspace member is covered by one command.

## External reading

- [pnpm motivation](https://pnpm.io/motivation)
- [pnpm workspaces](https://pnpm.io/workspaces)
- [The pnpm-workspace.yaml file](https://pnpm.io/pnpm-workspace_yaml)
- [pnpm recursive commands](https://pnpm.io/cli/recursive)
- [Node.js: the package.json manifest and packages](https://nodejs.org/api/packages.html)
