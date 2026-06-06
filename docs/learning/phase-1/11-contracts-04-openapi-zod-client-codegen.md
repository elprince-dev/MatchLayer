# openapi-zod-client and the generated Zod schemas

## Introduction

This document explains `openapi-zod-client`, a command-line code generator that turns an OpenAPI document into Zod schemas, and it walks through the schema file that generator produces. OpenAPI (a standardized, machine-readable description of an Application Programming Interface (API) — every route, request body, and response shape captured in a single document) is the input to the generator. Zod is a TypeScript-first library for declaring a data shape once and then validating an unknown value against that shape at run time. Code generation (codegen) — producing source code automatically from a description rather than writing it by hand — is the broader category this tool belongs to, and it is one link in a chain that keeps the browser-side data shapes identical to the server-side ones.

**Learning outcomes** — after reading this document you will be able to:

- Describe what `openapi-zod-client` consumes and what it emits.
- Read a generated Zod schema and map each line back to a field in the API contract.
- Explain why the generated schemas are validated at run time on the frontend and never edited by hand.
- Locate the script that runs the generator and the file the schemas are written into.

Prerequisites:

- [The OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md) — how the contract document is produced in the first place.
- [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md) — where shared, generated code belongs.
- [TypeScript strict mode and the repo compiler options](02-frontend-02-typescript-strict-mode.md) — the type rules the generated code is checked against.

## Problem it solves

A web client and the server it talks to must agree on the exact shape of every request and response: which fields exist, what type each one is, which are required, and what counts as a valid value. The concrete problem is keeping that agreement true over time. The server's contract changes — a field is renamed, a minimum length is added, a response gains a property — and every place the client encoded the old shape is now wrong, often without any error until a user hits the broken path.

Before a generator existed, a team kept the agreement by hand. Developers read the API documentation and wrote matching TypeScript interfaces, then wrote separate validation code to check incoming data and to back the input forms. Two pre-existing approaches were common, and both had a sharp edge:

- **Trust the server and skip client validation.** The client assumed every response matched the expected shape. When the server drifted, the mismatch surfaced deep inside rendering code as an undefined value, far from the real cause.
- **Hand-write a parallel validator.** The client kept its own copy of the rules. That copy was a second source of truth that quietly fell out of step with the server every time the contract moved, because nothing forced the two to match.

The server already publishes its contract as a JavaScript Object Notation (JSON) OpenAPI document, so the rules are written down once in an authoritative place. `openapi-zod-client` reads that authoritative document and writes the client-side validators from it, which removes the hand-maintained second copy and the drift that comes with it.

## Mental model

Picture a single authoritative contract written in one language and a machine that prints a faithful copy in a second language every time the original changes. The original is the server's contract; the printed copy is a set of validators the browser can run. You never hand-correct the printout — you change the original and print again.

The generator runs as a short pipeline:

1. The server describes its whole contract as one OpenAPI document.
2. The generator reads that document from top to bottom.
3. For every named shape in the document, the generator writes one Zod schema whose rules mirror that shape's fields and constraints.
4. For every route, the generator records the method, the path, and which schema validates the response.
5. The result is one source file that you import, never edit, and regenerate whenever the contract changes.

Holding that pipeline in mind keeps the rest of this document straight: the generated file is an output, not a hand-written module, and its trustworthiness comes from being reprinted from the one authoritative contract.

## How it works

A schema generator is a command-line interface (CLI) program: you point it at a description file and tell it where to write the result. The description it consumes is an OpenAPI document, usually a single JSON file. That document has two parts the generator cares about — a catalogue of named data shapes, and a list of routes that each reference those shapes for their request bodies and responses.

The heart of the tool is a mapping from the description's data shapes onto Zod's builder calls. Each named shape becomes one schema object. Inside it, every property becomes a validator: a string field becomes a string validator, an integer field becomes a number validator constrained to whole numbers, and a fixed set of allowed values becomes an enumeration validator. Constraints carry across too — a minimum length, a required-versus-optional distinction, a value that is allowed to be absent or null. A field that may be a value or null becomes a union of the value's validator and a null validator; an optional field is marked so that a missing value passes. The generated schema is therefore a line-by-line echo of the contract's rules, expressed in code the browser can execute.

Routes are handled separately from shapes. For each route the generator emits an entry recording the method, the path, the schema that validates the request body, and the schema that validates the response. Those entries can be assembled into a typed client whose calls already know which validator applies to each result. A naming flag controls how the emitted schemas are named: with it, each schema is exported under a stable, human-readable name derived from the contract; without it, the generator falls back to anonymous, position-based names that are awkward to import and that shift when the contract is reordered.

At run time the generated schemas earn their place in two ways. First, a response can be parsed through its schema before the rest of the code touches it, so a contract mismatch fails immediately and loudly at the boundary rather than silently later. Second, the same schema can back an input form: a form library validates what the user typed against the schema and reports each violation as a field error, so the client rejects malformed input before a request is ever sent. Because both uses read from the generated file, and the generated file is reprinted from the contract, the two stay aligned by construction.

## MatchLayer Phase 1 usage

The shared-types package owns this codegen step. Its manifest, `packages/shared-types/package.json`, declares both the pinned generator dependency and the script that runs it:

```json
  "scripts": {
    "codegen": "node ./scripts/codegen.mjs"
  },
  "devDependencies": {
    "openapi-zod-client": "^1.18.3",
    "zod": "^3.25.76"
  }
```

Source: `packages/shared-types/package.json`

Running that `codegen` script executes the orchestrator at `packages/shared-types/scripts/codegen.mjs`. After the orchestrator dumps the live OpenAPI document from the FastAPI app, its third step shells out to `openapi-zod-client`:

```javascript
console.log("[codegen] step 3/4: generating src/api-schemas.ts");
await execa(
  "pnpm",
  [
    "exec",
    "openapi-zod-client",
    "openapi.json",
    "--output",
    "src/api-schemas.ts",
    "--with-alias",
  ],
  {
    cwd: packageRoot,
    stdio: "inherit",
  },
);
```

Source: `packages/shared-types/scripts/codegen.mjs`

The `--with-alias` flag is what makes the generator emit stable, named schema objects instead of anonymous ones. The command writes its output to the generated file `packages/shared-types/src/api-schemas.ts`, where each named shape in the contract becomes one Zod schema. The registration request, for example, is emitted as:

```typescript
import { makeApi, Zodios, type ZodiosOptions } from "@zodios/core";
import { z } from "zod";
const RegisterRequest = z.object({
  email: z.string().email(),
  password: z.string().min(12),
  display_name: z.union([z.string(), z.null()]).optional(),
});
```

Source: `packages/shared-types/src/api-schemas.ts`

Read that schema field by field: `email` must be a valid email string, `password` must be at least twelve characters long, and `display_name` is an optional string-or-null. Those rules mirror the server's model exactly, because the generator wrote them from the same OpenAPI document the server produced.

Application code never imports that generated file directly. The curated entry point `packages/shared-types/src/index.ts` re-exports each generated schema under a stable name, so the rest of the codebase imports a friendly name rather than reaching into the generated module:

```typescript
import { schemas } from "./api-schemas";
export const RegisterRequestSchema = schemas.RegisterRequest;
```

Source: `packages/shared-types/src/index.ts`

A continuous integration (CI) drift check regenerates the schemas on every pull request and fails the build when the committed file differs from the live contract, so the generated file can be trusted as an accurate copy rather than a stale one.

## Common pitfalls

- **Mistake:** Hand-editing the generated schema file to tweak a rule or rename a field.
  **Symptom:** The edit disappears the next time the `codegen` script runs, or the CI drift check fails because the committed file no longer matches the regenerated output.
  **Recovery:** Change the server's model so the contract itself changes, then rerun the generator; treat the generated file as read-only output.

- **Mistake:** Writing a parallel hand-written Zod schema for an endpoint that already has a generated one.
  **Symptom:** The two schemas drift apart, and a form accepts input the server later rejects (or rejects input the server would have accepted).
  **Recovery:** Import the generated schema for anything with an API representation, and reserve hand-written Zod schemas for purely client-side state that the server never sees.

- **Mistake:** Dropping the `--with-alias` flag from the generator invocation.
  **Symptom:** Schemas are emitted under anonymous, position-based names, and the curated re-export file can no longer find stable names to export, so imports across the app break.
  **Recovery:** Keep the alias flag in the generator command so every schema keeps a stable, contract-derived name.

- **Mistake:** Importing schemas straight from the generated module everywhere instead of from the curated entry point.
  **Symptom:** Imports churn and break whenever the contract is reordered or a schema is renamed, and the generated module's shape leaks across the whole codebase.
  **Recovery:** Import schemas only from the package's curated entry point, which gives each schema a name that stays stable across regenerations.

## External reading

- [openapi-zod-client (project repository)](https://github.com/astahmer/openapi-zod-client)
- [Zod documentation](https://zod.dev/)
- [FastAPI: first steps and the generated OpenAPI document](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [TypeScript handbook](https://www.typescriptlang.org/docs/handbook/intro.html)
