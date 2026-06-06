import { test, expect, type Page } from "@playwright/test";

import {
  THEMES,
  assertBodyBackgroundMatchesToken,
  assertNoDevOverlay,
  assertNoHorizontalScrollAcrossWidths,
  assertTouchTargetAtLeast44,
  gotoWithTheme,
  hasNoHorizontalScroll,
} from "./visual-helpers";

/**
 * Landing page (`/`) — Playwright visual/layout acceptance gates (task 9.2;
 * design Section 8.2, 9.2, 9.4; Req 7.2, 14.4, 18.1, 18.2).
 *
 * The Landing_Page is Public and indexable, so this gate visits `/` directly.
 * Per the task it asserts the cross-screen gates — no horizontal scrollbar
 * across 320–1920px (and at 390px), correct dark AND light rendering with a
 * committed screenshot per (viewport × theme), and a ≥44px touch target on the
 * primary action — **plus** the two Landing-specific semantic gates: exactly
 * one `<h1>` and the `header/nav/main/footer` landmark structure (Req 7.2).
 *
 * Determinism: the config emulates `prefers-reduced-motion: reduce`, so the
 * hero staggered fade-up, the demo gauge count-up, and every `SectionReveal`
 * scroll fade-up render in their final, visible state immediately (Req 4.9,
 * 3.8) — making the full-page screenshot stable rather than mid-animation. The
 * fixed GlassNav sits at its transparent-over-hero state at the top of the page
 * (scroll position 0), which is the representative captured frame.
 */

const LANDING_PATH = "/";

/** Assert the real landing composition rendered (hero H1 + glass nav CTA),
 *  the precondition for the layout/screenshot gates. */
async function expectLandingRendered(page: Page): Promise<void> {
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "See how real ATS systems evaluate your resume",
    }),
  ).toBeVisible();
}

/**
 * Assert the Landing_Page semantic structure (Req 7.2; design Section 10.3):
 * exactly one `<h1>` and the four landmark roles all present.
 *
 * Uses ARIA **role** locators rather than raw element selectors:
 *   - `banner`      — the GlassNav `<header>` (the page's top-level header).
 *   - `navigation`  — the GlassNav primary nav + the footer nav (≥1 present).
 *   - `main`        — the page `<main id="main">` content landmark.
 *   - `contentinfo` — the site `<footer>`.
 *
 * Role locators are the correct semantic contract here (the gate is asserting
 * landmarks, not tag names) and are robust against any non-landmark element
 * that happens to use the same tag — e.g. a `<footer>` injected by tooling
 * does not carry the `contentinfo` role unless it is a top-level footer, so
 * `getByRole("contentinfo")` stays unambiguous where `locator("footer")` would
 * not. `banner`/`contentinfo`/`main` are unique per page, so `toBeVisible()`
 * holds without a `.first()` disambiguator.
 */
async function assertLandingSemantics(page: Page): Promise<void> {
  // Exactly one <h1> on the page (Req 7.2).
  await expect(page.locator("h1")).toHaveCount(1);

  // Each landmark role renders at least once (Req 7.2). navigation can be >1
  // (primary nav + footer nav); the rest are unique per page.
  expect(
    await page.getByRole("navigation").count(),
    "nav landmark must render",
  ).toBeGreaterThanOrEqual(1);

  // The three unique landmarks are present and visible.
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("contentinfo")).toBeVisible();
}

for (const theme of THEMES) {
  test(`landing layout + screenshot — ${theme}`, async ({ page }, testInfo) => {
    await gotoWithTheme(page, LANDING_PATH, theme);
    await expectLandingRendered(page);

    // Landing-specific semantic gates: one <h1> + header/nav/main/footer.
    await assertLandingSemantics(page);

    // No horizontal scroll at the active project's viewport (Req 14.4, 18.1).
    // The full 320–1920 sweep runs in its own test below.
    expect(await hasNoHorizontalScroll(page)).toBe(true);

    // Correct per-theme rendering: body paints the resolved --color-bg token
    // (Req 14.6, 19.1) — dark and light genuinely differ.
    await assertBodyBackgroundMatchesToken(page);

    // Primary action: the hero "Get started — it's free" CTA → /register clears
    // the ≥44px touch-target floor (Req 18.2, 3.3). Scoped to #hero to
    // disambiguate from the identically-named final-CTA button.
    await assertTouchTargetAtLeast44(
      page.locator("#hero").getByRole("link", { name: /Get started/ }),
    );

    // Committed baseline per (viewport × theme), excluding the assert-only 1920
    // project (design 9.2, 9.4).
    if (testInfo.project.name !== "desktop-1920") {
      await assertNoDevOverlay(page);
      await expect(page).toHaveScreenshot(`landing-${theme}.png`, {
        fullPage: true,
      });
    }
  });
}

// No-horizontal-scroll across the full responsive range (Req 18.1: 320–1920px).
// Resizing is isolated from the screenshot tests so it never disturbs their
// fixed-viewport captures.
test("landing — no horizontal scroll across 320–1920px", async ({ page }) => {
  await gotoWithTheme(page, LANDING_PATH, "dark");
  await expectLandingRendered(page);
  await assertNoHorizontalScrollAcrossWidths(page);
});
