# WCAG AA color contrast in practice

## Introduction

This document explains how to tell whether the text and controls in an interface
are readable enough for people with low vision, and how the project keeps its
colours above that bar. The standard is the Web Content Accessibility
Guidelines (WCAG), an international specification for accessible web content,
and within it the "AA" conformance level (AA), a middle tier of requirements
that most products
aim to meet. The specific rule this document focuses on is colour contrast: the
measured difference in lightness between a foreground colour (such as text) and
the background behind it. The practice is to compute that difference for each
colour pairing and check it against a numeric threshold.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a contrast ratio is and why a higher ratio means more readable text. The ratio compares the relative lightness of two colours.
- State the AA thresholds for normal text, large text, and user-interface elements, and when each applies. The required number depends on the size and role of what is being coloured.
- Read code that converts a stored colour value to a contrast ratio and checks it against the threshold. The check turns a design rule into a test that fails on regression.
- Recognise the common mistakes around colour contrast and recover from them. Most defects come from relying on colour alone or testing only one appearance.

Prerequisites: this document builds on
[Tailwind CSS v4 and the theme-token strategy](02-frontend-03-tailwind-v4-and-theme-tokens.md),
which introduces the design tokens (named colour values, stored once and
referenced everywhere) and the light and dark sets whose contrast this document
checks.

## Problem it solves

Text that is too close in lightness to its background is hard or impossible to
read for people with low vision, colour-vision deficiencies, or anyone using a
screen in bright sunlight. The concrete problem is deciding, objectively,
whether a given pairing of text colour and background colour is readable enough —
and proving it stays readable as the palette evolves.

The common prior approach judged contrast by eye. A designer looked at a mockup,
decided it "looked fine", and shipped it. That subjective judgement fails the
readers who most need contrast, because a colour that looks acceptable to someone
with full vision can be unreadable to someone without it. It also drifts silently:
a later tweak to a brand colour can quietly push a pairing below the readable
threshold with nobody noticing.

A measurable standard solves this. The accessibility guidelines define a precise
formula that turns any two colours into a single contrast ratio, and a set of
numeric thresholds that ratio must clear. Because it is a formula, it can be
computed automatically and checked in a test, so contrast becomes a fact that is
verified rather than an opinion that is hoped for.

## Mental model

Think of contrast like the difference in height between two stacked blocks. If
the blocks are nearly the same height, you can barely tell where one ends and the
other begins; the bigger the height difference, the more obvious the boundary.
Contrast ratio measures the "height difference" in lightness between a colour and
its background — and the standard says the difference must be at least a certain
amount before text drawn on that background counts as readable.

When you check a pairing, the procedure is:

1. Take the two colours involved — the foreground (usually text) and the background directly behind it.
2. Convert each colour to a single lightness number using the standard's relative-luminance formula, which weights the red, green, and blue parts the way the human eye perceives them.
3. Form the ratio of the lighter number to the darker number, adjusted by a small constant, giving a value from 1 (identical) up to 21 (black on white).
4. Compare that ratio to the threshold for the text's size and role: normal text needs the most contrast, while large text and user-interface elements are allowed a little less.
5. If the ratio meets or beats the threshold, the pairing passes; if not, either adjust a colour or restrict that pairing to a use that allows a lower threshold.

Steps 2 and 3 are pure arithmetic, which is exactly why the check can be
automated and pinned in a test.

## How it works

The accessibility guidelines define contrast as a ratio computed from the
relative luminance of two colours. Relative luminance is a single number
representing how light a colour appears, computed by first "linearising" each of
the red, green, and blue channels (undoing the curve that display encodings
apply) and then combining them with fixed weights — green counts most, blue
least — because the human eye is most sensitive to green and least to blue. The
contrast ratio is then the lighter colour's luminance plus a small constant,
divided by the darker colour's luminance plus the same constant. The result runs
from 1 to 1 (no difference at all) up to 21 to 1 (pure black against pure white).

The standard sets different thresholds for different content, because larger and
bolder shapes are legible at lower contrast than small body text. Normal-size
text must reach a ratio of 4.5 to 1 at the AA level. Large text — roughly
headline size, or smaller if bold — is allowed a lower bar of 3 to 1, because its
larger strokes remain legible with less contrast. User-interface components and
meaningful graphical shapes (the edge of a button, the fill of an indicator)
share that same 3 to 1 bar. A focus outline, which marks the element a keyboard
user is currently on, is a graphical object and so must clear 3 to 1 against the
surface beside it.

Because the ratio is a deterministic formula, a project can store each colour as
its raw red-green-blue channel values and compute every pairing's ratio in an
automated check. The check linearises the channels, computes luminance, forms the
ratio, and asserts it clears the threshold assigned to that pairing's role. A
particularly useful refinement is to also assert that pairings the design has
deliberately deemed _unsuitable_ for text really do fall below the bar — so that
a future colour change which accidentally made an indicator-only colour look
"safe enough" for text would be caught rather than silently accepted.

One more practical rule: contrast must hold in every appearance the interface
offers. A light appearance and a dark appearance use different colour values for
the same named role, so each pairing has to be checked in both. A colour that is
readable in one can fail in the other, so passing a single appearance proves
nothing about the other.

## MatchLayer Phase 1 usage

In MatchLayer the colour values live as design tokens in the global stylesheet,
`apps/web/src/app/globals.css`, stored as raw red-green-blue channel triplets.
The light set sits under the document root — for example the primary text colour
and the page background:

Source: `apps/web/src/app/globals.css`

```text
  /* Text */
  --color-text: 10 10 11;
  --color-text-muted: 82 82 91;
  --color-text-subtle: 113 113 122;
```

The dark set redefines the same token names with different channel values, so
the same role has to be contrast-checked separately in dark:

Source: `apps/web/src/app/globals.css`

```text
  /* Text */
  --color-text: 244 244 245;
  --color-text-muted: 161 161 170;
  --color-text-subtle: 113 113 122;
```

Those token values are verified against the AA thresholds by an automated test,
`apps/web/tests/contrast.test.ts`, which parses the triplets straight from the
stylesheet and computes each ratio. The thresholds it enforces are exactly the
guideline values — 4.5 to 1 for normal text and 3 to 1 for large text and
user-interface elements:

Source: `apps/web/tests/contrast.test.ts`

```typescript
/** Normal-text AA minimum (below 18pt / 14pt-bold). */
const AA_NORMAL = 4.5;
/** Large-text / UI-component / graphical-object AA minimum. */
const AA_LARGE_UI = 3.0;
```

The ratio itself is computed with the guidelines' own formula — the lighter
luminance plus a constant over the darker luminance plus the same constant:

Source: `apps/web/tests/contrast.test.ts`

```typescript
/** WCAG contrast ratio between two colors (order-independent: (L₁+.05)/(L₂+.05)). */
function contrastRatio(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}
```

Because the test reads the same token file the application ships and runs in both
the light and dark sets, a future change to any colour triplet that pushed a
text pairing below 4.5 to 1 would fail the test immediately, keeping the palette
honest rather than relying on a visual review.

## Common pitfalls

- **Mistake:** Using colour as the only way to convey meaning — for example, marking an error in red text with no other signal.
  **Symptom:** A reader with a colour-vision deficiency cannot tell the error state from a normal state, because the only difference is a hue they cannot distinguish.
  **Recovery:** Pair colour with a second cue (an icon, a label, a border, or text) so the meaning survives even when the colour does not, and keep the text itself above the contrast threshold.

- **Mistake:** Checking contrast in only one appearance and assuming the other is fine.
  **Symptom:** Text is perfectly readable in the light appearance but washed out in the dark one (or the reverse), because the two use different colour values for the same role.
  **Recovery:** Compute every pairing's ratio in both the light and dark sets, treating them as separate checks that must each pass.

- **Mistake:** Using a low-contrast accent colour (such as a decorative or gradient colour) as small body text.
  **Symptom:** The text is hard to read against the background and the contrast ratio falls below 4.5 to 1, even though the colour looks attractive in a heading or a graphic.
  **Recovery:** Reserve low-contrast accent colours for large text, decorative fills, or graphical elements that only need the 3 to 1 bar, and use a high-contrast token for body text.

- **Mistake:** Removing a focus outline because it clashes with the design, or replacing it with one too faint to see.
  **Symptom:** A keyboard user cannot tell which element is currently focused, and the focus indicator fails the 3 to 1 user-interface threshold against its surroundings.
  **Recovery:** Keep a visible focus indicator whose colour clears the 3 to 1 bar against the adjacent surface, restyling rather than removing it.

## External reading

- [W3C: WCAG Understanding Success Criterion 1.4.3 Contrast (Minimum)](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html)
- [W3C: WCAG Understanding Success Criterion 1.4.11 Non-text Contrast](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html)
- [W3C: relative luminance definition](https://www.w3.org/WAI/GL/wiki/Relative_luminance)
- [MDN Web Docs: Color contrast and accessibility](https://developer.mozilla.org/en-US/docs/Web/Accessibility/Guides/Understanding_WCAG/Perceivable/Color_contrast)
