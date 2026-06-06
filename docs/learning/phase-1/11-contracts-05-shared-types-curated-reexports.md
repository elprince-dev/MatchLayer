# The curated index.ts re-export pattern for shared types

## Introduction

This document explains how a shared library can expose a small, hand-chosen set
of names to the rest of a codebase while hiding the large, awkward, machine-made
files behind them. The technique is the **re-export pattern**: one entry-point
module (a single file that other code imports from) declares friendly names and
points each one at a definition that physically lives in another file, so the
importing code never touches the other file directly. When the entry-point
module exists only to forward names outward like this, it is often called a
**barrel file** — a module that gathers exports from several internal files and
re-publishes them through one front door. The names it forwards form the
library's **curated public surface** — the deliberately selected
Application Programming Interface (API), meaning the set of importable names,
that the library promises to keep stable for its consumers.

The files being hidden here are **generated code**: source files written by a
tool rather than by a person, produced from another document and overwritten
whenever that document changes. In this project they are produced by codegen
(code generation — the automated step that turns one machine-readable
description into source files) run from the contract of the back-end service.
Because generated files are rewritten on every run, importing from them directly
is fragile; the re-export pattern gives consumers a stable surface that does not
move when the generator reshapes its output.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a re-export (barrel) module is and why a library forwards names through one curated entry point instead of letting consumers import internal files.
- Describe how a curated public API decouples the names consumers depend on from the names a code generator happens to emit.
- Read a re-export module and tell a type alias apart from a value re-export, and a forwarded name apart from a renamed one.
- Recognise the common mistakes around bypassing the entry point or letting generated names leak, and recover from them.

Prerequisites:

- [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md) — introduces the single repository and the idea of a shared library that other projects import.
- [The root package.json and shared tsconfig.base.json](01-foundations-05-root-package-and-tsconfig.md) — explains the import alias by which the rest of the codebase refers to this shared library by a readable name.

## Problem it solves

A code generator that derives types and validators from a service contract emits
files whose internal names and shapes are dictated by the generator, not by the
people who use them. The concrete problem is that those generated shapes are both
ugly and unstable to depend on. A generated type for a request body might be
reachable only through a long chain of index lookups that mirrors the raw
contract document, and a generated validator might be filed under a name the
generator chose (for example a trailing `Out` suffix) rather than the name the
rest of the team uses in conversation. If every consumer imports those raw shapes
directly, two things go wrong: the consuming code fills up with unreadable
type expressions, and any change in how the generator names or nests its output
ripples out into every file that imported it.

The common prior approach is to let each consumer reach straight into the
generated files and write the long expression or the generator's chosen name
inline at every use site. That arrangement has real costs:

- The unreadable, deeply-indexed type expression is copied into many files, so a reader has to decode the same chain repeatedly and a typo in it fails far from its cause.
- Consumers couple themselves to the generator's naming and nesting, so re-running the generator with a new version — or switching generators — forces edits across the whole codebase.
- There is no single place that records which parts of the generated output are meant to be public, so internal, regenerated detail leaks into application code that was never supposed to see it.

A curated re-export module removes those costs. It names each public shape once,
in one file, behind a short readable identifier, and forwards it to wherever the
generated definition currently lives. Consumers import the short name; the long
expression and the generator's quirks stay behind the front door.

## Mental model

Think of a busy office building with a single staffed reception desk. Visitors do
not wander the corridors hunting for the right room; they ask at the desk, and the
desk routes them to the correct office. If a department moves to a different floor,
the receptionist updates one internal directory and every visitor still arrives at
the right place — nobody outside has to learn the new room number. The reception
desk is the curated entry point; the offices are the generated files; the
directory the receptionist keeps is the set of re-export lines.

When code elsewhere asks the shared library for a name, the resolution works like
this:

1. The consumer imports a short, readable name from the library's single entry-point module — never from the generated files behind it.
2. The entry-point module looks up that name among its re-export lines and finds the line that defines or forwards it.
3. For a forwarded name, the line points at the real definition inside a generated file (and may attach a different, friendlier public name to it on the way out).
4. The type checker or the running program follows that pointer to the actual definition, so the consumer gets the real shape without ever naming the generated file.
5. When the generator later rewrites its files, only the re-export lines inside the entry point might need adjusting; every consumer keeps importing the same unchanged names.

That five-step routing is the whole idea. The rest of this document fills in how a
re-export module expresses those forwarding lines and how a project applies the
pattern.

## How it works

A re-export module is an ordinary source file whose body is dominated by
`export` statements that forward names defined elsewhere. In a typed,
module-based language two kinds of thing can be forwarded, and the distinction
matters:

- **Types** exist only at compile time. They describe the shape of data and are erased before the program runs. A re-export module forwards a type by declaring a type alias — a new name set equal to an existing type expression. Forwarding a type can be marked as type-only, which tells the compiler the name carries no runtime value and can be dropped entirely from the emitted program.
- **Values** exist at run time. A validator object, a function, or a constant is a value. A re-export module forwards a value with a normal export that binds a new name to the existing value, and that binding survives into the running program.

Because a generated file usually exposes both — a type for compile-time shape and
a matching runtime validator for checking real data — the entry-point module
typically forwards them in pairs: one type alias and one value export per concept.

Three capabilities make the pattern work. First, **renaming on the way out**: the
forwarded public name does not have to equal the internal name. A line can bind a
clean public identifier to a value that the generator filed under a clumsier name,
so the awkward name stays internal while consumers see the clean one. Second,
**deriving narrower names from a broader one**: a type alias can be defined by
indexing into another already-exported type rather than by reaching back into the
raw source, so a nested shape is expressed in terms of the public parent and can
never drift away from the fields the parent actually has. Third, **a single
declared entry point**: the library's manifest names one file as the module that
consumers resolve to, so importing the library by its package name always lands on
the curated surface and the generated files are not part of the public import
path.

The combined effect is a deliberate seam between two naming worlds. On the inside
are the generator's names and nesting, free to change whenever the generator runs.
On the outside are the curated names, chosen by people and held stable. The
re-export module is the one place those two worlds meet, which means a change on
either side is absorbed in that single file instead of rippling across every
consumer.

## MatchLayer Phase 1 usage

In MatchLayer the curated entry point is `packages/shared-types/src/index.ts`. It
is the single import surface for the shared types library: the package manifest
points its `main`, `types`, and `exports` entries at that one file, so any
sibling project that imports the package by name resolves to the curated module
and never to the generated files beside it.

Source: `packages/shared-types/package.json`

```json
  "main": "./src/index.ts",
  "types": "./src/index.ts",
  "exports": {
    ".": "./src/index.ts"
  },
```

The entry point pulls the generated definitions in at the top, from two files
that are produced by codegen and must not be imported directly by application
code. The first is `packages/shared-types/src/api-types.ts`, written by the
`openapi-typescript` generator, which turns the back-end contract into a single
`paths` type describing every endpoint. The second is
`packages/shared-types/src/api-schemas.ts`, written by the `openapi-zod-client`
generator, which produces a `schemas` object of runtime validators (Zod schemas —
small objects that check real data against the contract at run time). The
re-export module imports the type as type-only and the validators as a value:

Source: `packages/shared-types/src/index.ts`

```typescript
import type { paths } from "./api-types";
import { schemas } from "./api-schemas";
```

From those two imports it forwards a curated pair per concept: a readable type
alias and a matching validator export. The login endpoint is representative — the
type alias resolves the long, index-chained expression behind one short name, and
the consumer never has to write that chain itself:

Source: `packages/shared-types/src/index.ts`

```typescript
export type LoginRequest =
  paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]["application/json"];
```

The matching validators show renaming on the way out. The login request validator
keeps its name, but the login response validator is bound to a generated object
filed under a different internal name (`TokenPairResponse`), so the clean public
name `LoginResponseSchema` is what consumers see while the generator's name stays
internal:

Source: `packages/shared-types/src/index.ts`

```typescript
export const LoginRequestSchema = schemas.LoginRequest;
export const LoginResponseSchema = schemas.TokenPairResponse;
```

The module also derives narrower names from a broader exported type rather than
reaching back into the raw contract. The nested value objects inside a match
result are defined by indexing into the already-exported `MatchResponse` type, so
they stay in lockstep with the fields the response actually carries and cannot
drift from it:

Source: `packages/shared-types/src/index.ts`

```typescript
export type ScoreBreakdown = MatchResponse["score_breakdown"];
export type Keyword = MatchResponse["matched_keywords"][number];
export type Suggestion = MatchResponse["suggestions"][number];
```

Their runtime validators alias the generated objects, which the generator named
with a trailing `Out` suffix, under the same clean public names used for the
types:

Source: `packages/shared-types/src/index.ts`

```typescript
export const ScoreBreakdownSchema = schemas.ScoreBreakdownOut;
export const KeywordSchema = schemas.KeywordOut;
export const SuggestionSchema = schemas.SuggestionOut;
```

The net effect is that the rest of the codebase imports `LoginRequest`,
`MatchResponse`, `ScoreBreakdown`, and their schema counterparts from one place,
while the index-chained `paths[...]` expressions and the generator's `*Out` names
stay sealed inside this file. When codegen re-runs and reshapes the generated
files, only these re-export lines might need a touch; no consumer changes.

## Common pitfalls

- **Mistake:** Importing a type or validator straight from the generated `api-types.ts` or `api-schemas.ts` file instead of from the curated entry point.
  **Symptom:** The next codegen run renames or reshapes the generated output and the direct import breaks, or the consuming file fills with the long `paths[...]["post"]...` expression that is hard to read and easy to mistype.
  **Recovery:** Import the curated name from the package's entry point, and if the name you need is not exported there yet, add one curated re-export line for it rather than reaching past the front door.

- **Mistake:** Hand-editing a generated file to rename a shape or fix its nesting, instead of renaming it on the way out in the re-export module.
  **Symptom:** The edit vanishes the next time the generator runs and overwrites the file, so the "fix" silently disappears and the build drifts back to the generator's names.
  **Recovery:** Leave generated files untouched and express every renaming or narrowing in the entry-point module, which the generator never overwrites; bind the clean public name to the generated value there.

- **Mistake:** Forwarding a type with a plain re-export when the compiler cannot tell it is type-only, or treating a runtime validator as if it were a type.
  **Symptom:** A type-only name is mistakenly expected to exist at run time and is undefined when the program runs, or a value import is dropped from the bundle because it was treated as a type.
  **Recovery:** Import compile-time shapes with a type-only import and forward them as type aliases, and forward runtime validators as ordinary value exports, keeping the two kinds on separate lines so their nature is explicit.

- **Mistake:** Re-deriving a nested shape by indexing back into the raw generated `paths` chain instead of indexing into the already-exported parent type.
  **Symptom:** A field renamed in the contract changes the parent type but not the hand-built nested copy, so the two disagree and a Continuous Integration (CI) drift check or the type checker reports a mismatch.
  **Recovery:** Define nested names by indexing into the public parent type so they track the parent automatically and cannot fall out of step with the fields the API returns.

## External reading

- [TypeScript handbook: Modules (re-exporting and `export ... from`)](https://www.typescriptlang.org/docs/handbook/2/modules.html)
- [TypeScript: type-only imports and exports](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-3-8.html)
- [TypeScript handbook: Modules reference](https://www.typescriptlang.org/docs/handbook/modules/reference.html)
- [Node.js: ECMAScript modules](https://nodejs.org/api/esm.html)
- [pnpm: workspaces and linking packages by name](https://pnpm.io/workspaces)
