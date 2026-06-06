# Zod runtime validation at the API boundary

## Introduction

This document explains how a browser-side application checks the data it receives from a server, at the moment it arrives, against a precise description of the shape that data is supposed to have. The tool that does the checking here is Zod, a TypeScript-first library that lets you declare a data shape once as a _schema_ (an in-code description of which fields exist, what type each one is, and which values count as valid) and then test any unknown value against that schema while the program is running. The word for that run-time test is **runtime validation** — confirming, as the program executes, that a value really matches the shape the rest of the code assumes, rather than trusting that it does. In this project the schemas are not written by hand: they are produced by codegen (code generation — the automated step that turns one machine-readable description into source files) from the server's OpenAPI document (a standardized, machine-readable description of an Application Programming Interface (API) — every route, request body, and response shape captured in one file), so the browser validates against the exact contract the server publishes.

**Learning outcomes** — after reading this document you will be able to:

- Explain what runtime validation is and why a typed frontend still needs it at the network boundary.
- Describe the difference between a compile-time type and a run-time schema, and why one cannot replace the other.
- Read a Zod `safeParse` call and predict what it returns for valid and for invalid data.
- Locate the generated Zod schemas this project validates against and the matching screens that call them.

Prerequisites:

- [openapi-zod-client and the generated Zod schemas](11-contracts-04-openapi-zod-client-codegen.md) — how the schemas this document validates against are produced from the contract.
- [The curated index.ts re-export pattern for shared types](11-contracts-05-shared-types-curated-reexports.md) — the named surface the screens import the schemas from.
- [TypeScript strict mode and the repo compiler options](02-frontend-02-typescript-strict-mode.md) — the compile-time type rules that runtime validation complements.
- [The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md) — explains the Client Component, the interactive browser-side screen where this validation runs.

## Problem it solves

A typed frontend gives a strong but narrow guarantee. The type checker proves that _the code_ is internally consistent — that a function which expects a number is never handed a string by another part of the same program. It proves nothing about data that crosses the network, because that data is born outside the program. When a server sends a response, the browser receives a blob of text, decodes it into an object, and the type system is asked to _assume_ that object matches the declared type. That assumption is a promise, not a fact. If the server renamed a field, changed a number to a string, or omitted a value, the type checker never notices, because the mismatch happened at run time in data it could not see.

The concrete problem is the gap between _the type a value is declared to have_ and _the value that actually arrives_. Before runtime validation, two approaches were common, and each had a sharp edge:

- **Cast and hope.** The code decoded the response and asserted "this is a `MatchResult`" with a type assertion. The compiler believed the assertion unconditionally. When the real data did not match, the failure surfaced far away — a component read `score_breakdown.final_score` and got `undefined`, and the screen rendered a blank or threw deep inside rendering, with no hint that the true cause was a contract mismatch at the network edge.
- **Hand-write defensive checks.** The code guarded each field by hand (`if (typeof body.score === "number") ...`). This worked but was a second, hand-maintained description of the shape that drifted out of step with the server every time the contract moved, and it grew unreadable for nested objects and arrays.

Runtime validation closes the gap by checking the arriving value against an explicit schema the instant it crosses the boundary, so a mismatch is caught at its source and reported as one clear, handleable outcome instead of a scattered, late failure.

## Mental model

Think of an arrivals checkpoint at a border. A passenger (the incoming data) walks up with a passport (the declared type). The type system is like the airline that already checked the ticket back at departure — useful, but it did not inspect this passenger at _this_ border. The checkpoint officer (the schema) does the real inspection here and now: every required stamp present, every field the right kind, nothing forged. A passenger who passes walks through with papers the officer vouches for; a passenger who fails is turned back at the desk, not waved through to cause confusion deeper inside the country.

The inspection runs as a short, repeatable sequence:

1. A request goes out, and a raw response comes back as text of unknown trustworthiness.
2. The text is decoded into a plain value whose true shape is not yet confirmed.
3. That value is handed to the schema, which checks every field against the declared rules.
4. The schema returns one of two outcomes: success carrying a value now known to match the shape, or failure carrying a description of what was wrong.
5. The code branches on that outcome — render with the trusted value, or show a friendly error — so a malformed payload never reaches the rendering code as if it were valid.

Holding that checkpoint in mind keeps the rest straight: the schema is the officer that turns an _assumed_ shape into a _verified_ one at the exact moment data enters the program.

## How it works

A schema is a value, not a type. A type exists only while the code is being compiled and is erased before the program runs; it can describe a shape but cannot inspect a real value, because by run time it no longer exists. A schema is an ordinary object that survives into the running program and carries a method you can call on live data. This is the core reason a type cannot stand in for a schema: the two live in different worlds. The type guards the code at compile time; the schema guards the data at run time. A validation library bridges them by deriving the static type _from_ the schema, so a single declaration yields both the compile-time shape and the run-time checker, and the two can never disagree.

You build a schema by composing small validators. A field that must be a string of at least one character is one validator; a whole object is a validator that maps each property name to the validator for that property's value. Arrays wrap an element validator; a value that may be absent is marked optional; a value that may be one of several shapes is a union. The composed object mirrors the contract field for field, which is what makes it a faithful checkpoint rather than an approximation.

At run time you apply the schema to an unknown value with a parse call. There are two styles, and the difference matters for how errors are handled:

- A **throwing parse** returns the validated value on success and raises an exception on failure. It suits places where a failure is exceptional and should unwind to a surrounding handler.
- A **safe parse** never throws. It returns a small result object with a success flag: on success the object carries the parsed, now-trusted data; on failure it carries a structured description of every problem. Code reads the flag and branches, which keeps the failure path explicit and local — the malformed case becomes one ordinary branch rather than an exception thrown across the call stack.

A subtle but important property is that a successful parse returns _the parsed value_, not the original input. Use that returned value downstream, because it is the one the schema vouches for; the raw input is still of unknown shape as far as the rest of the program should be concerned. This is the practical form of the "parse, don't validate" idea: instead of merely asking "is this valid?" and then continuing to use the untyped original, you transform the unknown input into a value whose type is now proven, and you carry that proven value forward. The boundary between trusted and untrusted data is the parse call, and everything past it works with data the checkpoint has already cleared.

## MatchLayer Phase 1 usage

The schemas the matching screens validate against are generated into `packages/shared-types/src/api-schemas.ts` from the back-end OpenAPI document and exposed under curated names from `packages/shared-types/src/index.ts`. Two of those schemas drive the match flow: the request shape for creating a match, and the response shape returned for a scored match.

The request schema constrains the two fields the create-match endpoint accepts — a resume identifier and a job description — each required to be a non-empty string:

```typescript
const CreateMatchRequest = z.object({
  resume_id: z.string().min(1),
  job_description: z.string().min(1),
});
```

Source: `packages/shared-types/src/api-schemas.ts`

The response schema describes the full scored result the user interface (UI) renders — the numeric score, the breakdown object, the matched and missing keyword arrays, the suggestions, and the scoring metadata:

```typescript
const MatchResponse = z
  .object({
    id: z.string(),
    resume_id: z.string(),
    score: z.number().int(),
    score_breakdown: ScoreBreakdownOut,
    matched_keywords: z.array(KeywordOut),
    missing_keywords: z.array(KeywordOut),
    suggestions: z.array(SuggestionOut),
    scorer_version: z.string(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .passthrough();
```

Source: `packages/shared-types/src/api-schemas.ts`

Application code never reaches into the generated file directly. The curated entry point re-exports each schema under a stable, readable name, and the screens import those names:

```typescript
export const CreateMatchRequestSchema = schemas.CreateMatchRequest;
export const MatchResponseSchema = schemas.MatchResponse;
```

Source: `packages/shared-types/src/index.ts`

The upload screen, a Client Component (a screen marked to run in the browser so it can hold interactive state) at `apps/web/src/app/(app)/upload/page.tsx`, validates the _outgoing request_ with the generated request schema before sending it. The `safeParse` call confirms the body matches the contract, and the code branches on the result rather than trusting the object it assembled:

```typescript
const matchRequest = CreateMatchRequestSchema.safeParse({
  resume_id: resume.id,
  job_description: jobDescriptionText,
});
if (!matchRequest.success) {
  setError("We couldn't start the match. Please try again.");
  return;
}
```

Source: `apps/web/src/app/(app)/upload/page.tsx`

The results screen validates the _incoming response_. The data view at `apps/web/src/components/results/results-view.tsx` imports both the schema (a run-time value) and the matching type (a compile-time shape) from the shared package:

```typescript
import { MatchResponseSchema } from "@matchlayer/shared-types";
import type { MatchResponse } from "@matchlayer/shared-types";
```

Source: `apps/web/src/components/results/results-view.tsx`

After fetching a match it runs the body through `MatchResponseSchema.safeParse` at the boundary. A parse failure means the live API and the committed contract have drifted, and the screen turns that into a friendly, retryable error instead of crashing on a missing field deep in rendering:

```typescript
const parsed = MatchResponseSchema.safeParse(body);
if (!parsed.success) {
  throw new MatchFetchError("retryable");
}
```

Source: `apps/web/src/components/results/results-view.tsx`

Because both screens read from the generated schemas, and those schemas are reprinted from the server's OpenAPI document on every change, the browser's checkpoint stays aligned with the server's contract by construction. A continuous integration (CI) drift check regenerates the schemas on every pull request and fails the build if the committed copy no longer matches the live contract, so the schemas the screens trust are an accurate copy rather than a stale one.

## Common pitfalls

- **Mistake:** Reading the original fetched body after a successful parse instead of the parser's returned `data`.
  **Symptom:** The compiler still types the variable as `unknown` (or the raw input type), so field access either fails to type-check or silently works on unvalidated data, defeating the point of parsing.
  **Recovery:** Use the value the parse returns (`parsed.data`) downstream; treat the raw input as untrusted once a schema has produced a verified copy of it.

- **Mistake:** Using a throwing parse on the network path where a malformed payload is an expected, handleable outcome.
  **Symptom:** A contract drift or a bad response throws an unhandled exception that unwinds into the rendering tree and shows a blank screen or an error boundary instead of a friendly message.
  **Recovery:** Use `safeParse` at the boundary and branch on the `success` flag, mapping the failure to a recoverable UI state (a retryable error or empty state).

- **Mistake:** Hand-writing a second Zod schema for an endpoint that already has a generated one, to "tweak" a rule.
  **Symptom:** The hand-written copy drifts from the contract, so a form accepts input the server later rejects (or rejects input the server accepts), and the two descriptions disagree with no single source of truth.
  **Recovery:** Validate against the generated schema imported from the shared package; reserve hand-written schemas for purely client-side state that has no API representation.

- **Mistake:** Treating the compile-time type as if it guaranteed the runtime value, skipping validation because "the type says it's a `MatchResponse`".
  **Symptom:** A renamed or missing field arrives at run time, the type checker never flagged it, and a component reads an `undefined` nested value far from the network call.
  **Recovery:** Parse every response at the boundary; rely on the type for the code's internal consistency and on the schema for the data's actual shape.

## External reading

- [Zod documentation](https://zod.dev/)
- [TypeScript handbook: narrowing and type guards](https://www.typescriptlang.org/docs/handbook/2/narrowing.html)
- [FastAPI: features and the generated OpenAPI document](https://fastapi.tiangolo.com/features/)
- [Mozilla Developer Network (MDN) Web Docs: using the Fetch API](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)
