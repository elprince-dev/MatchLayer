# Test taxonomy and layers

## Introduction

This document maps out the different _kinds_ of automated test the project runs
and explains what each kind is responsible for proving. A test layer (a category
of test grouped by what it exercises and how it runs) is the unit of
organization here: rather than one undifferentiated pile of tests, the suite is
split into layers — unit, integration, property, smoke, end-to-end,
accessibility, and timing — that each answer a different question about the
software. Knowing which layer a test belongs to tells you how fast it runs, what
it depends on, what failure of it means, and where in the directory tree it
lives. This document is the map; the sibling documents for each individual tool
go deeper on one layer at a time.

**Learning outcomes** — after reading this document you will be able to:

- Name the seven test layers used in Phase 1 and state, in one sentence, what each one verifies.
- Explain why fast, dependency-free tests vastly outnumber slow, full-stack tests, using the test-pyramid mental model.
- Tell the difference between a layer defined by _scope_ (unit versus integration versus end-to-end) and a category defined by _technique or concern_ (property, smoke, accessibility, timing).
- Locate where a test of a given layer belongs in the repository's test directory tree.

Prerequisites: No prerequisites. This document defines every test-layer term on
first use and is written for a reader who has never organized a test suite
before.

## Problem it solves

A project of any size accumulates hundreds of tests. If they are all thrown into
one folder and run the same way, several concrete problems appear. You cannot
tell, from a red build, whether a pure piece of arithmetic broke or whether a
whole user flow through the browser fell over. You cannot run "the quick checks"
during development and defer "the slow checks" to a server, because nothing
labels which is which. And two tests that fail for completely different reasons —
one because a function returns the wrong number, one because a database is
unreachable — look identical in the output.

Before a taxonomy is adopted, the common prior state is exactly that: an
undifferentiated bag of tests, all executed by a single command, all treated as
equally fast and equally trustworthy. In that world a developer waits minutes for
a real database and a real browser to spin up before learning that a one-line
formula was wrong, so the feedback loop that should take seconds takes minutes.
Teams respond by running the suite less often, which is the opposite of what
tests are for.

A test taxonomy fixes this by giving every test a named layer. Each layer has a
defined scope, a defined set of dependencies, a defined speed expectation, and a
defined meaning when it fails. That lets the fast layers run constantly and the
slow layers run deliberately, and it lets a failure point straight at the kind of
thing that broke.

## Mental model

The classic picture is the **test pyramid**: a triangle, wide at the bottom and
narrow at the top, where height represents how much of the system a test
exercises and width represents how many tests of that kind you should have.

Walk the pyramid from the bottom up:

1. **Unit tests** form the wide base. A unit test exercises one small piece of code — a single function or class — in isolation, with no database, network, or browser. They are tiny and run in milliseconds, so you write many of them and run them constantly.
2. **Integration tests** sit in the middle. An integration test exercises several pieces working together against a real dependency such as a database, so it is slower and you write fewer of them — enough to prove the wiring between parts is correct.
3. **End-to-end tests** form the narrow tip. An end-to-end test drives the whole assembled application the way a user would, through a real browser, so it is the slowest and most fragile kind; you keep only a handful covering the most important journeys.

Three more categories cut _across_ the pyramid rather than stacking on it,
because they are defined by a technique or a concern instead of by scope:
**property** tests (assert a rule holds across many generated inputs), **smoke**
tests (a shallow "does it even start" check), **accessibility** tests (verify the
interface is usable by assistive technology), and **timing** tests (verify a
response time stays within a budget). Picture these as vertical stripes painted
over the triangle: each can apply at more than one height.

## How it works

A test layer is defined by four properties, and naming the layer fixes all four
at once: its **scope** (how much code runs), its **dependencies** (what must be
available for it to run), its **speed** (how long it takes), and the **meaning of
a failure** (what kind of defect it points at).

Scope is the primary axis, and it is the one the pyramid orders.

- A **unit test** isolates the smallest meaningful piece of code and feeds it inputs directly. Because it touches nothing external, it is deterministic and fast, and a failure means that one piece of logic is wrong — nothing else.
- An **integration test** lets two or more components run together against a real collaborator, most often a database. It is slower because that collaborator must be started, and a failure means the seam _between_ components — the queries, the transactions, the data mapping — is wrong even if each component is individually correct.
- An **end-to-end test** (often shortened to E2E) launches the whole application and drives it from the outside, through a browser, exactly as a person would. It is the slowest and the most sensitive to unrelated change, and a failure means a complete user journey is broken, though it rarely tells you which internal part is at fault.

The cross-cutting categories are defined by technique or concern instead.

- **Property-based testing** is a technique where, instead of asserting one hand-written example, you assert a general rule — a _property_ — and a library generates hundreds of varied inputs trying to break it. A failure hands you the specific input that violated the rule.
- A **smoke test** is a deliberately shallow check that the system comes up and answers at all — the testing equivalent of switching a device on to see if smoke pours out. A failure means the build is fundamentally broken and deeper tests are not worth running yet.
- An **accessibility test** checks that the rendered interface carries the structure assistive technology relies on — correct roles, names, and heading order under the Web Content Accessibility Guidelines (WCAG). A failure means the interface excludes some users.
- A **timing test** measures how long an operation takes and asserts it stays within a budget. A failure means a performance or security-timing guarantee has regressed.

The same physical test file can belong to a scope layer _and_ a cross-cutting
category — a property test that drives one function is also a unit-scope test, an
accessibility test that renders a whole page is end-to-end in spirit. The
taxonomy is a vocabulary for talking precisely about tests, not a set of
mutually exclusive boxes. The practical payoff is selective execution: the fast,
dependency-free layers run on every save, while the layers that need a database,
a browser, or a quiet machine for stable timing are run on demand or on a build
server.

## MatchLayer Phase 1 usage

In MatchLayer the backend test tree groups tests by layer into sibling
directories. `apps/api/tests/unit/` holds fast, dependency-free unit tests;
`apps/api/tests/integration/` holds tests that run against a real database;
`apps/api/tests/property/` holds the Hypothesis property tests; and
`apps/api/tests/timing/` holds the timing-category tests. Shared backend setup
lives in `apps/api/tests/conftest.py`, and a representative smoke check that the
application boots and answers its health probe lives in
`apps/api/tests/test_health.py`. The frontend keeps its component and browser
tests under `apps/web/tests/`, with the Playwright end-to-end and visual specs
isolated in `apps/web/tests/visual/`.

The backend test runner's configuration names the layers explicitly. It points
the runner at the test tree and registers the `timing` marker — a label
attached to slow, machine-sensitive timing tests so they can be excluded from
the Continuous Integration (CI) run, the automated build that runs on a shared
server:

Source: `apps/api/pyproject.toml`

```text
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "timing: timing-sensitive tests (e.g., login-timing INV-5); excluded from CI via -m 'not timing'",
]
```

The **property** layer asserts rules over generated inputs. The scoring property
below states that the Applicant Tracking System (ATS) match score is always a
bounded integer, and the test framework generates hundreds of resume and
job-description pairs trying to violate that bound:

Source: `apps/api/tests/property/test_score_boundedness.py`

```python
@pytest.mark.parametrize("weights", _WEIGHT_PAIRS)
@settings(max_examples=200, deadline=None)
@given(resume_text=_document, job_description=_document)
def test_score_is_a_bounded_integer(
    weights: tuple[float, float],
    resume_text: str,
    job_description: str,
) -> None:
```

The **integration** layer runs against a real database rather than a stand-in. A
fixture resets the relevant tables before each test by issuing a real Structured
Query Language (SQL) statement against the running database, which only works
because an actual database is present:

Source: `apps/api/tests/integration/conftest.py`

```python
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "TRUNCATE TABLE refresh_tokens, password_reset_tokens, "
                "audit_events, users RESTART IDENTITY CASCADE"
            )
```

The **timing** layer measures wall-clock cost and asserts a budget. Its tests
carry the `timing` marker and are skipped when the database is unreachable, so a
machine without the right environment does not produce a misleading failure. The
discipline behind this layer is explained in
[no-account enumeration](06-auth-10-no-account-enumeration.md):

Source: `apps/api/tests/timing/test_login_timing_local.py`

```python
pytestmark = [
    pytest.mark.timing,
    pytest.mark.skipif(
        not _postgres_available(),
        reason="Postgres not available for timing test",
    ),
]
```

The **accessibility** layer renders the interface and runs an automated audit
against it. The helper below runs the axe-core engine — a library that inspects
rendered markup for accessibility violations — restricted to the foundational
WCAG rule packs (the `wcag2a` and `wcag2aa` tags shown below). The numeric
color-contrast side of accessibility is covered
separately in [the color-contrast document](02-frontend-09-wcag-aa-color-contrast.md):

Source: `apps/web/tests/a11y.test.tsx`

```tsx
async function runAxe(container: Element): Promise<axe.AxeResults> {
  return axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
}
```

The **end-to-end** layer drives the assembled frontend through a real browser.
The Playwright configuration points the browser-driven runner at the visual spec
directory and runs those specs against a built application:

Source: `apps/web/playwright.config.ts`

```typescript
export default defineConfig({
  testDir: "./tests/visual",
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
});
```

Two more anchors round out the taxonomy. The frontend component layer (the
Application Programming Interface (API) of individual components, exercised under
a fast test runner with no browser) is configured in `apps/web/vitest.config.ts`,
and the import-boundary unit test that enforces the package-separation rules
lives at `apps/api/tests/unit/test_import_boundaries.py`. Which layers run in
which automated check is described in [the Phase 1 CI jobs](12-hosting-02-phase-1-ci-jobs.md).

## Common pitfalls

- **Mistake:** Writing an integration or end-to-end test for logic that a unit test could cover, because "more realistic" feels safer.
  **Symptom:** The suite grows slow and flaky; a one-line formula change forces a multi-minute database-and-browser run, and intermittent failures unrelated to the change erode trust in the build.
  **Recovery:** Push each assertion down to the lowest layer that can prove it — pure logic belongs in a unit test — and reserve integration and end-to-end tests for the seams and journeys only they can exercise.

- **Mistake:** Running the timing layer inside the shared Continuous Integration run alongside every other test.
  **Symptom:** The build fails at random because a busy server adds latency noise that blows the sub-millisecond timing budget, even though nothing about the code changed.
  **Recovery:** Mark timing tests with a dedicated marker and exclude them from the default run, executing them deliberately on a quiet machine; the configuration shown above registers exactly such a marker.

- **Mistake:** Treating an accessibility test that finds no violations as proof the interface is fully accessible.
  **Symptom:** An automated audit passes, yet keyboard-only and screen-reader users still hit barriers the tool cannot detect, because automated rules cover only part of the guidelines.
  **Recovery:** Treat the automated accessibility layer as a floor, not a ceiling, and pair it with manual checks — keyboard navigation, a screen-reader pass, and reduced-motion verification — for full coverage.

- **Mistake:** Filing a property test and a unit test as interchangeable, then deleting the worked examples once a property exists.
  **Symptom:** A regression slips through because the generated inputs never happened to hit the specific edge case a removed example pinned down, and debugging a generated counterexample is harder than reading a named example.
  **Recovery:** Keep both — concrete examples document intended behavior and pin known edge cases, while the property asserts the general rule across the wider input space.

## External reading

- [pytest documentation](https://docs.pytest.org/en/stable/)
- [Vitest documentation](https://vitest.dev/)
- [Playwright documentation](https://playwright.dev/docs/intro)
- [Hypothesis documentation](https://hypothesis.readthedocs.io/en/latest/)
- [W3C Web Accessibility Initiative (WAI): Test and evaluate accessibility](https://www.w3.org/WAI/test-evaluate/)
