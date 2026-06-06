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
 * Auth screens (`/login`, `/register`) — Playwright visual/layout acceptance
 * gates (task 9.2; design Section 8.3, 9.2, 9.4; Req 8.6, 14.4, 18.1, 18.2).
 *
 * The Auth_Pages are classified noindex but **publicly reachable without a
 * session** (Req 8 route classification), so these gates visit the real routes
 * directly — no visual-harness route is needed (unlike Upload, which is behind
 * the `(app)` auth shell). Neither page issues a network request until the form
 * is submitted, so the initial render the gate captures is deterministic.
 *
 * Per the task, this spec asserts the cross-screen gates for **both** auth
 * pages: no horizontal scrollbar across 320–1920px (and at 390px), correct dark
 * AND light rendering with a committed screenshot per (screen × viewport ×
 * theme), and a ≥44px touch target on the primary action (the full-width submit
 * button — "Sign in" on login, "Create account" on register, both `h-11`).
 *
 * The (auth) layout centers the `max-w-md` card on both axes from 320px to
 * ≥1920px (Req 8.6) over a subtle animated background that the config's
 * reduced-motion emulation freezes to a static texture (Req 8.5) — so both the
 * layout measurements and the screenshots are stable.
 */

interface AuthScreen {
  /** Screenshot/key name + the route to visit. */
  readonly name: "login" | "register";
  readonly path: string;
  /** The page `<h1>` text, asserted to confirm the real page rendered. */
  readonly heading: string;
  /** The primary submit button's accessible name. */
  readonly submitName: string;
}

const AUTH_SCREENS: readonly AuthScreen[] = [
  {
    name: "login",
    path: "/login",
    heading: "Sign in",
    submitName: "Sign in",
  },
  {
    name: "register",
    path: "/register",
    heading: "Create an account",
    submitName: "Create account",
  },
];

/** Assert the real auth page rendered (heading + submit), the precondition for
 *  the layout/screenshot gates. */
async function expectAuthRendered(
  page: Page,
  screen: AuthScreen,
): Promise<void> {
  await expect(
    page.getByRole("heading", { name: screen.heading }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: screen.submitName }),
  ).toBeVisible();
}

for (const screen of AUTH_SCREENS) {
  for (const theme of THEMES) {
    test(`${screen.name} layout + screenshot — ${theme}`, async ({
      page,
    }, testInfo) => {
      await gotoWithTheme(page, screen.path, theme);
      await expectAuthRendered(page, screen);

      // No horizontal scroll at the active project's viewport (Req 14.4, 18.1).
      // The full 320–1920 sweep runs in its own test below.
      expect(await hasNoHorizontalScroll(page)).toBe(true);

      // Correct per-theme rendering: body paints the resolved --color-bg token
      // (Req 14.6, 19.1) — dark and light genuinely differ.
      await assertBodyBackgroundMatchesToken(page);

      // Primary action: the full-width submit button clears the ≥44px touch
      // target floor (Req 18.2) — it is `h-11` (44px).
      await assertTouchTargetAtLeast44(
        page.getByRole("button", { name: screen.submitName }),
      );

      // Committed baseline per (screen × viewport × theme), excluding the
      // assert-only 1920 project (design 9.2, 9.4).
      if (testInfo.project.name !== "desktop-1920") {
        await assertNoDevOverlay(page);
        await expect(page).toHaveScreenshot(`${screen.name}-${theme}.png`, {
          fullPage: true,
        });
      }
    });
  }

  // No-horizontal-scroll across the full responsive range (Req 18.1: 320–1920px,
  // Req 8.6 card centered across the same range). Resizing is isolated from the
  // screenshot tests so it never disturbs their fixed-viewport captures.
  test(`${screen.name} — no horizontal scroll across 320–1920px`, async ({
    page,
  }) => {
    await gotoWithTheme(page, screen.path, "dark");
    await expectAuthRendered(page, screen);
    await assertNoHorizontalScrollAcrossWidths(page);
  });
}
