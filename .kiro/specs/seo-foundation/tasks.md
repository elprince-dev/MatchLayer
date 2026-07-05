# Implementation Plan: seo-foundation

## Overview

This spec lands as **one PR** on branch `phase-1/seo-foundation` (per `conventions.md` branch naming) that completes the public-page SEO baseline for the MatchLayer marketing surface. It is a **delta on the SEO scaffolding `frontend-redesign` already shipped** (`apps/web/src/lib/seo/`, `app/sitemap.ts`, `app/robots.ts`, `src/proxy.ts`, the `(marketing)` route group) — no task rebuilds that scaffolding; each task extends or modifies a file that already exists, or adds a new public page/asset alongside it.

The implementation language is **TypeScript** (Next.js App Router, the Next.js Metadata API). The design (`design.md`) specifies a concrete stack throughout — no implementation-language selection is required. This is **not a property-based-testing feature** (design → Testing Strategy); tasks use Vitest unit/integration tests and structural guards, extending the existing `apps/web/tests/sitemap.test.ts` and `apps/web/tests/non-indexing.test.ts`.

The acceptance signal is: the six required CI checks the foundation locked in stay green (`backend`, `frontend`, `shared-types`, `security`, `openapi-drift`, `required-checks`), plus the new Vitest suites below, plus a manual Lighthouse pass recorded in the release runbook.

**Build order:** SEO helper module core (config + builder) → default OG image → sitemap/robots/proxy deltas → public pages + auth-page metadata → Metadata-API-only structural guards → Core Web Vitals + Search Console verification + runbook → final checkpoint.

> **Decision gate (design D1) — RESOLVED: Option B (ADR 0007).** `/login` and `/register` stay `noindex` and out of the sitemap (hygiene metadata only). Concretely: they are **absent** from `PUBLIC_ROUTES`, `proxy.ts` keeps stamping them `noindex` (no functional change, docstring only), the `(auth)` layout keeps its `robots: { index: false, follow: false }`, and the existing `non-indexing.test.ts` auth-page assertions are preserved. `seo.md`'s route table and this spec's requirements (Marketing_Surface glossary, Req 6.2) were amended to match, and ADR 0007 records the decision. Task 3.3 is therefore a docstring-only edit; task 4.3 is hygiene-only metadata on still-`noindex` pages.

## Tasks

- [x] 1. SEO helper module core (`apps/web/src/lib/seo/`)
  - [x] 1.1 Extend `site.ts` with the default-OG and verification config
    - Add `OG_DEFAULT = { path: "/og/og-default.png", width: 1200, height: 630, alt: <descriptive branded alt> } as const` as the single source for the default Open Graph image (Req 4.1, 4.5, 9.3).
    - Add `SEARCH_CONSOLE_VERIFICATION: string | undefined` (unset by default — DNS-TXT is the primary path per design D3; this is the coded fallback for Req 12.3).
    - Leave `SITE_URL`, `SITE_NAME`, `SITE_DESCRIPTION`, `SITE_DEFAULT_TITLE` unchanged (single origin/identity source, Req 2.3; design D5).
    - _Requirements: 4.1, 4.5, 9.3, 12.3, 2.3_
    - _Design: Site config (`lib/seo/site.ts`), D2, D3, D5_

  - [x] 1.2 Add the single public-route allowlist `routes.ts`
    - Create `apps/web/src/lib/seo/routes.ts` exporting `PUBLIC_ROUTES` (root-relative, default-deny) as the one authority shared by the sitemap and its tests. Option A set: `["/", "/about", "/privacy", "/terms", "/login", "/register"]`. A top-of-file comment states the default-deny rule (a route is added only after it is classified Public per `seo.md`).
    - _Requirements: 6.2, 6.5, 2.4, 5.5_
    - _Design: Public route table (`lib/seo/routes.ts`)_

  - [x] 1.3 Extend `buildMarketingMetadata` in `metadata.ts`
    - Always emit the default OG image from `OG_DEFAULT` when `input.ogImage` is absent (Req 4.1), with absolute URL resolution via `metadataBase` (Req 4.4) and `width`/`height`/`alt` (Req 4.5); a supplied `ogImage` overrides it (Req 4.3).
    - Emit the Twitter `summary_large_image` image alongside OG (Req 3.3); keep `og:url` equal to the canonical (Req 3.2) and the title/description social fallback (Req 3.4).
    - Add a length guard: throw (or emit a value a unit test asserts on) when `title > 60` or `description > 155` characters (Req 9.4, 1.2, 1.3).
    - Attach `verification` (home page only, gated on a builder option/flag) from `SEARCH_CONSOLE_VERIFICATION` when set (Req 12.3).
    - Keep the site-default fallback for missing title/description (Req 1.6) and the no-`robots` behavior that keeps public pages indexable (Req 7.5). Signature stays backward-compatible for existing callers.
    - _Requirements: 1.2, 1.3, 1.6, 3.2, 3.3, 3.4, 4.1, 4.3, 4.4, 4.5, 9.4, 12.3_
    - _Design: Metadata_Builder (`lib/seo/metadata.ts`)_

  - [x] 1.4 Update the barrel `index.ts`
    - Re-export `OG_DEFAULT`, `SEARCH_CONSOLE_VERIFICATION`, and `PUBLIC_ROUTES` so all callers import from `@/lib/seo` (Req 9.1, 9.2). Keep the scope-guard comment that these are `(marketing)`-only.
    - _Requirements: 9.1, 9.2_
    - _Design: D4_

  - [x] 1.5 Unit tests for the extended builder and config
    - Assert: title ≤ 60 / description ≤ 155 enforced (over-long throws); canonical absolute + self-referential per `path`; default OG image emitted (absolute URL, 1200×630, alt) when no override and overridden by `ogImage`; OG (`og:title/description/image/url/type`) + Twitter `summary_large_image` present with `og:url` == canonical and title/description fallback; missing title/description → site defaults; verification emitted only when configured.
    - _Requirements: 1.2, 1.3, 1.6, 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 4.1, 4.3, 4.4, 4.5, 9.4, 12.3_
    - _Design: Testing Strategy → Metadata_Builder_

- [x] 2. Branded default OG image
  - [x] 2.1 Author the reproducible OG-image generator
    - Create `apps/web/scripts/generate-og-default.tsx` using `next/og` `ImageResponse` to render a 1200×630 image with the violet→cyan brand gradient and Geist type per `design.md` (Req 4.2). Add a `package.json` script (e.g. `og:generate`) that runs it and writes the PNG.
    - _Requirements: 4.2_
    - _Design: D2_

  - [x] 2.2 Generate and commit the static asset
    - Run the generator once; commit `apps/web/public/og/og-default.png` (1200×630). The asset is then served statically (zero per-request cost, fixed dimensions for Req 4.5).
    - _Requirements: 4.1, 4.2, 4.5_
    - _Design: D2_

- [x] 3. Sitemap, robots, and proxy deltas
  - [x] 3.1 Point `sitemap.ts` at the shared allowlist
    - Modify `apps/web/src/app/sitemap.ts` to import `PUBLIC_ROUTES` from `@/lib/seo` instead of its local literal; emit each as an absolute URL via `new URL(path, SITE_URL)` matching each page's canonical. Keep it a generated `app/sitemap.ts`; never emit `(app)`/`/api/` paths (guaranteed by the allowlist).
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 2.4_
    - _Design: Sitemap_Generator_

  - [x] 3.2 Add the `Sitemap` directive to `robots.ts`
    - Modify `apps/web/src/app/robots.ts` to emit `sitemap: new URL("/sitemap.xml", SITE_URL).toString()` (the piece the current file explicitly deferred to this spec — Req 5.4). Keep the existing `disallow` set (`/api/`, `/upload`, `/matches`, `/library`, `/dashboard`) and never add an `Allow` for a non-public path (Req 5.2, 5.3, 5.5).
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_
    - _Design: Robots_Generator_

  - [x] 3.3 (Option B) Keep the proxy noindex set; update its docstring only
    - Leave `/login` and `/register` in `apps/web/src/proxy.ts` `NOINDEX_PATH_PREFIXES` (Public-but-noindex, ADR 0007). Update the docstring to cite ADR 0007. Do not touch the CSP, HSTS, or the `X-Robots-Tag` stamping (Req 11.1 — no CSP change for SEO).
    - _Requirements: 5.3, 11.1_
    - _Design: D1 (Option B), Security-headers proxy_

  - [x] 3.4 Update sitemap/robots/non-indexing tests
    - Extend `apps/web/tests/sitemap.test.ts`: sitemap contains exactly `PUBLIC_ROUTES` as absolute URLs matching canonicals (Req 6.2, 6.4) and none of `/upload`, `/matches`, `/library`, `/dashboard`, `/login`, `/register`, `/api/` (Req 6.3). Add a robots test asserting the absolute `Sitemap` directive (Req 5.4) and the disallow set (Req 5.2). **Option B:** in `apps/web/tests/non-indexing.test.ts`, keep all `/login`/`/register` noindex + sitemap-exclusion assertions; the only edit is flipping the existing "robots emits no `Sitemap` directive" assertion to require the absolute `Sitemap` directive now that Req 5.4 adds it.
    - _Requirements: 5.2, 5.3, 5.4, 6.2, 6.3, 6.4_
    - _Design: Testing Strategy → Sitemap, Robots, Option A behavior_

- [x] 4. Public pages and auth-page metadata
  - [x] 4.1 Add `/privacy` and `/terms` public pages
    - Create `apps/web/src/app/(marketing)/privacy/page.tsx` and `.../terms/page.tsx` (Server Components) with the minimal-but-real policy/ToS text required by `security.md` → Privacy. Each exports `metadata = buildMarketingMetadata({ path, title, description })` with a unique title/description (Req 1.4, 1.5), renders the `header → main → footer` landmarks, exactly one `<h1>`, non-skipping heading order, and an internal link back to `/` (Req 7.1–7.5).
    - _Requirements: 1.4, 1.5, 7.1, 7.2, 7.3, 7.5_
    - _Design: New public pages; Scope note (privacy/terms required now)_

  - [x] 4.2 Add the thin `/about` public page
    - Create `apps/web/src/app/(marketing)/about/page.tsx` with a truthful Phase-1 capability description (ATS simulation + keyword/TF-IDF match analysis — no semantic/AI/LLM claims, `product.md` honesty rule), unique metadata via the builder, and the same landmark/heading/linking structure as 4.1.
    - _Requirements: 1.4, 1.5, 7.1, 7.2, 7.3, 7.5_
    - _Design: New public pages; Scope note (`/about` in, `/pricing` deferred)_

  - [x] 4.3 (Option B) Auth-page metadata — hygiene only, keep `noindex`
    - The `(auth)` layout keeps `robots: { index: false, follow: false }`; `/login` and `/register` stay out of `PUBLIC_ROUTES`/the sitemap. Do **not** attach `buildMarketingMetadata` (it would add canonical/OG chrome to a noindex page and trip the `non-indexing.test.ts` "no canonical/OG on (auth)" guard). If unique page titles are desired, set a plain `title`/`description` via the Metadata API on each auth page without canonical/OG. Simplest compliant path: leave the auth pages as-is (they already inherit the layout's noindex and title). This task is effectively a no-op verification under Option B.
    - _Requirements: 1.4, 1.5, 10.4_
    - _Design: D1 (Option B), ADR 0007_

  - [x] 4.4 Uniqueness + render tests across the Marketing_Surface
    - Add a test that gathers the `metadata` export of every public page and asserts titles and descriptions are pairwise unique (Req 1.4, 1.5), and that every route in `PUBLIC_ROUTES` resolves to a rendering page (guards against a sitemap entry to a 404). Per-page tests assert one `<h1>`, the four landmarks, and non-skipping heading order (Req 7.1–7.3), matching the redesign's a11y approach.
    - _Requirements: 1.4, 1.5, 7.1, 7.2, 7.3_
    - _Design: Testing Strategy → Uniqueness, Semantic HTML_

- [x] 5. Metadata-API-only structural guards
  - [x] 5.1 Guard against hand-placed head markup and cross-boundary imports
    - Add a structural test that greps the `(marketing)` tree for hand-placed `<head>`, `<meta`, `<link rel="canonical"`, and inline social tags and fails on any hit (Req 10.2, 10.3). Extend the existing non-indexing/boundary guard to assert no `(app)`/`(auth)` route imports `@/lib/seo` (Req 10.4; ADR 0006, ADR 0007) — under Option B the `(auth)` pages must **not** import the marketing builder (that would add canonical/OG to a noindex page), so this guard stays strict with no exemption.
    - _Requirements: 10.1, 10.2, 10.3, 10.4_
    - _Design: Testing Strategy → Metadata-API-only guards_

  - [x] 5.2 Assert the CSP is untouched and no JSON-LD ships
    - Add a test asserting `proxy.ts` `script-src` gains no `'unsafe-inline'` for SEO beyond the pre-existing Phase-1 value (Req 11.1) and that no `<script type="application/ld+json">` is emitted on any public page (Req 11.2). Document the reserved per-request CSP-nonce mechanism for future JSON-LD in a code comment / the runbook, shipping no payload now (Req 11.3, 11.4).
    - _Requirements: 11.1, 11.2, 11.3, 11.4_
    - _Design: Reserved CSP-nonce / JSON-LD mechanism_

- [x] 6. Core Web Vitals, Search Console verification, and runbook
  - [x] 6.1 Enforce the structural CWV guarantees
    - Confirm `next/font` (Geist) and `next/image` usage on every public page; mandate explicit `width`/`height` on any raster added by 4.1/4.2 pages (Req 8.4). Add/keep an ESLint guard (`@next/next/no-img-element`) so a raw `<img>` fails lint. No public page introduces layout shift from unsized media (Req 8.2).
    - _Requirements: 8.2, 8.4_
    - _Design: Core Web Vitals strategy_

  - [x] 6.2 Wire Search Console verification (DNS-first) and document it
    - Primary path: document the DNS TXT record in the release runbook (no code, no CSP impact — Req 12.1, 12.2). Coded fallback: ensure `SEARCH_CONSOLE_VERIFICATION` flows through the builder to a home-page `verification` meta tag via the Metadata API only (Req 12.3), never inline script, and sets no cookie (Req 12.4).
    - _Requirements: 12.1, 12.2, 12.3, 12.4_
    - _Design: D3_

  - [x] 6.3 Record the CWV + verification steps in the release runbook
    - Add a runbook section: the manual Lighthouse pass (LCP < 2.5s, CLS < 0.1, INP < 200ms — Req 8.1–8.3) as the authoritative CWV check, plus the Search Console DNS-TXT verification step. Optionally add a warn-only Lighthouse CI job (free on GitHub Actions) that reports but does not gate.
    - _Requirements: 8.1, 8.2, 8.3_
    - _Design: Core Web Vitals strategy_

- [x] 7. Final checkpoint — full suite green
  - Ran `pnpm --filter @matchlayer/web build` (✓ compiled; `/about`, `/privacy`, `/terms`, `/robots.txt`, `/sitemap.xml` prerendered), plus `typecheck`, `lint`, `format`, and the full Vitest suite (336 passed / 8 skipped across 33 files). The `robots.txt` `Sitemap` directive, the public-route-only sitemap, per-page unique metadata, the branded OG image, and the `/login`/`/register` `noindex` (Option B) are all asserted by tests. Live post-deploy `curl` smoke and the manual Lighthouse CWV pass are documented in `docs/runbooks/seo-release.md` for release time.

## Notes

- D1 is resolved to **Option B** (ADR 0007): all tasks are required as written. Tasks 3.3 and 4.3 are reduced to docstring/no-op verification under Option B (see the decision-gate note above).
- No backend, database, or API changes — this spec is entirely within `apps/web`.
- The authenticated/PII non-indexing guarantee is owned by `phase-1-matching` Req 15 and is a fixed dependency here (Req 13); no task modifies `(app)` routes or `/api/*` responses.
- `/pricing` is deferred until Phase 7 (nothing to price pre-monetization); the design defines the pattern to add it later.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "2.1"] },
    { "id": 2, "tasks": ["1.5", "2.2", "3.1", "3.2", "3.3"] },
    { "id": 3, "tasks": ["3.4", "4.1", "4.2", "4.3"] },
    { "id": 4, "tasks": ["4.4", "5.1", "5.2", "6.1", "6.2"] },
    { "id": 5, "tasks": ["6.3"] },
    { "id": 6, "tasks": ["7"] }
  ]
}
```
