import { test, expect, type Page } from "@playwright/test";

/**
 * ATS Results flagship — Playwright visual/layout acceptance gates (task 9.1;
 * design Section 9.1–9.4; Req 11.8, 12.5, 12.7, 14.1–14.7, 18.1, 18.5, 20.5).
 *
 * The flagship ATS Results page is the single highest visual-design priority
 * (Req 14.8), so these are the first and highest-priority checks in the
 * consolidated validation phase. They render the **real** Results composition
 * — `ResultsContent` / `EmptyResultState`, imported by the env-gated
 * visual-harness route `app/visual-harness/results/[fixture]` — from the
 * canonical Section 5 fixtures, with **no network fetch and no auth**, so the
 * gate measures production layout deterministically and can never drift from
 * it (see the harness route docstring for the rationale).
 *
 * Fixtures (design Section 5): `a` → strong match (score 85, the flagship
 * success state); `c` → degenerate 0/0 (the Empty_Result_State). Each is
 * rendered in **both** themes.
 *
 * Viewport projects (design Section 9.2, configured in `playwright.config.ts`):
 * `desktop-1280` (1280×720), `desktop-1440` (1440×900), `mobile-390` (390×844),
 * and the assert-only `desktop-1920` (1920×1080). This single spec runs under
 * every project; viewport-specific gates are guarded on the active viewport, so
 * e.g. the above-the-fold gate fires only at 1280×720.
 *
 * Determinism: the config emulates `prefers-reduced-motion: reduce`, so the
 * score reveal and staggered entrances render in their final state instantly
 * (design Section 10.5) — the representative frame for a stable baseline — and
 * the layout geometry the gates measure is identical to the post-animation
 * state.
 */

const THEMES = ["dark", "light"] as const;
type Theme = (typeof THEMES)[number];

/** Field names that must never appear as raw text/markup on a rendered result
 *  (design 9.3 #7, #8). `scorer_version` is checked separately — its VALUE is
 *  allowed, but only inside the styled footnote. */
const FORBIDDEN_FIELD_NAMES = [
  "similarity_component",
  "keyword_coverage_component",
  "job_description_text",
] as const;

/** Placeholder/debug signatures that must never appear in a rendered result
 *  (design 9.3 #7). `lorem`/`ipsum` are matched case-insensitively; the rest
 *  are matched verbatim. */
const FORBIDDEN_PLACEHOLDERS = ["undefined", "NaN", "[object Object]"] as const;

/** The Restricted-PII field the contract never returns and the UI must never
 *  surface — neither in the DOM nor in any network payload (Req 11.8, 20.5). */
const JD_TEXT_FIELD = "job_description_text";

/**
 * Navigate to a visual-harness Results fixture with a deterministic theme.
 *
 * `next-themes` resolves the active theme from `localStorage["theme"]` in its
 * pre-paint script (then toggles the `.dark` class on `<html>`), so seeding the
 * key via `addInitScript` BEFORE navigation pins the rendered theme with no
 * wrong-theme frame. Also installs a network sniffer that records any
 * document/RSC/JSON/JS payload containing `job_description_text`, so the
 * "never on the wire" assertion (Req 11.8, 20.5) can run after load.
 */
async function gotoHarness(
  page: Page,
  fixture: "a" | "c",
  theme: Theme,
): Promise<{ jdNetworkHits: string[] }> {
  await page.addInitScript((t) => {
    try {
      window.localStorage.setItem("theme", t);
    } catch {
      /* localStorage unavailable — the default (dark) still renders. */
    }
  }, theme);

  // Emulate reduced motion so the score reveal and staggered entrances render
  // in their final state instantly (design Section 10.5): the captured frame is
  // the stable post-animation representative, and the measured layout geometry
  // is identical to the resolved state.
  await page.emulateMedia({ reducedMotion: "reduce" });

  const jdNetworkHits: string[] = [];
  page.on("response", async (response) => {
    try {
      // Immutable build assets under `/_next/static/` are compiled application
      // code, produced at build time before any user data exists — they can
      // never carry a user's PII. They DO legitimately contain the *field name*
      // token `job_description_text` as prose inside the OpenAPI-generated Zod
      // schema endpoint descriptions (`@matchlayer/shared-types` →
      // `api-schemas.ts`, pulled in transitively by `ResultsContent`'s import
      // of `MatchResponseSchema`). That is documentation, not the Restricted
      // PII *value* (the job description text). Scanning these chunks for the
      // field name is therefore a false positive, so skip them — the real
      // leak surfaces (the match API JSON, the document HTML, and the RSC
      // flight payload) are still scanned below (Req 11.8, 20.5).
      if (response.url().includes("/_next/static/")) {
        return;
      }
      const contentType = response.headers()["content-type"] ?? "";
      // The match API response is where a PII field would realistically leak;
      // the document + RSC payload is where SSR'd props would. Scan those text
      // surfaces and ignore binary/opaque ones.
      if (!/json|text|x-component/.test(contentType)) {
        return;
      }
      const body = await response.text();
      if (body.includes(JD_TEXT_FIELD)) {
        jdNetworkHits.push(response.url());
      }
    } catch {
      /* Body not readable (redirect/opaque) — nothing to scan. */
    }
  });

  await page.goto(`/visual-harness/results/${fixture}`);
  await page.waitForLoadState("networkidle");

  // Pin the resolved theme: `.dark` present for dark, absent for light. This is
  // also the precondition for the per-theme body-background assertion below.
  if (theme === "dark") {
    await expect(page.locator("html")).toHaveClass(/(^|\s)dark(\s|$)/);
  } else {
    await expect(page.locator("html")).not.toHaveClass(/(^|\s)dark(\s|$)/);
  }

  return { jdNetworkHits };
}

/** `true` when no element overflows the viewport horizontally (Req 14.4, 18.1). */
async function hasNoHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth <= doc.clientWidth;
  });
}

/**
 * Assert the rendered body's resolved background equals the active theme's
 * `--color-bg` token (design 9.3 #6). The `<body>` uses the `bg-bg` utility,
 * which resolves to `rgb(var(--color-bg))`; this pins that the theme's surface
 * token is actually painted (dark `rgb(10, 10, 11)` / light `rgb(255,255,255)`).
 *
 * Wrapped in `expect(...).toPass()` so the comparison is retried until the
 * stylesheet has actually been applied. Reading `getComputedStyle` once can
 * race the standalone server's first paint — observed transiently on a cold
 * worker as `--color-bg` resolving empty and `<body>` still painting the
 * UA-default transparent `rgba(0, 0, 0, 0)`. Polling waits for the token to
 * resolve to a full RGB triplet AND the body to match it, so the assertion
 * stays strict (exact per-theme token match) without flaking on paint timing.
 */
async function assertBodyBackgroundMatchesToken(page: Page): Promise<void> {
  await expect(async () => {
    const { bodyBg, expected, tokenResolved } = await page.evaluate(() => {
      const triplet = getComputedStyle(document.documentElement)
        .getPropertyValue("--color-bg")
        .trim();
      const parts = triplet.split(/\s+/).map(Number);
      const tokenResolved =
        parts.length === 3 && parts.every((n) => Number.isFinite(n));
      const [r, g, b] = parts;
      return {
        bodyBg: getComputedStyle(document.body).backgroundColor,
        expected: `rgb(${r}, ${g}, ${b})`,
        tokenResolved,
      };
    });
    // Guard against comparing a half-applied stylesheet: require the token to
    // have resolved to three finite channels before trusting `expected`.
    expect(tokenResolved, "--color-bg must resolve to an RGB triplet").toBe(
      true,
    );
    expect(bodyBg).toBe(expected);
  }).toPass({ timeout: 5_000 });
}

/** Scan the rendered text + markup for placeholder/debug/raw-field-name leaks
 *  (design 9.3 #7). */
async function assertNoPlaceholderOrRawFields(page: Page): Promise<void> {
  const visibleText = (await page.locator("body").innerText()).toLowerCase();
  const markup = await page.content();

  for (const placeholder of ["lorem", "ipsum"]) {
    expect(
      visibleText,
      `placeholder "${placeholder}" must not render`,
    ).not.toContain(placeholder);
  }
  for (const placeholder of FORBIDDEN_PLACEHOLDERS) {
    expect(
      await page.locator("body").innerText(),
      `debug signature "${placeholder}" must not render`,
    ).not.toContain(placeholder);
  }
  for (const field of FORBIDDEN_FIELD_NAMES) {
    expect(markup, `raw field name "${field}" must not appear`).not.toContain(
      field,
    );
  }
}

/** Assert `job_description_text` is absent from BOTH the DOM and every observed
 *  network payload (Req 11.8, 20.5; design 9.3 #8). */
async function assertJobDescriptionTextAbsent(
  page: Page,
  jdNetworkHits: string[],
): Promise<void> {
  const markup = await page.content();
  expect(
    markup,
    "job_description_text must be absent from the DOM",
  ).not.toContain(JD_TEXT_FIELD);
  expect(
    jdNetworkHits,
    `job_description_text must be absent from network payloads (seen in: ${jdNetworkHits.join(
      ", ",
    )})`,
  ).toEqual([]);
}

// ---------------------------------------------------------------------------
// Fixture A — strong match (success composition)
// ---------------------------------------------------------------------------

for (const theme of THEMES) {
  test(`results fixture A (strong) — ${theme}`, async ({ page }, testInfo) => {
    const { jdNetworkHits } = await gotoHarness(page, "a", theme);

    const viewport = page.viewportSize();
    expect(viewport).not.toBeNull();
    const { width, height } = viewport!;

    const gaugeSvg = page.locator('[data-testid="score-gauge"] svg');
    const scoreLabel = page.getByText("Excellent", { exact: true });
    await expect(gaugeSvg).toBeVisible();
    await expect(scoreLabel).toBeVisible();

    // (1) No horizontal scroll at every width (Req 14.4 @1280/1440/1920; 18.1 @390).
    expect(await hasNoHorizontalScroll(page)).toBe(true);

    // (6) Body background matches the resolved --color-bg per theme (Req 14.6, 19.1).
    await assertBodyBackgroundMatchesToken(page);

    // (7) No placeholder/debug/raw-field content for a successful result (Req 14.3).
    await assertNoPlaceholderOrRawFields(page);

    // (8) job_description_text never present in DOM or on the wire (Req 11.8, 20.5).
    await assertJobDescriptionTextAbsent(page, jdNetworkHits);

    // scorer_version VALUE is shown, but only inside the styled footnote
    // (Req 11.8; design 9.3 #7). The footnote is the `font-mono text-text-subtle`
    // line; assert its styling and that the value lives nowhere else.
    const footnote = page.getByText(/^Scored with /);
    await expect(footnote).toBeVisible();
    const footnoteClass = (await footnote.getAttribute("class")) ?? "";
    expect(footnoteClass).toContain("font-mono");
    expect(footnoteClass).toContain("text-text-subtle");
    const scorerVersion = "tfidf-keyword@1.3.0+lexicon.2025-02-01";
    const bodyText = await page.locator("body").innerText();
    const footnoteText = await footnote.innerText();
    expect(bodyText.split(scorerVersion).length - 1).toBe(1);
    expect(footnoteText).toContain(scorerVersion);

    // Token classes are applied on key nodes (not UA-default unstyled) — design 9.3 #7.
    const breakdown = page.locator('[aria-label="Score breakdown"]');
    await expect(breakdown).toBeVisible();
    const breakdownClass = (await breakdown.getAttribute("class")) ?? "";
    expect(breakdownClass).toContain("rounded-card");
    expect(breakdownClass).toContain("bg-bg-elevated");

    // (2) @1280×720: gauge + qualitative label both above the fold (Req 14.1).
    if (width === 1280 && height === 720) {
      const gaugeBox = await gaugeSvg.boundingBox();
      const labelBox = await scoreLabel.boundingBox();
      expect(gaugeBox).not.toBeNull();
      expect(labelBox).not.toBeNull();
      expect(gaugeBox!.y + gaugeBox!.height).toBeLessThanOrEqual(720);
      expect(labelBox!.y + labelBox!.height).toBeLessThanOrEqual(720);

      // (4) Full content within two viewport heights @1280×720 (Req 14.5).
      const scrollHeight = await page.evaluate(
        () => document.body.scrollHeight,
      );
      expect(scrollHeight).toBeLessThanOrEqual(2 * 720);
    }

    // (3) @1440×900: gauge + both breakdown bars + "Matched keywords" heading
    //     all within one viewport (Req 14.2).
    if (width === 1440 && height === 900) {
      const gaugeBox = await gaugeSvg.boundingBox();
      expect(gaugeBox).not.toBeNull();
      expect(gaugeBox!.y + gaugeBox!.height).toBeLessThanOrEqual(900);

      const bars = page.getByRole("progressbar");
      await expect(bars).toHaveCount(2);
      for (let i = 0; i < 2; i++) {
        const barBox = await bars.nth(i).boundingBox();
        expect(barBox).not.toBeNull();
        expect(barBox!.y + barBox!.height).toBeLessThanOrEqual(900);
      }

      const matchedHeading = page.getByRole("heading", {
        name: "Matched keywords",
      });
      const headingBox = await matchedHeading.boundingBox();
      expect(headingBox).not.toBeNull();
      expect(headingBox!.y + headingBox!.height).toBeLessThanOrEqual(900);
    }

    // (5) @390×844: mobile gauge ≥120px diameter and score ≥24px (Req 18.5).
    if (width === 390) {
      const gaugeBox = await gaugeSvg.boundingBox();
      expect(gaugeBox).not.toBeNull();
      expect(Math.round(gaugeBox!.width)).toBeGreaterThanOrEqual(120);

      const scoreFontSize = await page
        .locator('[data-testid="score-gauge"]')
        .evaluate((el) => {
          const span = Array.from(el.querySelectorAll("span")).find((s) =>
            /^\d+$/.test((s.textContent ?? "").trim()),
          );
          return span ? parseFloat(getComputedStyle(span).fontSize) : 0;
        });
      expect(scoreFontSize).toBeGreaterThanOrEqual(24);
    }

    // Visual-regression baseline per (viewport × theme), excluding the
    // assert-only 1920 project (design 9.2, 9.4).
    if (testInfo.project.name !== "desktop-1920") {
      await expect(page).toHaveScreenshot(`results-a-${theme}.png`, {
        fullPage: true,
      });
    }
  });
}

// ---------------------------------------------------------------------------
// Fixture C — degenerate 0/0 (Empty_Result_State)
// ---------------------------------------------------------------------------

for (const theme of THEMES) {
  test(`results fixture C (degenerate) — ${theme}`, async ({
    page,
  }, testInfo) => {
    const { jdNetworkHits } = await gotoHarness(page, "c", theme);

    // The degenerate-but-valid surface renders the Empty_Result_State (the same
    // branch results-view takes — Req 12.5, 12.6), never the danger ErrorState.
    const emptyState = page.getByRole("status");
    await expect(emptyState).toBeVisible();
    await expect(page.getByText("Not enough to analyze yet")).toBeVisible();

    // No horizontal scroll at every width (Req 14.4, 18.1).
    expect(await hasNoHorizontalScroll(page)).toBe(true);

    // Body background matches the resolved --color-bg per theme (Req 14.6).
    await assertBodyBackgroundMatchesToken(page);

    // No placeholder/debug/raw-field content (Req 14.3).
    await assertNoPlaceholderOrRawFields(page);

    // job_description_text never present (Req 11.8, 20.5).
    await assertJobDescriptionTextAbsent(page, jdNetworkHits);

    // (9) Degenerate state is NOT styled with `danger` (Req 12.5–12.7;
    //     design 9.3 #9): no element in the empty-state subtree paints any
    //     property with the resolved `--color-danger` token.
    const dangerUsed = await emptyState.evaluate((root) => {
      const dangerTriplet = getComputedStyle(document.documentElement)
        .getPropertyValue("--color-danger")
        .trim();
      const [r, g, b] = dangerTriplet.split(/\s+/).map(Number);
      const needle = `${r}, ${g}, ${b}`;
      const nodes = [root, ...Array.from(root.querySelectorAll("*"))];
      const props = [
        "color",
        "backgroundColor",
        "borderTopColor",
        "borderRightColor",
        "borderBottomColor",
        "borderLeftColor",
      ] as const;
      return nodes.some((node) => {
        const cs = getComputedStyle(node as Element);
        return props.some((p) => (cs[p] ?? "").includes(needle));
      });
    });
    expect(dangerUsed, "EmptyResultState must not use the danger token").toBe(
      false,
    );

    // Visual-regression baseline per (viewport × theme), excluding 1920.
    if (testInfo.project.name !== "desktop-1920") {
      await expect(page).toHaveScreenshot(`results-c-${theme}.png`, {
        fullPage: true,
      });
    }
  });
}
