# TypeScript strict mode and the repo compiler options

## Introduction

This document explains a single configuration switch that makes a typed language
catch a whole family of mistakes before the program ever runs. The language is
TypeScript (a version of JavaScript that adds types — labels that say what kind
of value each variable holds — and checks them ahead of time). The switch is
**strict mode**, one setting in the type checker's configuration file that turns
on a group of stricter checks together rather than one at a time. This document
also walks the exact compiler options the project turns on, so you can read the
configuration files and know what each line buys you.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a type checker does and what turning on strict mode changes. A type checker reads code without running it and reports values used in ways their types do not allow.
- Name the individual checks that strict mode bundles together and say what each one prevents. Strict mode is a convenience switch, not a single check.
- Read the project's shared base configuration and a per-application configuration that extends it. The project defines strict options once and inherits them everywhere.
- Recognise the common mistakes around strict mode and recover from them. Most friction comes from values that might be absent, which strict mode refuses to ignore.

Prerequisites: this document builds on
[The root package.json and shared tsconfig.base.json](01-foundations-05-root-package-and-tsconfig.md),
which introduces the shared base type-checker configuration that every project
extends, and
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md), which
introduces the single-repository layout the configuration files live in.

## Problem it solves

A dynamically checked language lets a program run even when a value is used in a
way that makes no sense — calling a method on a value that turned out to be
absent, reading a property that does not exist, passing a number where text was
expected. The concrete problem is that these mistakes surface only at runtime,
often in production, far from the line that caused them.

The common prior approach was to catch such mistakes with tests and careful code
review, and to accept that some would still slip through to users. That approach
has real costs:

- A whole category of errors — using a value that might be absent — is invisible until the exact code path runs with the exact data that triggers it.
- Tests only cover the cases someone thought to write, so an untested combination of inputs can still fail in the field.
- Refactoring is risky, because nothing checks that every caller of a changed function was updated to match.

A type checker addresses this by analysing the code without running it and
reporting type mismatches up front. Strict mode sharpens that analysis: it turns
on the stricter checks that, in particular, force code to account for values
that might be absent, so the "used something that wasn't there" class of bug is
caught at check time instead of in production.

## Mental model

Think of the type checker as a proofreader who reads your whole manuscript
before it goes to print and flags every place a sentence refers to a character
who was never introduced. Without strict mode, the proofreader is lenient and
lets some of those slips through. Turning on strict mode tells the proofreader to
be uncompromising: every reference must be accounted for, and "this might be
nothing" must be handled explicitly rather than assumed away.

When the type checker runs over a file with strict mode on, it works like this:

1. It reads each value's declared or inferred type — the set of shapes that value is allowed to take.
2. At every use of that value, it checks that the operation is valid for _all_ shapes the type allows, not only the common one.
3. Where a type includes the possibility of being absent (a value that could be null or undefined), it requires the code to handle that possibility before using the value.
4. Where a value's type cannot be determined at all, strict mode refuses to silently treat it as "anything" and asks for an explicit type instead.
5. It reports each unhandled case as an error, naming the file and line, so the mistake is fixed before the program runs.

Steps 2 and 3 are the heart of strict mode: it makes "what if this value is
absent or unexpected?" a question the code must answer, not one left to chance.

## How it works

A type checker for a typed language reads a configuration file that turns
individual checks on or off, then analyses the source without executing it. Most
of those checks can be enabled one by one, but the language also provides a
single umbrella switch that enables a curated family of them at once. That
umbrella is strict mode. Turning it on is equivalent to turning on every member
of the family, and it is the recommended baseline because the members reinforce
one another.

The members of that family each close a specific gap:

- One member rejects values whose type could not be inferred and would otherwise be treated as "anything", forcing an explicit type where the checker is in the dark.
- One member makes absence part of the type system: a value that could be null or undefined is treated as a distinct type, and the code must narrow it — check for the absent case — before using it.
- Other members tighten how function arguments, class fields, and `this` are checked, so a function called with the wrong shape, or a field used before it is assigned, is reported rather than allowed.

A project can layer additional checks beyond the strict family. One commonly
added check treats reading a value out of an array or object by an index or key
as possibly absent, because at runtime the slot may be empty even when the
container's type suggests otherwise. This guards the gap between "the container
holds values of this type" and "this particular slot definitely has one".

Because all of these are configuration, the practical pattern in a
multi-project repository is to declare the strict options once in a shared base
configuration and have each project's own configuration _extend_ it. Extending
means the project inherits the base options and then adds or overrides only the
few fields specific to it — where its source lives, which environment it targets
— while the strict settings stay identical across every project from one source.

## MatchLayer Phase 1 usage

In MatchLayer the strict options are declared once in the shared base
configuration at the repository root, `tsconfig.base.json`. Every TypeScript
project inherits them by extending this file:

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

`strict` set to `true` turns on the whole strict family at once. The line
immediately after it, `noUncheckedIndexedAccess`, adds the extra guard described
above: reading an array or object by index is treated as possibly absent, so the
code has to handle the empty-slot case. The remaining options fix the language
target and the module system rather than the strictness level.

The web application's own configuration, `apps/web/tsconfig.json`, extends that
base and then re-states the strict options explicitly alongside its
browser-specific settings:

Source: `apps/web/tsconfig.json`

```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "noEmit": true,
    "jsx": "preserve",
    "incremental": true,
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "noUncheckedIndexedAccess": true,
    "plugins": [{ "name": "next" }],
```

The `extends` line is what pulls in every base option. The `lib` and `jsx`
fields are web-specific — they tell the checker the browser features and the
React syntax this project uses. The repeated `strict`, `noImplicitAny`,
`strictNullChecks`, and `noUncheckedIndexedAccess` lines name the two most
important strict-family members directly: `noImplicitAny` (reject values the
checker could not type) and `strictNullChecks` (treat absence as part of the
type). Re-stating them here makes the web application's strict posture explicit
to anyone reading only this file.

## Common pitfalls

- **Mistake:** Using a value that the type says might be absent (null or undefined) without first checking for the absent case, under `strictNullChecks`.
  **Symptom:** The type checker reports that the value is "possibly null" or "possibly undefined" at the line where it is used.
  **Recovery:** Narrow the value before use — guard it with a conditional, provide a default, or otherwise prove to the checker it is present — rather than turning the check off.

- **Mistake:** Silencing a strict error by relaxing a strict option in one project's own configuration instead of treating the shared base as the single source of truth.
  **Symptom:** That one project type-checks under weaker rules than the rest, so unsafe code passes there but fails in a sibling project or in the repository-wide type-check.
  **Recovery:** Remove the local relaxation and keep extending the shared base; if a rule genuinely must change for everyone, change it once in the base file so the whole repository moves together.

- **Mistake:** Suppressing a strict error with an escape hatch — an explicit "anything" type or a comment that disables the checker on that line — to make the error disappear quickly.
  **Symptom:** The error is gone but the underlying unsafe use remains, and the same value causes a runtime failure later where the checker can no longer see it.
  **Recovery:** Model the value's real type so the checker can verify the use, reserving escape hatches for genuinely untyped third-party boundaries and documenting why each one is needed.

- **Mistake:** Reading an array element or object entry by index and using it directly, while `noUncheckedIndexedAccess` is on.
  **Symptom:** The checker reports the indexed result as possibly undefined, even though the surrounding type looks like it always holds a value.
  **Recovery:** Handle the possibly-absent result — check it, default it, or restructure the access — because at runtime the slot really can be empty.

## External reading

- [TypeScript: tsconfig reference (compiler options)](https://www.typescriptlang.org/tsconfig)
- [TypeScript handbook: the strict family of options](https://www.typescriptlang.org/docs/handbook/2/everyday-types.html)
- [TypeScript: What is a tsconfig.json](https://www.typescriptlang.org/docs/handbook/tsconfig-json.html)
- [MDN Web Docs: null and undefined in JavaScript](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Optional_chaining)
