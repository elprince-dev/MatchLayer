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
 * Upload screen — Playwright visual/layout acceptance gates (task 9.2; design
 * Section 9.2, 9.4; Req 9.13, 14.4, 18.1, 18.2).
 *
 * The real Upload page (`(app)/upload`) sits behind the `(app)` auth shell, so
 * these gates render it through the env-gated, auth-free visual-harness route
 * `app/visual-harness/upload` (enabled only under `PLAYWRIGHT_VISUAL=1`, which
 * the Playwright `webServer` sets). That harness mounts the **production**
 * `UploadPage` inside chrome mirroring the authenticated shell, so the measured
 * layout matches what a logged-in user sees in the page's initial idle state
 * (it issues no network request until a file is chosen / submitted).
 *
 * Per the task, this spec asserts the cross-screen gates: no horizontal
 * scrollbar across 320–1920px (and at 390px), correct dark AND light rendering
 * with a committed screenshot per (viewport × theme), and a ≥44px touch target
 * on the primary action. Determinism comes from the config's reduced-motion
 * emulation + the harness's auth-free, fetch-free initial render.
 *
 * The Upload page's **primary action** is the full-width "Analyze Match" submit
 * button (`h-11` = 44px) — the page's main CTA, matching how the Auth gate
 * measures its submit button and the Landing gate measures its hero CTA. It
 * starts disabled (no resume yet + empty JD), but `disabled` doesn't change the
 * control's box, so its rendered height is still the binding touch-target
 * dimension and is measurable while disabled (`toBeVisible()` is true for a
 * disabled-but-rendered button). The "Browse files" control inside the
 * {@link import("@/components/upload/upload-widget").UploadWidget} is a
 * secondary, within-widget affordance (Button `sm` size), not the page's
 * primary action, so it is intentionally not the target of this gate.
 */

const HARNESS_PATH = "/visual-harness/upload";

/** Assert the Upload page rendered its real composition (drop zone + JD field +
 *  submit), not an error/loading state — the precondition for the gates. */
async function expectUploadRendered(page: Page): Promise<void> {
  await expect(
    page.getByRole("heading", { name: "Analyze your resume" }),
  ).toBeVisible();
  await expect(
    page.getByRole("group", { name: "Resume upload drop zone" }),
  ).toBeVisible();
  await expect(page.getByLabel("Job description")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Analyze Match" }),
  ).toBeVisible();
}

for (const theme of THEMES) {
  test(`upload layout + screenshot — ${theme}`, async ({ page }, testInfo) => {
    await gotoWithTheme(page, HARNESS_PATH, theme);
    await expectUploadRendered(page);

    // No horizontal scroll at the active project's viewport (Req 14.4 @1280/
    // 1440/1920; Req 18.1 @390). The full 320–1920 sweep runs in its own test.
    expect(await hasNoHorizontalScroll(page)).toBe(true);

    // Correct per-theme rendering: body paints the resolved --color-bg token
    // (Req 14.6, 19.1) — dark and light genuinely differ.
    await assertBodyBackgroundMatchesToken(page);

    // Primary action: the full-width "Analyze Match" submit button clears the
    // ≥44px touch-target floor (Req 18.2) — it is `h-11` (44px). It starts
    // disabled (no resume + empty JD), which does not change its box, so its
    // height is still measurable as the binding touch-target dimension.
    await assertTouchTargetAtLeast44(
      page.getByRole("button", { name: "Analyze Match" }),
    );

    // Committed baseline per (viewport × theme), excluding the assert-only 1920
    // project (design 9.2, 9.4).
    if (testInfo.project.name !== "desktop-1920") {
      await assertNoDevOverlay(page);
      await expect(page).toHaveScreenshot(`upload-${theme}.png`, {
        fullPage: true,
      });
    }
  });
}

// No-horizontal-scroll across the full responsive range (Req 18.1: 320–1920px).
// Runs once per project; resizing here is isolated from the screenshot tests
// above so it never disturbs their fixed-viewport captures.
test("upload — no horizontal scroll across 320–1920px", async ({ page }) => {
  await gotoWithTheme(page, HARNESS_PATH, "dark");
  await expectUploadRendered(page);
  await assertNoHorizontalScrollAcrossWidths(page);
});
