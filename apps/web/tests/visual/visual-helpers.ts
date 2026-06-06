import { expect, type Locator, type Page } from "@playwright/test";

/**
 * Shared helpers for the Upload / Auth / Landing Playwright visual-layout gates
 * (task 9.2; design Section 9.2, 9.4; Req 7.2, 8.6, 9.13, 14.4, 18.1, 18.2).
 *
 * The flagship ATS Results gates (task 9.1, `results.spec.ts`) inline their own
 * helpers because they assert a large, screen-specific set of layout invariants
 * (above-the-fold geometry, the no-PII-on-the-wire sniffer, the degenerate
 * danger-token check). The three secondary screens here share the **same small
 * set** of cross-screen gates — no horizontal scroll across the responsive
 * range, correct per-theme background, a committed screenshot per
 * (screen × viewport × theme), and a ≥44px touch target on the primary action —
 * so those are factored out here and imported by all three specs to keep them
 * DRY and consistent.
 *
 * This module deliberately has no `.spec`/`.test` suffix, so Playwright's
 * default `testMatch` never collects it as a test file (it lives alongside the
 * specs in `tests/visual/` only for colocation).
 */

/**
 * Both themes every screen-level gate runs in (design Section 9.3 #6, Req 14.6,
 * 19.1). next-themes resolves the active theme from `localStorage["theme"]`, so
 * each value here is seeded before navigation by {@link gotoWithTheme}.
 */
export const THEMES = ["dark", "light"] as const;
export type Theme = (typeof THEMES)[number];

/**
 * Representative viewport widths spanning the responsive range the
 * no-horizontal-scroll invariant must hold across. The task requires coverage
 * "across 320–1920px (and at 390px)": 320 is the Req 18.1 mobile floor, 390 the
 * mobile project width, 768/1024 the layout breakpoints, and 1280/1440/1920 the
 * Req 14.4 desktop widths. The Playwright projects only cover 390/1280/1440/1920
 * natively, so the sweep adds the 320px floor (and the intermediate widths) that
 * no project provides.
 */
export const NO_SCROLL_WIDTHS = [
  320, 360, 390, 414, 768, 1024, 1280, 1440, 1920,
] as const;

/** Height used while sweeping widths — tall enough to render a full row of
 *  content without forcing artificial vertical layout at narrow widths. */
const SWEEP_HEIGHT = 900;

/** WCAG 2.5.5 / design touch-target floor in CSS px (Req 18.2, 4.5, 3.3). */
const MIN_TOUCH_TARGET_PX = 44;

/**
 * Seed the theme + reduced motion, navigate to `path`, and confirm the resolved
 * theme is painted.
 *
 * `next-themes` reads `localStorage["theme"]` in its pre-paint script and
 * toggles the `.dark` class on `<html>`, so seeding the key via `addInitScript`
 * BEFORE navigation pins the rendered theme with no wrong-theme frame (the same
 * mechanism the flagship `results.spec.ts` uses). Reduced motion is emulated so
 * every scroll-reveal / entrance / background animation renders in its final
 * state instantly (design Section 10.5) — the stable representative frame for a
 * baseline screenshot, and the same geometry the layout gates measure.
 */
export async function gotoWithTheme(
  page: Page,
  path: string,
  theme: Theme,
): Promise<void> {
  await page.addInitScript((t) => {
    try {
      window.localStorage.setItem("theme", t);
    } catch {
      /* localStorage unavailable — the default (dark) still renders. */
    }
  }, theme);

  await page.emulateMedia({ reducedMotion: "reduce" });

  await page.goto(path);
  await page.waitForLoadState("networkidle");

  // Pin the resolved theme: `.dark` present for dark, absent for light. This is
  // also the precondition for the per-theme body-background assertion.
  if (theme === "dark") {
    await expect(page.locator("html")).toHaveClass(/(^|\s)dark(\s|$)/);
  } else {
    await expect(page.locator("html")).not.toHaveClass(/(^|\s)dark(\s|$)/);
  }
}

/** `true` when nothing overflows the viewport horizontally (Req 14.4, 18.1). */
export async function hasNoHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth <= doc.clientWidth;
  });
}

/**
 * Assert the rendered `<body>` background equals the active theme's
 * `--color-bg` token (design 9.3 #6; Req 14.6, 19.1). The root `<body>` uses
 * the `bg-bg` utility → `rgb(var(--color-bg))`, so this pins that the theme's
 * surface token is actually painted (dark `rgb(10, 10, 11)` / light
 * `rgb(255, 255, 255)`), i.e. dark vs light really differ.
 */
export async function assertBodyBackgroundMatchesToken(
  page: Page,
): Promise<void> {
  const { bodyBg, expected } = await page.evaluate(() => {
    const triplet = getComputedStyle(document.documentElement)
      .getPropertyValue("--color-bg")
      .trim();
    const [r, g, b] = triplet.split(/\s+/).map(Number);
    return {
      bodyBg: getComputedStyle(document.body).backgroundColor,
      expected: `rgb(${r}, ${g}, ${b})`,
    };
  });
  expect(bodyBg).toBe(expected);
}

/**
 * Sweep the responsive width range and assert no horizontal scrollbar appears
 * at any of them (Req 18.1 across 320–1920px; Req 14.4 at 1280/1440/1920). This
 * mutates the viewport, so callers run it in a **dedicated** test (never the one
 * that also captures a screenshot). A single `requestAnimationFrame` tick lets
 * any width-driven listeners (e.g. the GlassNav scroll/resize store) settle
 * before the measurement.
 */
export async function assertNoHorizontalScrollAcrossWidths(
  page: Page,
): Promise<void> {
  for (const width of NO_SCROLL_WIDTHS) {
    await page.setViewportSize({ width, height: SWEEP_HEIGHT });
    await page.evaluate(
      () =>
        new Promise((resolve) => requestAnimationFrame(() => resolve(null))),
    );
    const ok = await hasNoHorizontalScroll(page);
    expect(ok, `no horizontal scroll at ${width}px`).toBe(true);
  }
}

/**
 * Assert no Next.js dev/error overlay is mounted on the page.
 *
 * Next.js renders its dev-tools indicator, error overlay, and toasts inside
 * `<nextjs-portal>` custom elements (and the error overlay specifically adds a
 * `footer.error-overlay-footer`). Those must never appear on the production
 * standalone build the gates run against — if one does, it would corrupt a
 * `--update-snapshots` baseline (which never fails) by baking the overlay into
 * the committed image. Asserting their absence makes any stray overlay a loud,
 * immediate failure instead of a silent baseline corruption, and is the
 * precondition for a trustworthy screenshot capture. (Observed once as a
 * transient during a cold server start; a steady-state production render has
 * zero such portals.)
 */
export async function assertNoDevOverlay(page: Page): Promise<void> {
  expect(
    await page.locator("nextjs-portal").count(),
    "no Next.js dev/error overlay portal must be mounted",
  ).toBe(0);
  expect(
    await page.locator("footer.error-overlay-footer").count(),
    "no Next.js error overlay must be mounted",
  ).toBe(0);
}

/**
 * Assert an interactive element meets the ≥44px touch-target minimum on its
 * primary axis (Req 18.2, 4.5, 3.3). Buttons/links use a fixed `h-11` (44px)
 * height per the design, so the height is the binding dimension; rounding
 * guards against sub-pixel layout noise reporting e.g. 43.99.
 */
export async function assertTouchTargetAtLeast44(
  target: Locator,
): Promise<void> {
  await expect(target).toBeVisible();
  const box = await target.boundingBox();
  expect(box).not.toBeNull();
  expect(Math.round(box!.height)).toBeGreaterThanOrEqual(MIN_TOUCH_TARGET_PX);
}
