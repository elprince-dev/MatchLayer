# axe-core accessibility testing

## Introduction

This document explains how an automated accessibility test works and how the
project uses one to guard its screens against common accessibility defects.
Accessibility, in this context, is a measure of how usable an interface is for
people who rely on assistive technologies such as screen readers, keyboard-only
navigation, or screen magnification. The engine covered here is axe-core, an
open-source accessibility rule engine — maintained by Deque Systems — that
inspects rendered markup and reports the places where it breaks an accessibility
rule. The bar those rules encode is the Web Content Accessibility
Guidelines (WCAG), an international specification, published by the World Wide
Web Consortium, for making web content usable by people with disabilities.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an automated accessibility test can check and, importantly, what it cannot check on its own.
- Describe how a rule engine turns a rendered page into a list of violations.
- Read a test that runs the engine over a screen and asserts that it reports no violations.
- Recognise the common mistakes people make when adding accessibility tests, and recover from them.

Prerequisites: this document builds on
[color contrast and conformance thresholds](02-frontend-09-wcag-aa-color-contrast.md), which
introduces the same guidelines from the angle of readable colour, and on
[the reduced-motion accessibility pattern](02-frontend-06-framer-motion-and-reduced-motion.md),
which explains the animation preference that the accessibility test relies on to
render screens in a settled state.

## Problem it solves

The concrete problem is catching accessibility defects — a missing label on an
icon button, a heading level that skips, a control with no accessible name —
before they reach a user who depends on assistive technology. These defects are
easy to introduce and easy to miss, because a sighted developer clicking with a
mouse never encounters them.

The prior approach was manual review alone: a person opens each screen, reads
the markup, perhaps runs a screen reader, and decides whether it looks correct.
That approach has two weaknesses. It is slow, so it tends to happen rarely and
late, and it is inconsistent, because a tired reviewer misses things a fresh one
would catch. Worst of all, it regresses silently — a screen that was accessible
last month can break with an unrelated change, and nobody notices until a user
complains.

An automated accessibility test attacks the cheap, mechanical part of that work.
It encodes a large set of well-defined rules and runs them over the rendered
page on every change, so an entire family of regressions is caught in seconds
rather than discovered in production. It does not replace human review; it
removes the repetitive checks from the human's plate so the review time is spent
on the judgement calls a machine cannot make.

## Mental model

Think of an automated accessibility test like a spell-checker for an interface.
A spell-checker reliably catches mechanical mistakes — a misspelled word, a
doubled space — but it cannot tell you whether the sentence means what you
intended. The accessibility engine is the same: it catches mechanical
accessibility mistakes with high confidence, while whether the experience
actually makes sense to a screen-reader user remains a human judgement.

A single run proceeds in these steps:

1. Render the interface into a tree of elements the way a browser would, producing a structure the engine can walk and inspect.
2. Hand that rendered tree to the rule engine and choose which families of rules to run — for example, the structural rules tied to a particular conformance level.
3. The engine visits each element, evaluates every enabled rule against it, and records each failing element as a violation.
4. Read the resulting list: an empty list means no enabled rule failed, while every entry names the rule that fired, the offending element, and guidance on how to fix it.

The test then asserts the violation list is empty. If a later change introduces
a defect that one of the enabled rules covers, the list stops being empty and
the test fails, pointing straight at the broken element.

## How it works

When a page is rendered, the browser represents it as a Document Object
Model (DOM) — a live, in-memory tree where every element, attribute, and piece
of text is a node the program can read. An accessibility engine takes a node in
that tree as its starting point and walks downward, building a parallel view
called the accessibility tree: the same content as the platform's assistive
technologies would perceive it, with each element reduced to its role (what kind
of thing it is), its accessible name (how it is announced), and its state.

The engine then runs a catalogue of rules against that tree. Each rule is a
small, focused check: every image has a text alternative, every form control has
a label, there is exactly one top-level heading and the levels below it do not
skip, interactive elements have an accessible name, native HyperText Markup
Language (HTML) semantics are used in preference to re-implemented ones, and
Accessible Rich Internet Applications (ARIA) attributes — extra attributes that
describe roles and states to assistive technology — are used correctly. A rule
that fails on an element produces a violation: a record naming the rule, the
element, and a remediation hint.

Rules are grouped into named sets so a caller can choose how strict to be. The
accessibility guidelines define conformance levels, and the engine tags each
rule with the level and version it supports — for example the version-2 Level A
rules and the stricter version-2 double-A rules. The Web Accessibility
Initiative (WAI), the part of the standards body that owns these guidelines,
publishes the mapping from rules to levels, and the caller selects the tags that
match the bar the product is held to.

One rule deserves special mention because it behaves differently from the
others. The colour-contrast rule measures the lightness difference between text
and its background, and to do that it needs the real, computed Cascading Style
Sheets (CSS) values — the actual rendered colours. In a lightweight, headless
rendering environment that applies no stylesheet, those computed colours are
absent, so the contrast rule would measure nothing useful. The standard practice
is to disable the contrast rule in that environment and verify contrast
separately, with a calculation over the real colour values.

The most important thing to understand is the boundary of what this technique
proves. Automated rules cover only a subset of the accessibility guidelines —
the mechanically checkable portion. A green run means none of the enabled
automated rules failed; it does not mean the interface is fully conformant. Full
validation requires manual testing with assistive technologies — a real
screen-reader pass, keyboard-only operation, and zoom-and-reflow checks — and
expert human review. Automated checks and manual review are complementary
layers, and treating the automated layer as the whole story is the central
mistake to avoid.

## MatchLayer Phase 1 usage

In Phase 1 the accessibility engine is pulled in as a development-only
dependency of the web application. The dependency is declared in
`apps/web/package.json`:

Source: `apps/web/package.json`

```json
    "axe-core": "^4.11.0",
```

The accessibility gate itself lives in `apps/web/tests/a11y.test.tsx`. That test
renders each of the four main screens — the results screen, the upload screen,
the authentication screens, and the landing page — in both the dark and light
themes, and for each one runs the engine and asserts there are no violations. A
small helper wraps the engine call so every screen is scanned the same way: it
restricts the run to the WCAG version-2 Level A and double-A structural rule
sets and turns the colour-contrast rule off, because the test renders under a
headless environment with no stylesheet applied:

Source: `apps/web/tests/a11y.test.tsx`

```typescript
async function runAxe(container: Element): Promise<axe.AxeResults> {
  return axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    rules: { "color-contrast": { enabled: false } },
  });
}
```

Because the colour-contrast rule is disabled here, the contrast bar is enforced
in a separate, dedicated test, `apps/web/tests/contrast.test.ts`, which reads the
real design-token colour values and checks each pairing against the guideline
thresholds numerically. The two tests are complementary: the engine covers
structure, roles, names, and semantics, while the contrast test covers colour.
Neither replaces the manual screen-reader and keyboard passes that the test file
documents as still required for full validation.

## Common pitfalls

- **Mistake:** Treating a passing accessibility test as proof that a screen is fully accessible.
  **Symptom:** A screen sails through the automated gate yet a screen-reader user hits an unlabeled control, a keyboard user gets trapped, or reading order is wrong in practice.
  **Recovery:** Keep the automated run as one layer and pair it with the manual checklist — a real screen-reader pass, keyboard-only operation, and zoom-to-reflow — plus expert review before calling the screen done.

- **Mistake:** Leaving the colour-contrast rule enabled in a headless rendering environment that applies no stylesheet.
  **Symptom:** Contrast violations are either missed entirely or reported against blank computed colours, so the result is noise rather than signal.
  **Recovery:** Disable the contrast rule in that environment and verify contrast in a separate calculation over the real colour values, exactly as the dedicated contrast test does.

- **Mistake:** Running the engine before asynchronous content or entrance animations have settled.
  **Symptom:** The test is flaky — it passes sometimes and fails other times — or it scans a loading skeleton instead of the finished screen.
  **Recovery:** Wait for the settled state first, for example by stubbing the reduced-motion preference so animations resolve instantly and by awaiting a stable element before the scan.

- **Mistake:** Scoping the scan to the wrong element, so the markup you care about is outside the scanned subtree.
  **Symptom:** Real violations go unreported because the engine never visited the region that contains them.
  **Recovery:** Pass the rendered root that actually contains the markup under test, and confirm the landmark and heading assertions run against that same subtree.

## Hands-on checkpoint

Time box: about 15 minutes. Open `apps/web/tests/a11y.test.tsx` and read the
`runAxe` helper, noting the two choices it makes: the rule tags it restricts to,
and the one rule it disables. Then run the web application's test suite for that
file — with the package manager's test command scoped to `a11y.test.tsx` — and
read the output. The observable artifact is the passing run: a report showing
the per-screen, per-theme cases all green with an empty violation list. As a
second step, temporarily remove the accessible name from one icon button in a
screen the test covers, re-run, and confirm the engine now reports a violation
that names the rule and the offending element. Restore the change afterwards.

## External reading

- [World Wide Web Consortium, Web Accessibility Initiative: Web Content Accessibility Guidelines overview](https://www.w3.org/WAI/standards-guidelines/wcag/)
- [World Wide Web Consortium, Web Accessibility Initiative: Accessible Rich Internet Applications overview](https://www.w3.org/WAI/standards-guidelines/aria/)
- [World Wide Web Consortium, Web Accessibility Initiative: evaluating accessibility with tools and people](https://www.w3.org/WAI/test-evaluate/)
- [Vitest: test-runner documentation](https://vitest.dev/)
