# Vitest and Testing Library for component tests

## Introduction

This document explains how the front-end of the project checks that a single
user-interface component behaves correctly, without opening a real browser. Two
tools work together to make that possible. The first is Vitest (a test runner —
a program that discovers test files, executes the code inside them, and reports
which checks passed and which failed). The second is Testing Library (a small
set of helpers that render a component into a simulated page and then let a test
find and inspect elements the same way a person using the page would). A
component test is a test that renders one user-interface component on its own and
asserts something about what it produced — the text, the form fields, the links,
or its accessibility. Because there is no real browser involved, the component
is rendered into the Document Object Model (DOM), the in-memory tree of elements
that a browser normally builds from a page, supplied here by a lightweight
stand-in.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a test runner does and how Vitest discovers and executes front-end test files. The runner turns a folder of test files into a single pass-or-fail report.
- Describe how Testing Library renders a component and queries it by visible role, label, or text rather than by internal markup. Querying the way a user perceives the page keeps tests resistant to refactors.
- Read a component test that renders a component, finds elements, and asserts on them, including an accessibility check. The same file can verify both behaviour and accessibility in one run.
- Recognise the common mistakes around component testing and recover from them. Most defects come from querying implementation details instead of user-visible output.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
because a component test renders a Client Component (a component that runs in the
browser) and that distinction is introduced there, and on
[TypeScript strict mode and the repo compiler options](02-frontend-02-typescript-strict-mode.md),
because the tests and the components under test are written in TypeScript.

## Problem it solves

A user-interface component is only correct if it renders the right elements,
responds to the right inputs, and stays usable by people relying on assistive
technology. The concrete problem is checking all of that quickly and repeatably,
on every change, without a human opening a browser and clicking through the page
by hand. Manual checking does not scale: it is slow, it is easy to forget a case,
and it cannot run automatically before each merge.

An earlier common approach tested components by reaching into their internals —
asserting that a particular child component was present, or selecting elements by
their Cascading Style Sheets (CSS) class names and internal structure. That style
of test is brittle. Renaming a class, reorganising the markup, or swapping one
child component for an equivalent one breaks the test even though the page still
looks and behaves identically to a user. The test then fails for a reason that
has nothing to do with a real regression, which trains the team to ignore
failures.

A fast test runner paired with a query approach that mirrors how a person reads
the page solves both problems. The runner makes the checks automatic and quick,
and querying by visible role, label, and text means a test only fails when the
user-visible behaviour actually changes.

## Mental model

Think of a component test as a tiny stage play performed in an empty room. The
test runner is the stage manager who calls each scene; the simulated page is the
empty stage; the component is the single actor brought on for that scene; and the
queries are an audience member who can only point at things by what they appear
to be — "the Sign in button", "the Email field" — never by the actor's private
notes.

When one component test runs, the sequence is:

1. The test runner discovers the test file by matching it against a configured pattern, then begins executing the code inside it.
2. The test renders one component into a fresh simulated page, so the component's output exists as a tree of elements the test can inspect.
3. The test queries that tree the way a user would perceive it — by the role of an element (a button, a link), by the label of a form field, or by visible text.
4. The test makes one or more assertions: it states what must be true (this element exists, that link points there) and the runner records a pass or a failure.
5. After the test finishes, the simulated page is cleared so the next test starts from a clean slate with no leftover elements.

Steps 3 and 4 are the heart of the approach: find elements by what the user sees,
then assert on them.

## How it works

A test runner is a program that takes a project's test files, runs the code in
each one, and aggregates the results into a single report. It decides which files
are tests by matching their paths against configured patterns, runs each file in
an isolated scope so one test cannot leak state into another, and exposes a small
vocabulary of functions: one to group related tests, one to declare an individual
test, and one to express an assertion about a value. When an assertion does not
hold, the runner marks that test failed and prints what was expected versus what
was found.

Rendering a component for a test needs somewhere to render into. A real browser
provides that surface, but starting a browser for every test is slow. Instead the
runner can use a simulated environment that implements the Document Object Model
in plain code, so a component can be mounted, inspected, and torn down entirely
in memory. This stand-in builds the same tree of elements a browser would build
from HyperText Markup Language (HTML), without painting pixels to a screen.

On top of that simulated page, a querying library renders a component and returns
helpers for finding elements. Its guiding principle is that a test should locate
elements the way a person does: by their accessible role (a button, a heading, a
text box), by the label associated with a form field, or by the text they
display. Roles come from the Accessible Rich Internet Applications (ARIA)
standard, a set of semantics that assistive technology reads, so querying by role
doubles as a check that the markup is accessible. Querying this way avoids
coupling the test to private details like class names or the component tree's
internal shape, so the test keeps passing through refactors that do not change
what the user perceives.

The querying library exposes several query styles that differ in how they handle
a missing element. One style throws an error immediately when nothing matches —
useful when the element must be present. Another returns an empty result instead
of throwing — useful when a test wants to assert that something is absent. A
third waits a short while for an element to appear — useful for content that
arrives after an asynchronous update. Choosing the right style makes a test fail
with a clear message rather than a confusing one.

Accessibility can be checked inside the same test. An accessibility-auditing
engine walks the rendered tree and reports violations of accessibility rules —
missing labels, illogical heading order, elements that are not reachable. Running
that audit as an ordinary assertion means a component cannot regress its
accessibility without failing a test, which catches a whole class of defects that
visual review tends to miss.

## MatchLayer Phase 1 usage

In MatchLayer the front-end tests live in the web application and are configured
in `apps/web/vitest.config.ts`. That file wires the React plugin into Vitest and
tells the runner which files are tests. It begins by importing Vitest's
configuration helper and the React plugin:

Source: `apps/web/vitest.config.ts`

```typescript
import { defineConfig, configDefaults } from "vitest/config";
import react from "@vitejs/plugin-react";
```

The configuration then declares the default test environment and the glob
patterns that mark a file as a test. The `include` patterns match test files
under both the `tests` folder and the `src` tree, and `passWithNoTests` keeps the
run green when a filtered selection matches nothing:

Source: `apps/web/vitest.config.ts`

```typescript
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "node",
    include: [
      "tests/**/*.{test,spec}.{ts,tsx}",
      "src/**/*.{test,spec}.{ts,tsx}",
    ],
    exclude: [...configDefaults.exclude, "tests/visual/**"],
    passWithNoTests: true,
  },
```

The default environment is `node`; a DOM-rendering test opts into the simulated
page per file (with a `jsdom` pragma — jsdom is a JavaScript implementation of the
Document Object Model that runs without a real browser). A representative example
is the component test for the shared authentication card,
`apps/web/tests/auth-card.test.tsx`, which exercises the component defined in
`apps/web/src/components/auth/auth-card.tsx`. It imports the accessibility engine,
the render and query helpers from Testing Library, and the test functions from
Vitest:

Source: `apps/web/tests/auth-card.test.tsx`

```tsx
import axe from "axe-core";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
```

Inside a test, the card is rendered and then located the way a user would find
it — by a test id for the container and by the accessible role and name for the
form within it:

Source: `apps/web/tests/auth-card.test.tsx`

```tsx
const card = screen.getByTestId("auth-card");
const form = within(card).getByRole("form", { name: "Sign in" });
```

The same file runs an accessibility audit as an ordinary assertion. The helper
below runs the audit against the rendered container, restricted to the Web
Content Accessibility Guidelines (WCAG) rule pack, with the colour-contrast rule
disabled because the simulated page does not load the application's stylesheet:

Source: `apps/web/tests/auth-card.test.tsx`

```tsx
async function runAxe(container: Element): Promise<axe.AxeResults> {
  return axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
}
```

The whole suite is invoked through a package script in `apps/web/package.json`,
which runs Vitest once (rather than in watch mode) and tolerates an empty match:

Source: `apps/web/package.json`

```json
    "test": "vitest run --passWithNoTests",
```

## Common pitfalls

- **Mistake:** Querying for an element by its class name or internal component structure instead of by its visible role, label, or text.
  **Symptom:** A test breaks after a refactor that renamed a class or reorganised the markup, even though the page looks and behaves the same to a user.
  **Recovery:** Query by accessible role, by the label tied to a form field, or by visible text, so the test only fails when user-visible behaviour actually changes.

- **Mistake:** Rendering a DOM-based component test while the file runs in the default `node` environment instead of the simulated-page environment.
  **Symptom:** The test throws an error that `document` or `window` is not defined, because there is no Document Object Model in a plain `node` environment.
  **Recovery:** Opt the file into the simulated page (the `jsdom` environment) with the file-level pragma, so the render helper has a DOM to mount into.

- **Mistake:** Forgetting to clear the rendered output between tests in the same file.
  **Symptom:** A query unexpectedly finds two matching elements and fails, because the previous test's component is still mounted in the page.
  **Recovery:** Run the cleanup step after each test so every test starts from an empty page with no leftover elements.

- **Mistake:** Using a throwing query to assert that an element is absent.
  **Symptom:** The test errors out at the query call with "unable to find an element" before it can make the assertion, so the failure message is confusing.
  **Recovery:** Use the query style that returns an empty result for absence checks, and reserve the throwing style for elements that must exist.

## Hands-on checkpoint

Spend about ten minutes proving to yourself that the runner and the queries work
end to end. From the repository root, run the web app's test suite once through
its command-line interface (CLI) with `pnpm --filter @matchlayer/web test`. The
observable artifact is the terminal report listing each test file and a final
count of passing tests. Then open the authentication-card test, change one
expected accessible name (for example, the form's `"Sign in"` name) to a wrong
value, and rerun the command. You should now see exactly one failing test with a
message contrasting the expected name against what was found — confirming that the
query targets user-visible semantics. Revert your change and rerun to return the
suite to green.

## External reading

- [Vitest: Getting Started guide](https://vitest.dev/guide/)
- [Vitest: configuration reference](https://vitest.dev/config/)
- [Testing Library: React Testing Library introduction](https://testing-library.com/docs/react-testing-library/intro/)
- [Testing Library: about queries](https://testing-library.com/docs/queries/about/)
