# The root package.json and shared tsconfig.base.json

## Introduction

This document explains the two configuration files that sit at the very top of
the repository and set rules for the whole codebase: the root manifest (the
top-level `package.json` file that names the repository, declares the tooling
shared across every project, and defines the commands you run from the root) and
the shared base configuration for the type checker (the `tsconfig.base.json`
file, a single set of compiler options that every TypeScript project in the
repository inherits). A manifest is a small structured file that describes a
project to a package manager — its name, its dependencies, and the named
commands it can run. Both files live at the root of a monorepo (a single
version-controlled repository that holds many separate projects side by side),
so a change to either one reaches every project at once instead of being copied
into each project by hand.

**Learning outcomes** — after reading this document you will be able to:

- Explain what the root manifest is and why a repository-wide manifest pins tooling and defines fan-out commands rather than holding application code.
- Describe how a shared base configuration for the type checker lets many projects type-check under one set of rules.
- Read the root manifest's scripts, version pins, and shared development dependencies, and the base compiler options the projects extend.
- Recognise the common mistakes around editing the wrong configuration file and recover from them.

Prerequisites: this document builds on
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md), which
introduces the single-repository layout, and
[pnpm and pnpm workspaces](01-foundations-02-pnpm-and-workspaces.md), which explains the package
manager and how one configuration file ties many projects into one workspace (a
named project folder that the package manager links into one shared dependency
graph).

## Problem it solves

A repository that holds many projects has to decide where repository-wide
settings and commands live. Two concrete problems show up quickly. First, every
project needs the same code formatter, the same type checker, and the same
version of the package manager, or contributors produce inconsistent output that
fails on someone else's machine. Second, you want one command — run from the top
of the repository — that lints or tests every project at once, instead of
walking into each project folder and running the same command by hand.

The common prior approach is to copy the tooling configuration and the command
scripts into every project. That arrangement has real costs:

- The formatter, linter, and type-checker settings are duplicated in each project, and the copies slowly drift apart, so a file that passes in one project fails in another.
- Each project pins its own version of the package manager and its own version of shared tools, so two contributors can end up resolving dependencies with different tool versions.
- There is no single place to run a repository-wide command, so a project is easy to forget in a lint or test pass.

A coordinating root manifest plus a shared base type-checker configuration
remove that duplication. The root manifest pins one package-manager version,
holds the shared development tooling once, and defines commands that fan out to
every project. The base type-checker configuration holds the strict options once
and every project extends it, so the whole repository type-checks under one set
of rules.

## Mental model

Think of the root manifest as the directory board and house rules posted at the
main entrance of a single building, and the base type-checker configuration as
the master style guide every department copies its letterhead from. The board at
the entrance does not contain any department's actual work; it lists who is
inside and the rules everyone follows. The style guide is written once, and each
department's documents inherit it and then change only the few details specific
to that department.

When a project's type checker starts up, the inheritance works like this:

1. The project has its own small configuration file that declares it "extends" the shared base configuration at the root.
2. The type checker first reads the shared base configuration to load the strict options that apply everywhere.
3. It then reads the project's own configuration and layers any project-specific fields on top, overriding or adding to the inherited values.
4. The merged result is the effective configuration the type checker uses for that project, so the strict rules come from one shared source while each project keeps only its differences.

That four-step merge is the whole idea behind a shared base configuration. The
root manifest works on the same principle for commands: one definition at the
root, reused by every project.

## How it works

A package manifest is a structured text file, written in JavaScript Object
Notation (JSON) — a plain-text format for structured data — that sits at the
root of a project and describes it to a package manager. It records the
project's name, its version, the libraries it depends on, and a set of named
scripts (short command aliases the package manager can run). An ordinary
project's manifest mostly lists the libraries that project needs.

At the top of a multi-project repository, one manifest plays a different,
coordinating role rather than describing a single deployable program:

- It is marked private so the package manager never tries to publish it, because the repository root is not itself a publishable library.
- It pins the exact package-manager version, so every contributor and every automated build resolves dependencies with the same tool.
- It declares the development tooling shared by all projects — the formatter, the type checker, and any code-generation tools — once, installed at the root and reused everywhere.
- It defines scripts that fan a single command out to every project in the repository, so one command at the root can lint, type-check, test, or build the whole codebase.

The shared base configuration for the type checker follows the inheritance idea.
A type checker for a typed language reads a configuration file that turns
individual checks on or off. Putting the strict options in one base file and
having each project's own configuration extend it means the rules are defined
once. The most important of those options is strict mode — a single switch that
turns on a whole family of stricter type checks together, such as rejecting
values that might be null or undefined. Extending one base file keeps every
project on the same strict settings; a project changes only the handful of
fields unique to it, such as where its source files live or how its module
imports resolve.

One more job the base configuration commonly does is declare import aliases: a
mapping from a friendly import name to a real folder on disk. An alias lets code
in any project import a shared library by a stable, readable name instead of a
long relative path, and the type checker resolves that name to the library's
source using the mapping.

## MatchLayer Phase 1 usage

In MatchLayer the root manifest is `package.json` at the repository root. It does
not contain application code; it coordinates the JavaScript and TypeScript side
of the monorepo. The excerpt below shows its whole shape (the one-line
`description` field is omitted):

Source: `package.json`

```json
{
  "name": "matchlayer",
  "version": "0.0.0",
  "private": true,
  "engines": {
    "node": ">=24"
  },
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
  "devDependencies": {
    "openapi-typescript": "^7.4.4",
    "openapi-zod-client": "^1.18.3",
    "prettier": "^3.3.3",
    "typescript": "^5.6.3"
  }
}
```

Reading it field by field: `private` is `true` so the root is never published;
`engines` records that the repository expects Node.js version 24 or newer;
`packageManager` pins the exact pnpm version (`pnpm@9.15.9`) that corepack
activates for everyone. The `scripts` use pnpm's recursive flag `-r` so a single
`pnpm lint`, `pnpm typecheck`, `pnpm test`, or `pnpm build` at the root runs that
script in every workspace member at once. The `codegen` script runs the code
generation (codegen) step that turns the back-end's contract into TypeScript.
The `devDependencies` are the tools shared by every project — the formatter
Prettier, the TypeScript compiler, and the two generators that derive
TypeScript types and validators from the back-end's OpenAPI document (a
standard, machine-readable description of a web service's endpoints).

The shared base type-checker configuration is `tsconfig.base.json`, also at the
repository root. Every TypeScript project in the repository has its own
`tsconfig.json` that extends this file:

Source: `tsconfig.base.json`

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "skipLibCheck": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "baseUrl": ".",
    "paths": {
      "@matchlayer/shared-types": ["packages/shared-types/src"],
      "@matchlayer/shared-types/*": ["packages/shared-types/src/*"]
    }
  }
}
```

`strict` is `true`, which turns on the strict family of checks for the whole
repository, and `noUncheckedIndexedAccess` adds one more guard so that reading an
array or object by index is treated as possibly undefined. The remaining options
fix the language target and the module system. The `paths` block declares the
import alias `@matchlayer/shared-types`, so any project can import the shared
types package by that readable name and the type checker resolves it to the
package's source folder. Because all of this lives in one base file, a project's
own configuration only needs to extend it and add its own specifics.

## Common pitfalls

- **Mistake:** Loosening a strict check by editing one project's own `tsconfig.json` (for example turning `strict` off there) instead of treating the shared base configuration as the single source of truth.
  **Symptom:** That one project type-checks under weaker rules than the rest, so unsafe code passes locally but fails in a sibling project or in the repository-wide type-check command.
  **Recovery:** Remove the local override and keep the project extending `tsconfig.base.json`; if a rule genuinely needs to change for everyone, change it once in the base file so the whole repository moves together.

- **Mistake:** Adding `private: true` only as an afterthought, or removing it, on the root manifest that is not meant to be a published library.
  **Symptom:** The package manager attempts to publish the repository root, or warns about missing publish metadata, during an install or release step.
  **Recovery:** Keep `private` set to `true` on the root manifest; only the individual libraries that are genuinely meant to be published omit it and carry real publish metadata.

- **Mistake:** Changing the pinned `packageManager` version in the root manifest without telling the team, or ignoring the pin and installing with a different package-manager version.
  **Symptom:** Different contributors resolve dependencies with different tool versions, producing inconsistent installs and "works on my machine" differences between runs.
  **Recovery:** Treat the `packageManager` pin as the agreed version, update it deliberately in a single commit, and let corepack activate the pinned version rather than using a locally installed one.

- **Mistake:** Putting a tool or dependency that only one project needs into the root manifest's shared `devDependencies`, or duplicating a genuinely shared tool inside each project.
  **Symptom:** The root accumulates dependencies unrelated to repository-wide tooling, or the same tool is pinned to several versions across projects and they drift apart.
  **Recovery:** Keep only repository-wide tooling (the formatter, the type checker, code-generation tools) at the root, and declare a project-specific dependency in that project's own manifest.

## External reading

- [Node.js: the package.json manifest and packages](https://nodejs.org/api/packages.html)
- [TypeScript: What is a tsconfig.json](https://www.typescriptlang.org/docs/handbook/tsconfig-json.html)
- [TypeScript: tsconfig reference (compiler options)](https://www.typescriptlang.org/tsconfig)
- [pnpm: package.json fields and settings](https://pnpm.io/package_json)
