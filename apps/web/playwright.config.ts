import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright is the designated E2E/visual harness for the frontend redesign
 * (design Section 9.1 "Step 0"; tech.md "Playwright for E2E from Phase 1").
 *
 * The per-screen visual/layout specs live alongside this config in
 * `tests/visual/` (the flagship ATS Results gates in `results.spec.ts`,
 * task 9.1; Upload/Auth/Landing in task 9.2).
 *
 * The four viewport projects mirror design Section 9.2:
 *
 *   | Project        | Size      | Purpose                                              |
 *   | -------------- | --------- | ---------------------------------------------------- |
 *   | desktop-1280   | 1280×720  | Above-the-fold + two-screen capture (Req 14.1, 14.5) |
 *   | desktop-1440   | 1440×900  | Single-viewport composition gate (Req 14.2)          |
 *   | mobile-390     | 390×844   | Mobile gauge/score sizing + no h-scroll (Req 18.1/5) |
 *   | desktop-1920   | 1920×1080 | Assert-only: no horizontal scrollbar (Req 14.4)      |
 *
 * The 1920×1080 project is "assert-only" (design 9.2): it exists to verify the
 * no-horizontal-scrollbar invariant and is intentionally excluded from the
 * committed screenshot baselines described in design 9.4.
 */

// Where a dev/preview server is exposed for the visual specs. Overridable so CI
// or a `next start` preview can point Playwright at the right origin.
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";

/**
 * The command the managed `webServer` runs to serve the app for the gates.
 *
 * The visual gates render the env-gated visual-harness routes
 * (`/visual-harness/*`), which only exist when `PLAYWRIGHT_VISUAL=1`. We build
 * once and serve the optimized production output so the gate measures the same
 * layout users get (and so the `force-dynamic` harness route reads the flag at
 * request time).
 *
 * The app is built with `output: "standalone"` (required by
 * `infra/docker/web.Dockerfile`), and **`next start` is incompatible with that
 * output** — it serves an app whose client chunks fail to load, hydrating into
 * the `__next_error__` boundary. So we serve via the generated standalone
 * `server.js` instead, after staging `.next/static` + `public` next to it (the
 * same layout the Docker image produces). `tests/visual/serve-standalone.mjs`
 * does that staging and exec; `PLAYWRIGHT_VISUAL` is exported into the server's
 * environment via the command prefix.
 *
 * Override with `PLAYWRIGHT_WEB_SERVER_COMMAND` (e.g. to point at an
 * already-running `next dev`), or set `PLAYWRIGHT_BASE_URL` +
 * `reuseExistingServer` to skip managing a server entirely.
 */
const webServerCommand =
  process.env.PLAYWRIGHT_WEB_SERVER_COMMAND ??
  "PLAYWRIGHT_VISUAL=1 node tests/visual/serve-standalone.mjs";

export default defineConfig({
  // Visual/layout acceptance gates live here (design Section 9.1).
  testDir: "./tests/visual",

  // Run specs within a file in order; parallelize across files.
  fullyParallel: true,
  // Fail the CI build if a `test.only` is committed by accident.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // The managed `webServer` is a SINGLE Next.js standalone server (one Node
  // process). Every worker drives full-page screenshots through it, so too many
  // concurrent workers saturate that one server and trip the per-test timeout
  // (observed once the suite grew past the flagship results gates to include
  // the Upload/Auth/Landing gates). CI already pins 1 worker for determinism;
  // locally we cap at 2 — enough to overlap I/O without overwhelming the single
  // server — instead of Playwright's CPU-count default.
  workers: process.env.CI ? 1 : 2,
  reporter: process.env.CI ? "github" : "list",

  // Commit baseline screenshots per (screen × viewport × theme) under
  // tests/visual/__screenshots__/ (design Section 9.4).
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFileName}/{arg}-{projectName}{ext}",

  expect: {
    toHaveScreenshot: {
      // Small ratio flags unintended visual drift without tripping on
      // sub-pixel/antialiasing noise (design Section 9.4).
      maxDiffPixelRatio: 0.01,
    },
  },

  use: {
    baseURL,
    trace: "on-first-retry",
  },

  /**
   * Managed app server for the gates (design Section 9.1). Playwright starts
   * the app with `PLAYWRIGHT_VISUAL=1` so the `/visual-harness/*` routes render
   * (they `notFound()` otherwise), waits for the origin to respond, and tears
   * it down when the run finishes. `reuseExistingServer` is on locally so a
   * developer can keep a server running between runs; CI always starts fresh.
   * The build must have been produced first (`pnpm --filter @matchlayer/web
   * build`); the `test:visual` script chains the build before invoking
   * Playwright. The timeout is generous to cover a cold `next start`.
   */
  webServer: {
    command: webServerCommand,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },

  projects: [
    {
      name: "desktop-1280",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 720 },
      },
    },
    {
      name: "desktop-1440",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "mobile-390",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      // Assert-only (design 9.2): no-horizontal-scrollbar check at 1920×1080.
      // Excluded from committed screenshot baselines.
      name: "desktop-1920",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1920, height: 1080 },
      },
    },
  ],
});
