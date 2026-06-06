/**
 * Sitemap contract test — foundation wiring (Task 1.7; Req 7.5, 8.9; seo.md;
 * ADR 0006).
 *
 * `app/sitemap.ts` is the public, indexable surface allowlist. Two acceptance
 * criteria converge on it:
 *
 *   - Req 7.5 — the landing page (`/`) IS included and indexable.
 *   - Req 8.9 — `/login` and `/register` are EXCLUDED from the sitemap.
 *
 * Layered with `seo.md`'s default-deny rule, the sitemap must never list any
 * authenticated/PII route (`/upload`, `/matches`, …) or the `/api/` JSON
 * surface. This test asserts the positive (`/` present) AND the full negative
 * set the task enumerates: none of `/upload`, `/matches`, `/login`,
 * `/register`, `/api/`.
 *
 * This complements — and does not replace — the forward-looking guard in
 * `non-indexing.test.ts`, which asserts the robots-disallow set
 * (`/api/`, `/upload`, `/matches`, `/library`, `/dashboard`) never appears.
 * The two exclusion sets differ deliberately: `/login` and `/register` are
 * publicly reachable (so they are NOT robots-disallowed) yet are kept out of
 * the sitemap (Req 8.9–8.10). Both guards must stay green.
 *
 * The sitemap is a pure module export (no DOM), so the default Vitest `node`
 * environment is sufficient.
 */

import { describe, expect, it } from "vitest";

import sitemap from "@/app/sitemap";

/**
 * Paths the sitemap must NEVER emit (Req 8.9 + seo.md default-deny). These are
 * matched as substrings of each emitted absolute URL so e.g. `/matches` also
 * catches `/matches/[id]`, and `/api/` catches the whole JSON surface.
 */
const FORBIDDEN_PATHS = [
  "/upload",
  "/matches",
  "/login",
  "/register",
  "/api/",
] as const;

describe("app/sitemap.ts — public allowlist (Req 7.5, 8.9; seo.md)", () => {
  const entries = sitemap();
  const urls = entries.map((entry) => entry.url);

  it("returns a non-empty array of sitemap entries", () => {
    expect(Array.isArray(entries)).toBe(true);
    expect(entries.length).toBeGreaterThan(0);
  });

  it("includes the landing page `/` as an indexable entry (Req 7.5)", () => {
    // The landing page is the only indexable MVP surface. Assert an entry whose
    // URL path resolves to the site root (pathname exactly "/"), independent of
    // the configured origin.
    const hasRoot = urls.some((url) => new URL(url).pathname === "/");
    expect(hasRoot).toBe(true);
  });

  it("excludes every authenticated/PII and /api/ path (Req 8.9; seo.md)", () => {
    for (const url of urls) {
      for (const forbidden of FORBIDDEN_PATHS) {
        expect(url).not.toContain(forbidden);
      }
    }
  });

  it("emits only the landing page today (default-deny allowlist)", () => {
    // Pins the current public surface to exactly `/`. When the seo-foundation
    // spec adds `/pricing`, `/about`, etc., this expectation is updated
    // deliberately alongside that change — keeping the allowlist an explicit,
    // reviewed act rather than something that grows silently.
    const pathnames = urls.map((url) => new URL(url).pathname).sort();
    expect(pathnames).toEqual(["/"]);
  });
});
