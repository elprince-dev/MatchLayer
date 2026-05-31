/**
 * Non-indexing controls for the authenticated/PII surface
 * (phase-1-matching §16.3; Requirements 15.2, 15.4, 15.5; `seo.md`; ADR 0006).
 *
 * MatchLayer renders Restricted PII — resume text, job descriptions, and match
 * results — on the authenticated `(app)` route group, and serves it as JSON
 * from `/api/v1/*`. None of that may ever be crawled or indexed. This is a
 * privacy control, not just an SEO one, so it is enforced as defense in depth:
 *
 *   - the `(app)` layout exports `robots: { index: false, follow: false }`
 *     so every nested authenticated route inherits `noindex, nofollow` (15.2);
 *   - `app/robots.ts` disallows `/api/` and the authenticated app paths (15.4);
 *   - any future `app/sitemap.ts` must exclude the `(app)`/PII routes (15.5).
 *
 * These are module-export / data assertions (no DOM render), so the default
 * Vitest `node` environment is sufficient — no jsdom pragma needed.
 *
 * The `(app)` layout also default-exports an async Server Component that pulls
 * in `next/headers`, `next/navigation`, `@/lib/auth`, and `./shell-client`. We
 * only need its named `metadata` export, so the request-scoped dependencies are
 * mocked to keep the module import side-effect-free in the test runner. The
 * mock factories reference only `vi`, so they are safe under Vitest hoisting.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  headers: vi.fn(),
  cookies: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  redirect: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  setAccessToken: vi.fn(),
  useAuth: vi.fn(),
}));

// The (app) layout now imports the server-only session helper from
// `@/lib/auth-server` (split out of the `"use client"` `@/lib/auth` so a
// Server Component can call it). Mock it so importing the layout to read its
// `metadata` export stays side-effect-free.
vi.mock("@/lib/auth-server", () => ({
  verifySessionFromRefreshCookie: vi.fn(),
}));

import { metadata } from "@/app/(app)/layout";
import robots from "@/app/robots";

const here = path.dirname(fileURLToPath(import.meta.url));

// The PII-bearing / authenticated paths that must never be crawlable. These
// mirror the `(app)` route group plus the `/api/` JSON surface.
const FORBIDDEN_PATHS = [
  "/api/",
  "/upload",
  "/matches",
  "/library",
  "/dashboard",
] as const;

describe("non-indexing controls — (app) layout robots metadata (Requirement 15.2)", () => {
  it("exports robots metadata that opts the authenticated surface out of indexing", () => {
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });

  it("adds no sitemap/canonical/Open Graph discoverability metadata to the (app) layout (15.1)", () => {
    // Requirement 15.1 / `seo.md`: authenticated routes carry no SEO chrome.
    expect(metadata.alternates?.canonical).toBeUndefined();
    expect(metadata.openGraph).toBeUndefined();
  });
});

describe("non-indexing controls — app/robots.ts disallow rules (Requirement 15.4)", () => {
  const result = robots();

  it("applies the disallow rules to every user agent", () => {
    const { rules } = result;
    // Our implementation returns a single rule object, not an array.
    if (Array.isArray(rules)) {
      throw new Error("expected robots() to return a single rules object");
    }
    expect(rules.userAgent).toBe("*");
  });

  it("disallows the /api/ surface and every authenticated app path", () => {
    const { rules } = result;
    if (Array.isArray(rules)) {
      throw new Error("expected robots() to return a single rules object");
    }
    const disallow = Array.isArray(rules.disallow)
      ? rules.disallow
      : [rules.disallow];
    expect(disallow).toEqual(expect.arrayContaining([...FORBIDDEN_PATHS]));
  });

  it("adds no sitemap entry that could reference a PII route", () => {
    // Per `seo.md` and the design, no `sitemap`/`host` entry is added here so
    // that no authenticated/PII route is ever surfaced via a sitemap link. The
    // public sitemap is owned by the `seo-foundation` spec.
    expect(result.sitemap).toBeUndefined();
    expect(result.host).toBeUndefined();
  });
});

describe("non-indexing controls — forward-looking sitemap guard (Requirement 15.5)", () => {
  // Resolve the sitemap path relative to this test file so the guard travels
  // with the repo regardless of the runner's CWD.
  const sitemapPath = path.resolve(here, "../src/app/sitemap.ts");

  it("never lists an (app)/PII route once app/sitemap.ts lands", async () => {
    if (!fs.existsSync(sitemapPath)) {
      // No sitemap exists yet — it is owned by the `seo-foundation` spec. The
      // guard is wired and will activate automatically the moment the file is
      // added: the assertions below run against its real entries. For now we
      // pin the absence so this stays a meaningful, intentional state.
      expect(fs.existsSync(sitemapPath)).toBe(false);
      return;
    }

    // The file exists: import its default export, generate the entries, and
    // assert none of them point at a PII-bearing authenticated route. The
    // dynamic, computed file-URL import keeps this branch inert (and free of a
    // static unresolved-module error) until the file actually exists.
    const mod: { default: () => unknown } = await import(
      pathToFileURL(sitemapPath).href
    );
    const entries = await mod.default();
    expect(Array.isArray(entries)).toBe(true);

    const urls = (entries as Array<{ url: string }>).map((entry) => entry.url);
    for (const url of urls) {
      for (const forbidden of FORBIDDEN_PATHS) {
        expect(url).not.toContain(forbidden);
      }
    }
  });
});
