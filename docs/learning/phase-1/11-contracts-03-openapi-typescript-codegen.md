# openapi-typescript and the generated TypeScript types

## Introduction

This document explains `openapi-typescript`, a code generator that reads an
OpenAPI document and writes a tree of TypeScript types describing every endpoint
of a web Application Programming Interface (API). OpenAPI is a standard,
language-neutral description format for a web API, written as a JavaScript Object
Notation (JSON) document that lists each route, request method, request body, and
response shape. Code generation, often shortened to codegen, is the practice of
producing source files automatically from a machine-readable description rather
than typing them by hand. The output of `openapi-typescript` is one TypeScript
file of pure type declarations: no runtime code, only the shapes that the
compiler uses to check your frontend against the backend contract.

**Learning outcomes** — after reading this document you will be able to:

- Describe what `openapi-typescript` takes as input and what it emits as output.
- Explain why a generated type tree keeps frontend code honest about the backend contract.
- Read the generated `paths`, `components`, and `operations` interfaces and index into them to reach a single request or response shape.
- Recognize when the generated file is stale and how regeneration brings it back in sync.

Prerequisites:

- [The OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md) — covers what an OpenAPI document is and how the backend produces one. Read it first, because `openapi-typescript` consumes exactly that document.
- [TypeScript strict mode and the repo compiler options](02-frontend-02-typescript-strict-mode.md) — covers the type checker that gives the generated types their value.
- [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md) — covers why generated types live in a shared library that applications import.

## Problem it solves

A frontend and a backend speak to each other across a network boundary, but they
are written in different languages and compiled separately. The concrete problem
is keeping the frontend's idea of a request or response shape identical to the
backend's actual contract. When the backend renames a field, removes a property,
or changes a type, nothing in the frontend's source automatically notices.

The common prior approach is to hand-write a set of TypeScript interfaces that
mirror the backend models, then update them by memory whenever the backend
changes. That pre-existing state has three recurring failures:

- The hand-written interface and the real response drift apart silently. The code compiles, ships, and then fails at runtime when a field the frontend expected is missing or renamed.
- Every contract change becomes two edits in two languages that a human has to remember to keep in step, and the second edit is the one that gets forgotten.
- A reviewer cannot tell, from the frontend diff alone, whether a type still matches the backend, so the review gives false confidence.

Generating the types from the backend's own OpenAPI document removes the human
from the copying step. The generated file is derived from the contract, so it
cannot disagree with a contract it was produced from, and a regeneration that
changes the file is a visible, reviewable signal that the contract moved.

## Mental model

Think of `openapi-typescript` as a translator working from a single official
rulebook. The backend publishes one rulebook (the OpenAPI document) that
describes every message the two sides may exchange. The translator reads that
rulebook front to back and rewrites it as a set of labelled forms in the
frontend's own language. Nobody fills in the forms by hand; the translator
reprints them every time the rulebook changes, so the forms can never describe a
rule the rulebook does not contain.

Walk through the translation as four steps:

1. Start with the rulebook: a description document that names every route, every request method, and the exact shape of each request body and response.
2. The translator reads the document's two halves — the list of routes and the catalogue of reusable object shapes (the models) — and turns each into a named type declaration.
3. The translator writes one output file containing those declarations and nothing else: a route map you can index by route string, and a catalogue of models you can index by name.
4. Whenever the rulebook changes, you re-run the translator. The output file is overwritten, the compiler re-checks the frontend against the new shapes, and any place that no longer matches turns into a type error you see before shipping.

That four-step loop — describe, read, emit, recheck — is the whole idea. The rest
of this document fills in what each emitted shape looks like and how a reader
reaches a single field inside it.

## How it works

The generator takes one input: a description document, expressed as JSON, that
catalogues a web API. It produces one output: a TypeScript source file holding
only type declarations. There is no runtime behaviour in the output, so it adds
nothing to the shipped bundle; it exists purely so the type checker has shapes to
verify against.

The description document has two halves, and the generator turns each into a
top-level interface.

- The routes half lists every endpoint by its route string and, under each, the request methods it accepts. The generator emits a `paths` interface keyed by the literal route strings. Indexing into it with a route string and a method name walks you down to that operation's request and response shapes. Because the keys are the literal route strings, the type checker knows a misspelled route at the call site is wrong.
- The models half is a catalogue of reusable object shapes — the request and response bodies, and any nested objects they share. The generator emits a `components` interface with a `schemas` member, one named type per model. A field marked required in the description becomes a required property; an optional one becomes optional; a fixed literal value becomes a literal type.

Many generators also emit an `operations` interface, which gives each named
endpoint operation its own entry so a route's parameters, body, and responses can
be referenced by operation name instead of by route-and-method indexing. The
three interfaces describe the same contract from different angles.

The output types are structural and deeply nested, so reading them means
_indexing_ rather than importing a flat name. To reach a single response body you
start at `paths`, index by the route string, then by the method, then by the
responses map, then by the status code, then by the response content type. That
long index chain is precise but verbose, which is why teams usually wrap the
chains behind short, curated aliases instead of repeating them at every call
site.

Two properties matter for using the output safely. First, the file is generated,
not authored: it carries a banner saying so, and any hand-edit is erased by the
next run. Second, the file is only as fresh as the last regeneration. If the
contract changes and nobody regenerates, the types describe yesterday's contract
while the running service answers with today's — the exact drift the generator
was meant to prevent. The discipline, then, is to regenerate whenever the
contract moves and to treat a regeneration diff as part of the change.

## MatchLayer Phase 1 usage

In MatchLayer the generated TypeScript types live in the shared-types library at
`packages/shared-types/src/api-types.ts`. That file is produced, never authored,
by the codegen orchestrator script `packages/shared-types/scripts/codegen.mjs`,
which shells out to the pinned `openapi-typescript` binary. The relevant step of
the orchestrator runs the tool against the dumped contract and writes the type
tree to its output path:

Source: `packages/shared-types/scripts/codegen.mjs`

```javascript
await execa(
  "pnpm",
  [
    "exec",
    "openapi-typescript",
    "openapi.json",
    "--output",
    "src/api-types.ts",
  ],
  {
    cwd: packageRoot,
    stdio: "inherit",
  },
);
```

The `openapi.json` argument is the contract dumped from the backend application
by `apps/api/src/matchlayer_api/tools/dump_openapi.py`; the `--output` argument is
the file the generator overwrites on every run. The tool itself is a pinned
development dependency of the shared-types library, invoked through the `codegen`
script, both declared in `packages/shared-types/package.json`:

Source: `packages/shared-types/package.json`

```json
  "scripts": {
    "codegen": "node ./scripts/codegen.mjs"
  },
  "devDependencies": {
    "openapi-typescript": "^7.4.4",
  }
}
```

The file the tool emits opens with the generated-file banner and, below it, the
three top-level interfaces that describe the whole contract — the route map, the
model catalogue, and the per-operation entries:

Source: `packages/shared-types/src/api-types.ts`

```typescript
/**
 * This file was auto-generated by openapi-typescript.
 * Do not make direct changes to the file.
 */

export interface paths {
export interface components {
export interface operations {
```

Reaching a single shape means indexing into `paths`. The curated public surface
in `packages/shared-types/src/index.ts` does exactly that, then re-exports the
result under a short name so the rest of the monorepo never repeats the long
index chain. The health endpoint's response type is one example:

Source: `packages/shared-types/src/index.ts`

```typescript
export type HealthResponse =
  paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"];
```

Here `paths["/healthz"]["get"]["responses"]["200"]["content"]["application/json"]`
is the full index chain into the generated tree, and `HealthResponse` is the
curated alias that application code imports instead. When the backend changes the
health response, the regenerated `packages/shared-types/src/api-types.ts` changes
the shape at the end of that chain, and any frontend code holding a stale
expectation stops compiling.

## Common pitfalls

- **Mistake:** Editing `packages/shared-types/src/api-types.ts` by hand to add or tweak a field, because changing the generated file looked faster than changing the backend.
  **Symptom:** Your edit vanishes the next time anyone runs the codegen script, and until then the frontend type-checks against a shape the backend never actually returns.
  **Recovery:** Treat the generated file as read-only output. Change the backend model so the OpenAPI document changes, then regenerate; the field appears because the contract now contains it.

- **Mistake:** Importing the raw `paths` index chain (for example `paths["/api/v1/auth/login"]["post"]...`) directly into application components.
  **Symptom:** Call sites fill with long, brittle index expressions that break noisily whenever a route string or status code changes, and reviewers cannot read what shape is meant.
  **Recovery:** Index once behind a curated alias in `packages/shared-types/src/index.ts` and import the short name everywhere else, the way `HealthResponse` is exposed.

- **Mistake:** Changing a backend request or response model without regenerating the types in the same change.
  **Symptom:** The frontend keeps compiling against the old shape; the mismatch surfaces only at runtime as a missing or misshaped field, far from the change that caused it.
  **Recovery:** Re-run the codegen script after any contract change and commit the regenerated file alongside the backend edit, so the drift becomes a visible diff instead of a runtime surprise.

- **Mistake:** Expecting `openapi-typescript` to give you runtime validation as well as compile-time types.
  **Symptom:** Invalid data from the network slips through because TypeScript types are erased at build time and check nothing while the program runs.
  **Recovery:** Keep `openapi-typescript` for compile-time shapes and pair it with the generated runtime schemas for parsing untrusted responses; the two outputs are produced from the same contract by separate tools.

## Hands-on checkpoint

Time box: 20 minutes. This exercise reinforces how a reader reaches a single
shape inside the generated type tree, and confirms the generated file really does
type-check the frontend against the contract.

Steps:

1. Open `packages/shared-types/src/api-types.ts` and find the `paths` interface. Locate the entry for the `/healthz` route and follow it down to its `200` response content.
2. Open `packages/shared-types/src/index.ts` and read the `HealthResponse` alias. Confirm that its index chain matches the path you traced by hand in step 1.
3. Add a temporary line to a scratch TypeScript file in the library, such as `const probe: HealthResponse = { status: "down" };`, importing `HealthResponse` from the library entry point.
4. Run the library type check with `pnpm --filter @matchlayer/shared-types typecheck`.

Expected observable artifact: the type check fails, reporting that `"down"` is
not assignable to the `status` shape, because the generated type pins `status` to
the literal the contract declares. Change the value to the contract's literal and
the check passes. Delete the scratch line when you are done.

## External reading

- [openapi-typescript documentation](https://openapi-ts.dev/)
- [TypeScript handbook: object types](https://www.typescriptlang.org/docs/handbook/2/objects.html)
- [TypeScript handbook: indexed access types](https://www.typescriptlang.org/docs/handbook/2/indexed-access-types.html)
