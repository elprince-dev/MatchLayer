# Playwright for end-to-end testing

## Introduction

This document explains how an automated test can drive a real web browser
through a running application the way a person would — opening a page, reading
what renders, clicking, and checking the result — and how the Playwright tool
makes that reliable. Playwright is a browser-automation framework (a framework
is a reusable scaffold of code you build inside rather than write from scratch)
that launches a real browser, navigates to your app, and lets a test assert on
what the page actually shows. A test written this way is an end-to-end (E2E)
test: it exercises the whole stack — browser, page markup, styling, and any
back-end the page talks to — from the outside, instead of testing one function
in isolation. This is the broadest and most realistic kind of automated test,
and also the slowest, so it is reserved for the handful of flows that matter
most.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an end-to-end test is and how it differs from a unit test that exercises a single function in memory. An end-to-end test drives the assembled, running product through a browser.
- Describe how a browser-automation framework launches a browser, navigates to a page, locates elements, and waits for them before asserting. The framework hides the timing complexity behind a small set of commands.
- Read a Playwright configuration file and a representative test, and say what each part controls. The configuration decides where tests live, which browsers and screen sizes run, and how a server is started for the run.
- Recognise the common mistakes that make end-to-end tests slow or flaky and recover from them. Most flakiness comes from racing the page instead of waiting for it.

Prerequisites — read these Topic_Docs first, because this document builds on
the application they describe:

- [The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md) — the test renders pages this framework produces.
- [The Next.js standalone build output](01-foundations-11-nextjs-standalone-build.md) — the gates serve that build, so the way it is produced matters here.

## Problem it solves

A unit test (a test that calls one function directly and checks its return
value) can prove a single piece of logic is correct, but it can never prove that
the assembled product works when a person opens it in a browser. The wiring
between pieces is where real defects hide: a button that calls the wrong
handler, a page that renders blank because a script failed to load, a layout
that overflows the screen on a phone, or a field that should stay hidden but
leaks into the page. None of those show up in a function-level test, because no
function-level test ever loads the real page in a real browser.

The concrete problem is therefore: how do you check that the running
application — the actual page a user sees, served the way it is in production —
behaves correctly, automatically, on every change, without a human clicking
through it by hand?

The earliest approach was manual testing: a person opens each page and looks.
That does not scale and is forgotten under deadline pressure. The next approach
used older browser-automation drivers that controlled a browser through a
separate process; they worked but were notoriously flaky, because the test and
the browser ran out of step and the test would check for an element before the
page had finished drawing it. A modern framework solves both: it automates the
browser so checks run on every change, and it removes most flakiness by waiting
for the page to be ready before each action.

## Mental model

Think of an end-to-end test as a robot tester sitting at a real computer. You
hand the robot a short script — "open this page, find the score, confirm it
reads 85, take a photo" — and the robot performs each step against a genuine
browser, then reports pass or fail. The robot is patient by default: when you
tell it to click a button that has not appeared yet, it waits a moment and
retries rather than failing instantly.

When a single test runs, the sequence is:

1. The framework launches a browser and, when the test run manages it, also starts a server so the application is actually reachable at a web address.
2. The test tells the browser to navigate to a page, and the framework waits until the page has loaded before continuing.
3. The test describes an element it cares about — by its role, its text, or a test-only marker — producing a locator, which is a lazy handle to that element rather than the element itself.
4. The test performs an action or an assertion through that locator; the framework automatically waits for the element to exist and be ready before acting, retrying for a short window so a slightly-late element does not fail the test.
5. The framework records the outcome, optionally captures a screenshot or a trace for debugging, and tears the browser (and any server it started) down.

Steps 2 and 4 are the heart of the reliability story: the framework waits for
the page on your behalf, so a correct test does not need hand-written sleeps.

## How it works

A browser-automation framework controls a real browser the way a person would,
but through code. It can launch the browser with no visible window — called
headless mode, which is faster and suits an unattended run — or with a window
when a developer wants to watch. Once a browser is running, the framework opens
a page and drives it: navigate to an address, read text, click, type, and
inspect the rendered result.

The central abstraction is the locator: a description of an element (for
example, "the button whose accessible name is Submit") that is resolved to a
concrete element only at the moment an action runs. Because resolution is
deferred, the framework can implement auto-waiting — before it clicks or asserts,
it repeatedly checks that the element is present, visible, and stable, retrying
for a bounded time. Auto-waiting is what removes the classic source of
flakiness, where a test checks the page a fraction of a second before the page
is ready. Locators are usually chosen by user-visible traits — an element's
accessibility role, its visible text, or an explicit test marker attached to the
element — rather than by fragile position, so the test keeps passing when
unrelated markup changes.

Assertions in this style read the live page. A test can ask the browser to
evaluate a small piece of code inside the page — reaching into the Document
Object Model (DOM), the in-memory tree the browser builds from the page's
markup — and return a value the test then checks. That is how a test confirms,
say, that the page is not wider than the screen, or that a forbidden word never
appears in the rendered text.

A test run is configured rather than hard-coded. Configuration typically
declares where the test files live, which browsers and which screen sizes
(viewports) to run, how many times to retry a failed test, and how to start the
application before the run so the browser has something to talk to. Running the
same tests across several browser engines and viewport sizes is how one suite
guards desktop and mobile at once. Because end-to-end tests are slow relative to
unit tests, a run parallelises across files and a Continuous Integration (CI)
server — the shared machine that runs the test suite automatically on every
change — usually pins the settings for determinism. Some frameworks also support
visual testing: capture a screenshot, store it as a baseline, and fail a later
run when the page drifts from that baseline beyond a small tolerance.

## MatchLayer Phase 1 usage

In MatchLayer the Playwright setup lives beside the web app. The configuration
file is `apps/web/playwright.config.ts`, and the end-to-end specs sit in a
sibling folder it points at. The opening of the configuration declares where the
specs live and how a run behaves:

Source: `apps/web/playwright.config.ts`

```typescript
export default defineConfig({
  // Visual/layout acceptance gates live here (design Section 9.1).
  testDir: "./tests/visual",

  // Run specs within a file in order; parallelize across files.
  fullyParallel: true,
  // Fail the CI build if a `test.only` is committed by accident.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
```

`testDir` names the folder Playwright collects specs from; `fullyParallel` lets
files run side by side; `forbidOnly` makes the run fail if a developer commits a
focused-only test by accident; and `retries` re-runs a failed test twice on the
shared Continuous Integration (CI) machine to absorb rare non-determinism while
keeping local runs strict. The same file declares the browser-and-viewport
combinations to run, each one a named project:

Source: `apps/web/playwright.config.ts`

```typescript
  projects: [
    {
      name: "desktop-1280",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 720 },
      },
    },
```

A representative end-to-end spec is `apps/web/tests/visual/results.spec.ts`,
which drives the flagship results page. It defines a small helper that runs code
inside the live browser page to check the layout never overflows horizontally:

Source: `apps/web/tests/visual/results.spec.ts`

```typescript
/** `true` when no element overflows the viewport horizontally (Req 14.4, 18.1). */
async function hasNoHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth <= doc.clientWidth;
  });
}
```

`page.evaluate` ships the inner function into the browser, where it reads the
real rendered geometry from the Document Object Model (DOM) and returns a boolean
the test asserts on — a check impossible without a real browser. The run is
wired to a build-and-serve step through a script in `apps/web/package.json`, so
the suite always measures the production output rather than a development server:

Source: `apps/web/package.json`

```json
    "test:visual": "next build && playwright test",
```

Because these gates render the production build, the run serves the app through
the generated standalone server (`apps/web/tests/visual/serve-standalone.mjs`)
rather than a development server, which is why the standalone build output is a
prerequisite for understanding this setup.

## Common pitfalls

- **Mistake:** Inserting a fixed pause (a hard-coded "wait two seconds") before checking an element, instead of letting the framework wait for the element.
  **Symptom:** The test passes on a fast machine but fails intermittently on a slower or busier one, because two seconds was sometimes not enough; on fast machines it is needlessly slow.
  **Recovery:** Delete the fixed pause and assert through a locator; the framework auto-waits for the element to be ready, which is both faster and reliable.

- **Mistake:** Locating elements by brittle details such as a generated class name or the element's position in the markup.
  **Symptom:** An unrelated styling or layout change breaks the test even though the feature still works, so the suite cries wolf and developers learn to ignore it.
  **Recovery:** Locate by user-visible traits — accessible role, visible text, or an explicit test marker — so the test tracks behaviour, not incidental markup.

- **Mistake:** Pointing the end-to-end run at a development server instead of the production build.
  **Symptom:** The test measures a layout, bundle, or behaviour that differs from what users get, so it passes while the real deployment is broken (or fails on a dev-only overlay that never ships).
  **Recovery:** Build the app and serve that output for the run, so the browser exercises the same artifact that ships to users.

- **Mistake:** Treating end-to-end tests as the place to cover every input and edge case, mirroring what unit tests already check.
  **Symptom:** The suite grows huge and slow, every change waits minutes for feedback, and the Continuous Integration (CI) run becomes a bottleneck.
  **Recovery:** Keep end-to-end tests to a few high-value flows; push exhaustive case coverage down to fast unit tests and reserve the browser for whole-flow confidence.

## External reading

- [Playwright: Getting started](https://playwright.dev/docs/intro)
- [Playwright: Writing tests](https://playwright.dev/docs/writing-tests)
- [Playwright: Test configuration](https://playwright.dev/docs/test-configuration)
- [Playwright: Continuous Integration](https://playwright.dev/docs/ci)
