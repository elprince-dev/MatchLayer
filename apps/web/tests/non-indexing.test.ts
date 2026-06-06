/**
 * Non-indexing, sitemap, and SEO assertions across the four MVP routes
 * (Task 9.4; Requirements 7.5, 8.7, 8.8, 8.9, 8.10, 13.3, 21.10; `seo.md`,
 * `security.md`; ADR 0006).
 *
 * MatchLayer renders Restricted PII — resume text, job descriptions, and match
 * results — on the authenticated `(app)` route group, and serves it as JSON
 * from `/api/v1/*`. None of that may ever be crawled or indexed. The `(auth)`
 * pages (`/login`, `/register`) are publicly reachable but, per the `seo.md`
 * route classification, must also never be indexed. The landing page (`/`) is
 * the **one** indexable surface among the MVP screens (Req 7.5, 8.10). This is
 * a privacy control, not just an SEO one, so it is enforced as defense in
 * depth across several independent layers:
 *
 *   - the `(app)` layout exports `robots: { index: false, follow: false }` so
 *     every nested authenticated route — `/upload`, `/matches/[id]` — inherits
 *     `noindex, nofollow` (Req 8.x via the route classification; Req 13.3);
 *   - the `(auth)` layout exports the same directive so `/login` and
 *     `/register` inherit `noindex, nofollow` (Req 8.7, 8.8);
 *   - the `(marketing)` layout/page export full SEO metadata with **no**
 *     `noindex` directive, keeping `/` indexable (Req 7.5, 8.10);
 *   - `app/robots.ts` disallows `/api/` and the authenticated app paths;
 *   - `app/sitemap.ts` lists `/` and **excludes** every authenticated/PII and
 *     `/api/` path (Req 7.5, 8.9, 8.10);
 *   - `src/proxy.ts` stamps `X-Robots-Tag: noindex, nofollow` on the
 *     non-indexable route classes while leaving `/` indexable. The exhaustive
 *     path matrix, the segment-aware prefix edge cases, and the
 *     CSP-by-`NODE_ENV` assertions remain the source of truth in
 *     `proxy.test.ts` (preserved, not weakened, per Req 21.10). For Task 9.4
 *     traceability, the final `describe` below ALSO consolidates the specific
 *     four-MVP-route view — pinning, per route, that the authenticated/auth
 *     routes are noindex at BOTH the Metadata-API layer and the response-header
 *     layer while `/` stays indexable — so the cross-route guarantee reads as
 *     one matrix rather than being split across two files.
 *
 * These are module-export / data / source assertions (no DOM render), so the
 * default Vitest `node` environment is sufficient — no jsdom pragma needed.
 *
 * The `(app)` layout also default-exports an async Server Component that pulls
 * in `next/headers`, `next/navigation`, `@/lib/auth`, and `@/lib/auth-server`.
 * We only need its named `metadata` export, so the request-scoped dependencies
 * are mocked to keep the module import side-effect-free in the test runner. The
 * mock factories reference only `vi`, so they are safe under Vitest hoisting.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";
import { type NextRequest } from "next/server";

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
import { metadata as authMetadata } from "@/app/(auth)/layout";
import { metadata as marketingMetadata } from "@/app/(marketing)/layout";
import robots from "@/app/robots";
import sitemap from "@/app/sitemap";
import { proxy } from "@/proxy";

const here = path.dirname(fileURLToPath(import.meta.url));

// The PII-bearing / authenticated paths that must never be crawlable. These
// mirror the `(app)` route group plus the `/api/` JSON surface — i.e. the
// `app/robots.ts` *disallow* set (`/login`, `/register` are publicly reachable
// so they are deliberately NOT robots-disallowed; they are kept out of the
// sitemap instead — see `SITEMAP_FORBIDDEN_PATHS`).
const FORBIDDEN_PATHS = [
  "/api/",
  "/upload",
  "/matches",
  "/library",
  "/dashboard",
] as const;

// The paths the **sitemap** must never emit (Req 8.9 + `seo.md` default-deny).
// This set differs from the robots-disallow set above on purpose: `/login` and
// `/register` ARE excluded here (Req 8.9) even though they are not
// robots-disallowed, while `/library`/`/dashboard` are out of scope for the
// redesign's public surface. Matched as URL substrings so `/matches` also
// catches `/matches/[id]` and `/api/` catches the whole JSON surface.
const SITEMAP_FORBIDDEN_PATHS = [
  "/upload",
  "/matches",
  "/login",
  "/register",
  "/api/",
] as const;

describe("non-indexing controls — (app) layout robots metadata (Requirement 13.3)", () => {
  it("exports robots metadata that opts the authenticated surface out of indexing", () => {
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });

  it("adds no sitemap/canonical/Open Graph discoverability metadata to the (app) layout", () => {
    // `seo.md`: authenticated routes carry no SEO chrome.
    expect(metadata.alternates?.canonical).toBeUndefined();
    expect(metadata.openGraph).toBeUndefined();
  });
});

describe("non-indexing controls — (auth) layout robots metadata (Requirement 8.7, 8.8)", () => {
  it("exports robots metadata that opts /login and /register out of indexing", () => {
    // The `(auth)` route-group layout is a Server Component so it can export
    // `metadata`; `/login` and `/register` inherit `noindex, nofollow` from it
    // (seo.md route classification; Req 8.7, 8.8).
    expect(authMetadata.robots).toEqual({ index: false, follow: false });
  });

  it("adds no sitemap/canonical/Open Graph discoverability metadata to the (auth) layout (8.9, 8.10)", () => {
    // The auth pages are excluded from the sitemap and carry no SEO chrome;
    // the landing page (`/`) stays the only indexable surface (Req 8.10).
    expect(authMetadata.alternates?.canonical).toBeUndefined();
    expect(authMetadata.openGraph).toBeUndefined();
  });
});

describe("indexing — (marketing) layout keeps `/` indexable (Requirement 7.5, 8.10)", () => {
  // The inverse of the `(app)`/`(auth)` assertions: the one Public route group
  // carries full SEO chrome and, crucially, sets NO `noindex` directive. The
  // marketing layout/page build their metadata via `buildMarketingMetadata`,
  // which deliberately omits `robots` — the absence of a `noindex` is what
  // keeps `/` crawlable (Req 7.5). Making `/` noindex would break the only
  // indexable MVP surface, so we pin it explicitly here.
  it("sets no `robots` (noindex) directive on the (marketing) layout, so `/` stays indexable", () => {
    expect(marketingMetadata.robots).toBeUndefined();
  });

  it("carries a self-referential canonical for `/` (the indexable surface)", () => {
    // Positive SEO counterpart to the `(app)`/`(auth)` "no canonical" checks:
    // the public landing surface DOES advertise a canonical, resolving to `/`.
    expect(marketingMetadata.alternates?.canonical).toBe("/");
    expect(marketingMetadata.openGraph).toBeDefined();
  });
});

describe("non-indexing controls — app/robots.ts disallow rules (Requirement 21.10)", () => {
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
    // that no authenticated/PII route is ever surfaced via a robots-file link.
    expect(result.sitemap).toBeUndefined();
    expect(result.host).toBeUndefined();
  });
});

describe("non-indexing controls — app/sitemap.ts public allowlist (Requirement 7.5, 8.9, 8.10)", () => {
  // `app/sitemap.ts` now exists (added in task 1.6 / 8.7), so this guard is no
  // longer in "file may not exist" mode: it actively asserts the real entries.
  // It complements `sitemap.test.ts` by tying the sitemap into the consolidated
  // non-indexing matrix — `/` present (indexable), every PII/`/api/` path
  // absent. The exclusion set here (`SITEMAP_FORBIDDEN_PATHS`) intentionally
  // differs from the robots-disallow set above: `/login`/`/register` are
  // excluded from the sitemap (Req 8.9) though not robots-disallowed.
  const entries = sitemap();
  const urls = entries.map((entry) => entry.url);

  it("emits a non-empty sitemap", () => {
    expect(Array.isArray(entries)).toBe(true);
    expect(entries.length).toBeGreaterThan(0);
  });

  it("includes the landing page `/` as an indexable entry (Req 7.5)", () => {
    const hasRoot = urls.some((url) => new URL(url).pathname === "/");
    expect(hasRoot).toBe(true);
  });

  it("never lists an (app)/PII, (auth), or /api/ route (Req 8.9, 8.10)", () => {
    for (const url of urls) {
      for (const forbidden of SITEMAP_FORBIDDEN_PATHS) {
        expect(url).not.toContain(forbidden);
      }
    }
  });
});

describe("non-indexing controls — nested routes inherit the layout directive (Req 8.7, 8.8, 9.13, 13.3)", () => {
  // The Metadata API resolves a route's `robots` by inheritance: a nested page
  // gets its route-group layout's directive UNLESS it exports its own
  // `metadata`/`generateMetadata` that overrides `robots`. The layout-level
  // `noindex` is asserted above (via the imported `metadata`/`authMetadata`);
  // here we pin that the PII/auth *pages* add no own metadata export, so the
  // inherited `noindex, nofollow` is authoritative for `/upload`,
  // `/matches/[id]`, `/login`, and `/register`. A future edit that introduced
  // a page-level `metadata` (even well-intentioned SEO chrome) would trip this
  // and force a review against `seo.md` / ADR 0006.
  //
  // This is a static source assertion (no module import) so it stays
  // side-effect-free and does not require mocking the `'use client'` pages or
  // their data/router dependencies.
  const appRoot = path.resolve(here, "../src/app");

  // Detects an OWN page-level metadata source-of-truth export that could
  // override the inherited robots directive.
  const OWN_METADATA_EXPORT =
    /export\s+(?:const\s+metadata\b|(?:async\s+)?function\s+generateMetadata\b)/;

  const inheritingPages = [
    {
      label: "(app) /upload page inherits noindex (Req 9.13)",
      file: "(app)/upload/page.tsx",
    },
    {
      label: "(app) /matches/[id] page inherits noindex (Req 13.3)",
      file: "(app)/matches/[id]/page.tsx",
    },
    {
      label: "(auth) /login page inherits noindex (Req 8.7)",
      file: "(auth)/login/page.tsx",
    },
    {
      label: "(auth) /register page inherits noindex (Req 8.8)",
      file: "(auth)/register/page.tsx",
    },
  ] as const;

  it.each(inheritingPages)(
    "$label: page adds no own metadata/robots export, so the layout directive holds",
    ({ file }) => {
      const source = fs.readFileSync(path.join(appRoot, file), "utf8");
      expect(source).not.toMatch(OWN_METADATA_EXPORT);
    },
  );

  it("the (app) and (auth) layouts DO export the noindex metadata the pages inherit", () => {
    // Anchor the inheritance chain from the source side too: the layouts that
    // the pages above inherit from must themselves declare the metadata export
    // (the resolved directive is already asserted via the imported objects).
    const appLayout = fs.readFileSync(
      path.join(appRoot, "(app)/layout.tsx"),
      "utf8",
    );
    const authLayout = fs.readFileSync(
      path.join(appRoot, "(auth)/layout.tsx"),
      "utf8",
    );
    expect(appLayout).toMatch(OWN_METADATA_EXPORT);
    expect(authLayout).toMatch(OWN_METADATA_EXPORT);
  });
});

describe("non-indexing — consolidated cross-route SEO matrix (Task 9.4; Req 7.5, 8.7, 8.8, 8.9, 8.10, 13.3, 21.10)", () => {
  // One traceable place that pins the FULL cross-route guarantee Task 9.4
  // enumerates, viewed per MVP route rather than per control. For each route we
  // assert the two independent defense-in-depth layers agree:
  //
  //   1. Metadata API layer — the route's resolved `robots` directive. The
  //      `(app)` and `(auth)` pages have no own `metadata` export (asserted
  //      above), so each inherits its route-group layout's
  //      `robots: { index:false, follow:false }`; `/` inherits the
  //      `(marketing)` layout, which sets NO `robots` (stays indexable).
  //   2. Response-header layer — what `src/proxy.ts` stamps for the route's
  //      `X-Robots-Tag`. The exhaustive path/prefix matrix lives in
  //      `proxy.test.ts` (preserved); here we re-assert only the four MVP
  //      routes (+ a representative `/matches/[id]` instance) so the
  //      cross-route view is self-contained and traceable to Task 9.4.
  //
  // `proxy()` reads only `request.nextUrl.pathname` (for this branch) and
  // `process.env.NODE_ENV`, so a minimal `NextRequest`-shaped stub suffices —
  // matching the pattern used in `proxy.test.ts`.
  function robotsHeaderForPath(pathname: string): string | null {
    const req = {
      nextUrl: { protocol: "http:", pathname },
    } as unknown as NextRequest;
    return proxy(req).headers.get("x-robots-tag");
  }

  // Resolved `robots` directive each route inherits from its route-group
  // layout (no page-level override exists — asserted in the inheritance block
  // above).
  const NOINDEX = { index: false, follow: false } as const;

  // The authenticated `(app)` and `(auth)` MVP routes: noindex at BOTH layers.
  // `/matches/[id]` is represented by a concrete instance path because the
  // proxy matches on a resolved pathname, not the route template (Req 13.3).
  const noIndexRoutes = [
    {
      label: "(app) /upload",
      pathname: "/upload",
      layoutMetadata: metadata,
      requirement: "Req 9.13",
    },
    {
      label: "(app) /matches/[id]",
      pathname: "/matches/abc-123",
      layoutMetadata: metadata,
      requirement: "Req 13.3",
    },
    {
      label: "(auth) /login",
      pathname: "/login",
      layoutMetadata: authMetadata,
      requirement: "Req 8.7",
    },
    {
      label: "(auth) /register",
      pathname: "/register",
      layoutMetadata: authMetadata,
      requirement: "Req 8.8",
    },
  ] as const;

  it.each(noIndexRoutes)(
    "$label is noindex at the Metadata API layer ($requirement)",
    ({ layoutMetadata }) => {
      // The page inherits its route-group layout's directive.
      expect(layoutMetadata.robots).toEqual(NOINDEX);
    },
  );

  it.each(noIndexRoutes)(
    "$label is noindex at the response-header layer — X-Robots-Tag: noindex, nofollow ($requirement)",
    ({ pathname }) => {
      expect(robotsHeaderForPath(pathname)).toBe("noindex, nofollow");
    },
  );

  it.each(noIndexRoutes)(
    "$label is excluded from app/sitemap.ts ($requirement)",
    ({ pathname }) => {
      const urls = sitemap().map((entry) => new URL(entry.url).pathname);
      // No emitted sitemap pathname is, or is nested under, this route.
      const listed = urls.some(
        (p) => p === pathname || p.startsWith(`${pathname}/`),
      );
      expect(listed).toBe(false);
    },
  );

  it("`/` is indexable at the Metadata API layer — (marketing) sets no `robots` directive (Req 7.5, 8.10)", () => {
    expect(marketingMetadata.robots).toBeUndefined();
  });

  it("`/` carries NO X-Robots-Tag at the response-header layer, so it stays crawlable (Req 7.5, 8.10, 21.10)", () => {
    expect(robotsHeaderForPath("/")).toBeNull();
  });

  it("`/` is present in app/sitemap.ts as the one indexable MVP surface (Req 7.5)", () => {
    const urls = sitemap().map((entry) => new URL(entry.url).pathname);
    expect(urls).toContain("/");
  });
});
