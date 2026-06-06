# Monorepo layout and the apps-vs-packages split

## Introduction

This document explains how the project keeps every piece of its software in one
repository instead of scattering it across many, and how that single repository
is organized into deployable applications versus reusable libraries. A monorepo
(a single version-controlled repository that stores many separate projects side
by side) is the foundation that every other Phase 1 topic builds on, so this is
the place to start. It also introduces the idea of a workspace (a named project
folder, listed in a configuration file, that a package manager links into one
shared dependency graph) because the apps-vs-packages split is expressed through
workspaces.

**Learning outcomes** — after reading this document you will be able to:

- Describe what a monorepo is and why a single repository can hold many projects.
- Explain the difference between a deployable application and a shared library, and why each lives in its own top-level area.
- Identify the configuration files that declare the repository's workspaces and root-level tooling.
- Locate where a new application or a new shared library belongs within the layout.

Prerequisites: No prerequisites. This is the first document in the Foundation
and tooling track, written for a reader who has never seen a monorepo before.

## Problem it solves

A growing product is rarely one program. It tends to become a web front end, a
back-end service, some shared type definitions, machine-learning scripts, and
infrastructure definitions. The concrete problem is: where does all of that code
live, and how do you change two parts of it together without a painful dance of
version bumps and release coordination?

The common prior approach is the polyrepo (one separate repository per project).
In a polyrepo setup the front end lives in its own repository, the back end in
another, and shared code in a third. That arrangement creates real friction:

- A change that touches both a shared library and the application that uses it spans two repositories and two pull requests that must be merged in the right order.
- The shared library has to be published as a versioned artifact before the application can consume it, so even a one-line fix takes a publish-and-upgrade cycle.
- Tooling configuration (formatters, linters, type-checker settings) gets copied into every repository and slowly drifts out of sync.

A monorepo removes that friction by keeping all of those projects in one place,
where a single commit can change a library and its consumers together and one
shared set of tool configurations governs everything.

## Mental model

Think of a monorepo as one building with well-labelled rooms, rather than a
set of separate houses on different streets. Everyone shares the same front door
(one repository, one clone, one set of house rules), but each room has a defined
purpose.

When you need to decide where a piece of code goes, walk through these steps:

1. Ask whether the code is something you deploy and run on its own, or something other code imports. A thing you deploy is an application; a thing other code imports is a library.
2. If it is an application, it belongs in the applications area, with one folder per deployable unit.
3. If it is a shared library, it belongs in the packages area, with one folder per library.
4. If it is neither product code nor a library, but supporting material such as model-training scripts or infrastructure definitions, it belongs in its own dedicated top-level area so it does not weigh down the application folders.
5. Whichever area you choose, the project's root configuration files already know how to find it, because the root declares which folders are workspaces.

That five-step walk is the whole decision procedure. The rest of this document
fills in the details behind each step.

## How it works

A monorepo is a single repository whose contents are partitioned into
purpose-named top-level folders. The partitioning is a convention enforced by a
small amount of configuration rather than by any special tooling.

The most important convention is the split between two kinds of code:

- **Applications** are deployable units. Each one is built into something you run: a server process, a static site, a worker. An application is the end product; nothing else imports it.
- **Libraries** (often called packages) are reusable units of code that exist to be imported by applications or by other libraries. A library is never deployed on its own; it only ships as part of an application that depends on it.

Keeping these two kinds in separate top-level folders makes the dependency
direction obvious: applications depend on libraries, and libraries depend on
other libraries, but a library never depends on an application. That one-way
rule keeps the build graph acyclic and easy to reason about.

A workspace-aware package manager ties the folders together. The package manager
reads a configuration file that lists which folders are workspaces, then treats
every listed project as a member of one shared dependency graph. When one
workspace declares a dependency on another workspace in the same repository, the
package manager links them directly on disk instead of downloading a published
copy. The practical effect is that an edit to a shared library is visible to its
consumers immediately, with no publish step.

Finally, the repository root carries configuration that applies to everything:
the root manifest names the repository, pins the package-manager version, and
defines scripts that fan a single command out across every workspace. A shared
base configuration for the type checker and the formatter lives at the root too,
so each project inherits one consistent set of rules rather than maintaining its
own copy.

## MatchLayer Phase 1 usage

In MatchLayer the workspace list is declared in `pnpm-workspace.yaml` at the
repository root. It marks two globs as workspaces: every folder under the
applications area and every folder under the packages area.

Source: `pnpm-workspace.yaml`

```yaml
packages:
  - "apps/*"
  - "packages/*"
```

That single file is what creates the apps-vs-packages split. The `apps/`
directory holds deployable applications (the Next.js web front end and the
FastAPI back end in Phase 1), and the `packages/` directory holds shared
libraries (for example the generated shared TypeScript types). Two further
top-level areas sit outside the workspace globs on purpose: `ml/` holds Python
machine-learning pipelines and evaluation suites whose dependencies and
lifecycle differ from the application code, and `infra/` holds Docker and
deployment definitions that are reviewed and shipped differently from product
code. Keeping `ml/` and `infra/` out of the workspace list keeps them from being
linked into the JavaScript and TypeScript dependency graph, which they are not
part of.

The repository root also carries `package.json`, the root manifest. It is marked
private so it is never published, it pins the exact package-manager version, and
its scripts use `pnpm -r` to run a named script in every workspace at once.

Source: `package.json`

```json
{
  "name": "matchlayer",
  "private": true,
  "packageManager": "pnpm@9.15.9",
  "devDependencies": {
    "openapi-typescript": "^7.4.4",
    "openapi-zod-client": "^1.18.3",
    "prettier": "^3.3.3",
    "typescript": "^5.6.3"
  }
}
```

The dev dependencies declared here (the type checker, the formatter, and the
contract-generation tools) are installed once at the root and shared by every
workspace, rather than being repeated in each application. A shared base type
checker configuration lives alongside this manifest in `tsconfig.base.json`, and
each application extends it so the whole repository type-checks under one set of
rules.

## Common pitfalls

- **Mistake:** Putting a shared library inside the applications area (or a deployable application inside the packages area) because it was convenient at the time.
  **Symptom:** Another application cannot import the library cleanly, or the build tries to deploy something that was never meant to run on its own; dependency direction starts to look circular.
  **Recovery:** Move the folder to the area that matches its role — applications you deploy, libraries other code imports — and update the import paths. The apps-vs-packages rule is about role, not size.

- **Mistake:** Adding a new project folder but forgetting that the workspace globs in `pnpm-workspace.yaml` only cover the applications and packages areas.
  **Symptom:** The package manager does not pick up the new project, its dependencies are never installed, and repository-wide script runs skip it entirely.
  **Recovery:** Place the project under one of the declared workspace areas, or, if it genuinely belongs elsewhere, treat it like `ml/` and `infra/` — a deliberately non-workspace area with its own tooling — rather than expecting workspace behavior from it.

- **Mistake:** Copying formatter, linter, or type-checker configuration into an individual project instead of inheriting the shared root configuration.
  **Symptom:** One project formats or type-checks differently from the rest, and a file that passes locally fails in a sibling project or in continuous-integration checks.
  **Recovery:** Delete the duplicated settings and extend the shared root configuration (for the type checker, extend `tsconfig.base.json`); let the root be the single source of truth for cross-cutting rules.

- **Mistake:** Treating `ml/` or `infra/` as if it were a workspace and trying to import it from an application.
  **Symptom:** Imports fail to resolve, or the application build pulls in heavy machine-learning or infrastructure dependencies it should never ship.
  **Recovery:** Keep those areas decoupled — the back-end service consumes trained artifacts or calls a service, it does not import training code — and cross the boundary through a defined interface rather than a direct import.

## External reading

- [pnpm workspaces](https://pnpm.io/workspaces)
- [pnpm recursive commands (`pnpm -r`)](https://pnpm.io/cli/recursive)
- [Node.js: the package.json manifest and packages](https://nodejs.org/api/packages.html)
- [TypeScript handbook: project references](https://www.typescriptlang.org/docs/handbook/project-references.html)
