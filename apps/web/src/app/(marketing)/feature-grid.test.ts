/**
 * Feature-grid column-count guard for the Landing_Page (Task 8.8; Req 4.1).
 *
 * Req 4.1 requires the features grid to display **1 column below 640px, 2
 * columns between 640px and 1024px, and 4 columns above 1024px**. That
 * responsive grid is owned by the marketing page (`(marketing)/page.tsx`), not
 * by `FeatureCard` (the card only fills whatever cell the parent sizes), so the
 * column contract lives in the page module's grid wrapper class string:
 * `grid-cols-1 sm:grid-cols-2 lg:grid-cols-4` (Tailwind's `sm` = 640px, `lg` =
 * 1024px breakpoints).
 *
 * ## Why a source-read assertion (the robust approach)
 *
 * Rendering the full marketing page under jsdom is heavy and fragile: it is a
 * Server Component that exports `metadata` and composes several `'use client'`
 * islands (GlassNav's `useSyncExternalStore`, the Hero/HowItWorks/FinalCTA
 * framer-motion reveals, the nested next-themes `ThemeToggle`). The responsive
 * column counts are also **not observable in jsdom regardless** — Tailwind's
 * stylesheet is never loaded into the test DOM and media queries do not resolve
 * there (the same reason `globals-tokens.test.ts` and `auth-card.test.tsx`
 * assert on declarations rather than computed style). So the column contract is
 * the class string in the source, and this test reads the real page module and
 * asserts those breakpoint-prefixed utilities are present — the same
 * read-the-file-and-assert pattern `globals-tokens.test.ts` uses for the CSS
 * tokens. It is a pure module-level file read, so the default Vitest `node`
 * environment suffices (no jsdom pragma).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const pagePath = path.resolve(here, "./page.tsx");

let source = "";

beforeAll(() => {
  source = fs.readFileSync(pagePath, "utf8");
});

describe("Landing features grid — responsive column counts (Req 4.1)", () => {
  it("declares 1 column on the smallest viewport (grid-cols-1)", () => {
    expect(source).toContain("grid-cols-1");
  });

  it("declares 2 columns from the 640px (sm) breakpoint (sm:grid-cols-2)", () => {
    expect(source).toContain("sm:grid-cols-2");
  });

  it("declares 4 columns from the 1024px (lg) breakpoint (lg:grid-cols-4)", () => {
    expect(source).toContain("lg:grid-cols-4");
  });

  it("applies all three column counts on a single grid wrapper", () => {
    // The three responsive utilities must co-occur in one className so the grid
    // steps 1 → 2 → 4 across the breakpoints (rather than being spread across
    // unrelated elements).
    const gridClass =
      /grid-cols-1[^"'`]*\bsm:grid-cols-2\b[^"'`]*\blg:grid-cols-4\b/;
    expect(gridClass.test(source)).toBe(true);
  });
});
