# Design Document: seo-foundation

## Status

Proposed. Requirements approved (`requirements.md`). This design is the design-review-gate artifact for the `seo-foundation` spec.

## Overview

`seo-foundation` completes the **public-page SEO baseline** for the MatchLayer marketing surface (`matchlayer.net`). It is deliberately scoped to public discoverability and hardens one half of the single indexing policy defined in `seo.md` and ADR 0006; the privacy-critical non-indexing of authenticated/PII surfaces is owned by `phase-1-matching` Requirement 15 and is treated here as a fixed dependency, never restated.

This is **not a greenfield build.** The `frontend-redesign` spec already shipped a working slice of the SEO foundation that this design extends:

| Already exists (from `frontend-redesign`)                         | File                               |
| ----------------------------------------------------------------- | ---------------------------------- |
| `SITE_URL`, `SITE_NAME`, `SITE_DESCRIPTION`, `SITE_DEFAULT_TITLE` | `apps/web/src/lib/seo/site.ts`     |
| `buildMarketingMetadata()` + `MarketingMetadataInput`             | `apps/web/src/lib/seo/metadata.ts` |
| Barrel export                                                     | `apps/web/src/lib/seo/index.ts`    |
| Sitemap (allowlist `["/"]`, no `Sitemap` directive needed there)  | `apps/web/src/app/sitemap.ts`      |
| Robots (disallow only, **no `Sitemap` directive**)                | `apps/web/src/app/robots.ts`       |
| Security headers + `X-Robots-Tag` on non-public paths             | `apps/web/src/proxy.ts`            |
| `(marketing)` route group + landing page metadata                 | `apps/web/src/app/(marketing)/`    |

The design therefore expresses each requirement as a **delta** against this baseline. The bulk of the new work is: a branded default OG image with explicit dimensions and per-page overrides, a `Sitemap` directive on `robots.ts`, per-page metadata for the remaining public pages, Search Console verification wiring, the reserved (deferred) CSP-nonce/JSON-LD mechanism, structural guards enforcing "Metadata API only", and Core Web Vitals guardrails.

Language/stack is fixed by the repo and steering: **TypeScript**, Next.js App Router, the Next.js Metadata API. No implementation-language selection is required.

## Key design decisions

### D1 — Indexing status of `/login` and `/register` — RESOLVED: Option B (Public-but-noindex; ADR 0007)

**Resolution:** Option B was selected. `/login` and `/register` stay `noindex, nofollow` and out of the sitemap; they receive hygiene metadata only. Recorded in **ADR 0007**, with `seo.md`'s route table and this spec's requirements (Marketing_Surface glossary, Req 6.2) amended to match. The prose below is retained for context; the sitemap/robots/proxy sections reflect Option B.

This was the one genuine cross-spec conflict and the design did not silently resolve it.

- **`seo.md` route table** classifies `/login` and `/register` as **Public — "Full SEO, indexable, in sitemap."**
- **This spec's `requirements.md`** puts `/login` and `/register` in the `Marketing_Surface` glossary term, and Req 6.2 requires **every** Marketing_Surface route to be a sitemap entry, while Req 1–4 require full metadata for every Marketing_Surface page.
- **`frontend-redesign` already shipped the opposite:** `proxy.ts` stamps `/login` and `/register` with `X-Robots-Tag: noindex, nofollow`, and `sitemap.ts` omits them (its Req 8.9–8.10 deliberately kept them out of the index).

**Recommended resolution (Option A — follow `seo.md` + this spec's approved requirements):** treat `/login` and `/register` as indexable Public pages. Concretely:

1. Remove `/login` and `/register` from the `NOINDEX_PATH_PREFIXES` set in `proxy.ts`.
2. Add `/login` and `/register` to `PUBLIC_ROUTES` in `sitemap.ts`.
3. Give each a `metadata` export built from `buildMarketingMetadata`.

This keeps the two specs consistent with the authoritative steering doc. It does change behavior shipped by `frontend-redesign`, so it must be called out in the PR and confirmed at the design-review gate.

**Alternative (Option B — keep auth pages `noindex`):** indexing login/registration pages carries little marketing value and is a common SEO anti-pattern. Option B would keep them `noindex` and out of the sitemap, give them metadata for hygiene only, and would require amending `seo.md` and this spec's Req 6.2/glossary to reclassify the auth pages as a distinct "Public-but-noindex" class.

**Selected: Option B.** After review, indexing auth pages was judged to carry no marketing value and to overturn a deliberate, tested `frontend-redesign` decision for no benefit; ADR 0006 never actually classified the auth pages as indexable, so Option B corrects `seo.md` rather than overturning an ADR. The affected sections below (Public route table, Sitemap, Robots, Proxy, Testing) reflect Option B: `/login` and `/register` are absent from `PUBLIC_ROUTES`/the sitemap, `proxy.ts` keeps stamping them `noindex`, and the existing `non-indexing.test.ts` assertions for the auth pages are preserved.

### D2 — Default OG image: committed static asset, generated reproducibly

Req 4 needs a branded default OG image (violet→cyan gradient, Geist type) with explicit `og:image:width`/`height` and per-page overrides.

**Decision:** ship a **static `1200×630` PNG** at `apps/web/public/og/og-default.png`, generated once by a committed, re-runnable `next/og` (`ImageResponse`) script (`apps/web/scripts/generate-og-default.tsx`) so the asset stays on-brand and reproducible without hand-editing a binary. It is then served as a plain static file.

**Rationale:** a static asset has **zero per-request cost** (respects the `product.md` $20/mo ceiling), fixed dimensions (satisfies Req 4.5 cleanly), and no runtime/edge dependency. The builder centralizes the reference (Req 9.3), so per-page overrides remain a one-field change.

**Alternative considered:** the `opengraph-image.tsx` file convention with per-request `ImageResponse`. Rejected as the default because it decentralizes metadata away from the `buildMarketingMetadata` single-source (tension with Req 9/10) and adds per-request rendering cost. It stays available as the mechanism for a future page that wants a truly dynamic OG image.

### D3 — Search Console verification: DNS TXT first, Metadata-API tag as coded fallback

Req 12 permits DNS TXT **or** a Metadata-API verification tag, never inline script.

**Decision:** prefer **DNS TXT** at the registrar — no code, no CSP impact, honors Req 12.2 trivially, documented in a runbook. Provide a coded fallback path: a `verification` field wired through `site.ts` → `buildMarketingMetadata` (Next's `metadata.verification.google`), homepage-only, used only if DNS TXT is impractical. Verification meta tags set no cookies (Req 12.4 satisfied by construction).

### D4 — Extend the existing `lib/seo` module, do not fork it

All new helper surface (OG-image constants, verification config, the growing public-route table) lands **inside `apps/web/src/lib/seo/`** and is re-exported from the barrel, keeping the Metadata-API single-source intact (Req 9, Req 10). No new SEO helper location is introduced.

### D5 — Site origin stays a single hard-coded constant (with an env-var upgrade path)

`SITE_URL` in `site.ts` remains the single origin source feeding both the Metadata builder and the sitemap (satisfies Req 2.3, 2.4 by construction). It stays a constant rather than a `NEXT_PUBLIC_*` env var to avoid an env-drift-contract entry for a value that does not vary per environment. The design documents the promotion path to a public env var if a `noindex` staging origin is later needed.

## Architecture

```
apps/web/src/
├── lib/seo/                      # SEO_Helper_Module (Req 9) — extended, not replaced
│   ├── site.ts                   # + OG_DEFAULT (path/width/height/alt), SEARCH_CONSOLE_VERIFICATION
│   ├── metadata.ts               # buildMarketingMetadata: now always emits default OG image + verification
│   ├── routes.ts                 # NEW: PUBLIC_ROUTES single source (shared by sitemap + tests)
│   └── index.ts                  # barrel: re-export the new symbols
├── app/
│   ├── robots.ts                 # + Sitemap directive (absolute); disallow set unchanged
│   ├── sitemap.ts                # imports PUBLIC_ROUTES from lib/seo/routes.ts
│   └── (marketing)/
│       ├── layout.tsx            # group defaults (unchanged shape) + verification on home
│       ├── page.tsx              # `/` (exists)
│       ├── privacy/page.tsx      # NEW public page (policy requires it — security.md)
│       ├── terms/page.tsx        # NEW public page (policy requires it — security.md)
│       └── about/page.tsx        # NEW public page (thin, indexable)
├── proxy.ts                      # D1: remove /login,/register from noindex set (Option A)
├── scripts/generate-og-default.tsx  # NEW: reproducible OG-image generator (next/og)
└── public/og/og-default.png      # NEW: committed 1200×630 branded default OG image
```

Scope note on pages: `/pricing` is **deferred** — the product is pre-monetization (Stripe lands in Phase 7), so there is nothing to price and an indexable `/pricing` would be a thin/empty page. The design defines the pattern so `/pricing` slots in trivially when it exists; until then it is neither routed nor listed. `/privacy` and `/terms` are **required now** (`security.md` → "Privacy policy + Terms of Service published from Phase 1"). `/about` is a thin truthful page. Every public page listed in the sitemap must actually exist (Req 6.2 is satisfied against the realized route set, not aspirational routes).

## Components and Interfaces

### Metadata_Builder (`lib/seo/metadata.ts`) — extended

The existing `buildMarketingMetadata(input)` is extended, not rewritten. New behavior:

- **Always emits the default OG image** when `input.ogImage` is absent (Req 4.1). The current "no `og:image` when absent" behavior is replaced so no public page ships without a branded preview.
- **Emits `og:image:width`/`og:image:height`** for the default image from `OG_DEFAULT` (Req 4.5), and an `alt`.
- **Resolves OG image URLs absolutely** against `metadataBase` (Req 4.4) — already the case via `metadataBase`, made explicit and tested.
- **Enforces length budgets** (Req 9.4): a dev-time guard that throws (or emits a typed warning caught by a unit test) when `title > 60` or `description > 155` characters, so an over-long value fails a test rather than shipping.
- **Applies the site-level default title/description** when a page resolves none (Req 1.6) — already the fallback behavior; retained and tested.
- **Optionally attaches `verification`** (home page only) from `SEARCH_CONSOLE_VERIFICATION` when set (Req 12.3).

Signature is unchanged for callers; `MarketingMetadataInput` gains no required field. Per-page override of the OG image continues to flow through the existing `ogImage?` field (Req 4.3).

```ts
// simplified for illustration — full shape lives in the module
export interface MarketingMetadataInput {
  title?: string; // ≤ 60 (enforced)
  description?: string; // ≤ 155 (enforced)
  path?: string; // self-referential canonical, root-relative
  ogImage?: string; // per-page override; default applied when absent
}
```

[Source pattern: `apps/web/src/lib/seo/metadata.ts`](../../../apps/web/src/lib/seo/metadata.ts)

### Site config (`lib/seo/site.ts`) — extended

Adds:

- `OG_DEFAULT`: `{ path: "/og/og-default.png", width: 1200, height: 630, alt: string }` — the single source for the default OG image (Req 9.3).
- `SEARCH_CONSOLE_VERIFICATION`: optional string; unset by default (DNS-TXT path is primary, Req 12.1).

`SITE_URL`, `SITE_NAME`, `SITE_DESCRIPTION`, `SITE_DEFAULT_TITLE` are unchanged and remain the single origin/identity source (Req 2.3, D5).

### Public route table (`lib/seo/routes.ts`) — new

A single exported `PUBLIC_ROUTES` array is the **one allowlist** of indexable public paths, imported by `sitemap.ts` and asserted by tests. This removes the duplicate literal currently inside `sitemap.ts` and gives the sitemap/metadata/robots one authority to agree on (supports Req 2.4, 6.5). Default-deny: a route is added here only after it is classified Public per `seo.md`.

Route set (Option B, selected): `["/", "/about", "/privacy", "/terms"]`. `/login` and `/register` are excluded (Public-but-noindex; ADR 0007).

### Sitemap_Generator (`app/sitemap.ts`) — modified

- Imports `PUBLIC_ROUTES` from `lib/seo/routes.ts` instead of a local literal.
- Emits each route as an absolute URL via `new URL(path, SITE_URL)` (Req 6.4), matching each page's canonical (Req 2.4).
- Remains a generated `app/sitemap.ts`, never a static file (Req 6.1).
- Never emits an `(app)` or `/api/` path (Req 6.3) — guaranteed by the allowlist.

### Robots_Generator (`app/robots.ts`) — modified

- Keeps the existing `disallow: ["/api/", "/upload", "/matches", "/library", "/dashboard"]` (Req 5.2, 5.3).
- **Adds the `Sitemap` directive** pointing at the absolute sitemap URL (`new URL("/sitemap.xml", SITE_URL)`) — this is the deferred piece the current file explicitly left to this spec (Req 5.4).
- Remains a generated `app/robots.ts` (Req 5.1).
- Default-deny for unclassifiable routes is preserved by never adding an `Allow` for non-public paths (Req 5.5, 5.3).

Under Option A, `/login` and `/register` are simply absent from the disallow list (they were never disallowed in `robots.ts`; the noindex came from `proxy.ts`), so no robots change is needed for them beyond the proxy delta.

### Security-headers proxy (`proxy.ts`) — unchanged under Option B

- **Option B (selected):** no functional change. `/login` and `/register` remain in `NOINDEX_PATH_PREFIXES`, so they keep their `X-Robots-Tag: noindex, nofollow`. Only the explanatory docstring is updated to cite ADR 0007.
- **CSP is not modified by this spec** (Req 11.1): no `'unsafe-inline'` is added to `script-src` for SEO, and the existing Phase-1 CSP posture is preserved. Tightening CSP to nonces is Phase-6 work already noted in `proxy.ts`.

### New public pages

`privacy/page.tsx`, `terms/page.tsx`, `about/page.tsx` are Server Components exporting `metadata = buildMarketingMetadata({ path, title, description })` with unique titles/descriptions (Req 1.4, 1.5). Each renders the same `header → main → footer` landmark structure the landing page established, exactly one `<h1>`, non-skipping heading order, and internal links back to `/` so no page is orphaned (Req 7.1–7.5). Content for `/privacy` and `/terms` is the minimal-but-real policy text required by `security.md`.

### Reserved CSP-nonce / JSON-LD mechanism (deferred)

Per Req 11.2 and ADR 0006 Decision 5, **no JSON-LD payload ships in this spec.** The design records the mechanism for when it does: a per-request nonce generated in `proxy.ts`, threaded into the CSP `script-src` as `'nonce-<value>'`, and consumed by a `<script type="application/ld+json" nonce={nonce}>` on public pages only (Req 11.3). `'unsafe-inline'` is never used to enable JSON-LD (Req 11.1). This section is documentation of intent, not code to be written now (Req 11.4 keeps even `Organization`/`WebSite` gated behind this nonce path).

## Data Models

No database or API changes. This spec is entirely within `apps/web`. The only new persisted artifact is the committed static OG image binary. The "models" are the config constants (`OG_DEFAULT`, `PUBLIC_ROUTES`, `SEARCH_CONSOLE_VERIFICATION`) described above.

## Core Web Vitals strategy (Req 8)

CWV is guaranteed structurally rather than by a runtime SLA the app cannot enforce alone:

- **`next/font` (Geist)** is already wired in the root layout — no external font request, no FOIT/CLS (Req 8.4).
- **`next/image` with explicit `width`/`height`** is mandated for any raster added to a public page (Req 8.4); the landing page currently uses only inline SVG/gradient text, so it ships zero raster CLS risk.
- **Server Components by default** on the marketing surface keep client JS minimal, protecting LCP/INP.
- **Verification:** an optional Lighthouse CI run (free on GitHub Actions) against a built preview provides the LCP < 2.5s / CLS < 0.1 / INP < 200ms signal (Req 8.1–8.3). Because lab CWV is environment-noisy, CI treats it as a **reported budget with a warn threshold**, and the authoritative check is a documented manual Lighthouse pass in the release runbook. The hard, automated guarantees are the structural ones above (next/font, next/image with dimensions, RSC-first).

## Error Handling

- **Missing title/description** → site-level defaults applied, never an empty tag (Req 1.6).
- **Over-length title/description** → caught by the builder's length guard and surfaced as a failing unit test at build time (Req 9.4), not shipped.
- **Missing OG image override** → default branded image applied (Req 4.1).
- **Unset Search Console verification** → no verification tag emitted; DNS-TXT path assumed (Req 12.1).
- **A route added to `PUBLIC_ROUTES` that does not resolve** → caught by a test asserting every listed route renders (prevents a sitemap entry to a 404).

## Testing Strategy

**No correctness properties (property-based tests) are introduced.** `seo-foundation` produces static configuration and declarative metadata (constants, allowlists, and `Metadata` objects) rather than algorithms with an input space to fuzz, mirroring the `frontend-redesign` spec's testing decision. Behaviors that might otherwise be framed as properties — length bounds, canonical/sitemap origin agreement, default-deny route inclusion, the Metadata-API-only rule — are enforced by the deterministic Vitest unit/integration tests and structural guards below. Existing tests to preserve/extend: `apps/web/tests/sitemap.test.ts`, `apps/web/tests/non-indexing.test.ts`.

**Metadata_Builder (`lib/seo/metadata.ts`):**

- Title ≤ 60 and description ≤ 155 for every public page's resolved metadata (Req 1.2, 1.3, 9.4); an over-long input throws/fails.
- Canonical is absolute, rooted at `SITE_URL`, and self-referential per `path` (Req 2.1, 2.2).
- Default OG image is emitted (absolute URL, width 1200, height 630, alt) when no override (Req 4.1, 4.4, 4.5); a supplied `ogImage` overrides it (Req 4.3).
- OG (`og:title/description/image/url/type`) and Twitter `summary_large_image` present; `og:url` equals the canonical (Req 3.1–3.3); social title/description fall back to page title/description (Req 3.4).
- Missing title/description → site defaults (Req 1.6).
- Verification tag emitted only when configured, home page only (Req 12.3).

**Uniqueness across the Marketing_Surface (Req 1.4, 1.5):** a test that gathers the `metadata` export from every public page and asserts titles and descriptions are pairwise unique.

**Sitemap (`app/sitemap.ts`):** contains exactly the `PUBLIC_ROUTES` entries as absolute URLs matching canonicals (Req 6.2, 6.4); contains none of `/upload`, `/matches`, `/library`, `/dashboard`, `/api/` (Req 6.3); generated form (Req 6.1). Extend the existing test.

**Robots (`app/robots.ts`):** disallow set covers `/api/` and the `(app)` paths (Req 5.2); emits an absolute `Sitemap` directive (Req 5.4); no `(app)`/`/api/` path appears in any `Allow` (Req 5.3).

**Metadata-API-only guards (Req 10):**

- A structural test greps the `(marketing)` tree for hand-placed `<head>`, `<meta`, `<link rel="canonical"`, and inline social tags and fails on any hit (Req 10.2, 10.3).
- A boundary test asserts no `(app)`/`(auth)` route imports `@/lib/seo` (Req 10.4, ADR 0006), extending the existing non-indexing guard.

**Semantic HTML (Req 7):** for each new public page, assert exactly one `<h1>`, presence of `header`/`nav`/`main`/`footer` landmarks, and non-skipping heading order (unit render + optionally axe-core, matching the redesign's a11y approach).

**CSP untouched (Req 11.1):** a test asserts `proxy.ts` `script-src` contains no `'unsafe-inline'` **added for SEO** beyond the pre-existing Phase-1 value, and that no JSON-LD `<script>` is emitted on any public page (Req 11.2).

**Option B behavior (selected):** the existing `non-indexing.test.ts` assertions for `/login` and `/register` (noindex at both layers, excluded from the sitemap) are **preserved**. The only change to that file is updating the robots assertion, which previously required **no** `Sitemap` directive, to now require the absolute `Sitemap` directive this spec adds (Req 5.4).

## Requirements traceability

| Requirement                                          | Design coverage                                                                                 |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 1 Per-page metadata via Metadata API                 | Metadata_Builder (extended); uniqueness test; length guard                                      |
| 2 Self-referential canonical URLs                    | Builder `alternates.canonical`; `SITE_URL` single origin (D5); shared with sitemap              |
| 3 Open Graph + Twitter Card                          | Builder OG/Twitter emission + fallbacks                                                         |
| 4 Branded default OG image + overrides               | `OG_DEFAULT` + static asset (D2); builder default + `ogImage` override; width/height            |
| 5 Robots restricted to public routes                 | `robots.ts` disallow set + new `Sitemap` directive; default-deny                                |
| 6 Sitemap restricted to public routes                | `sitemap.ts` from `PUBLIC_ROUTES` allowlist; absolute URLs                                      |
| 7 Semantic HTML + single `<h1>`                      | New pages follow landing landmark pattern; per-page tests                                       |
| 8 Core Web Vitals budgets                            | `next/font` + `next/image` + RSC-first; Lighthouse (warn) + runbook                             |
| 9 Shared SEO helper module                           | Extend `lib/seo/` (D4); builder is the single entry point                                       |
| 10 Metadata API as only source                       | Grep/boundary guards; no hand-placed head markup                                                |
| 11 Reserved CSP-nonce for deferred JSON-LD           | Documented mechanism; no CSP change; no payload shipped                                         |
| 12 Search Console verification without inline script | DNS TXT first, Metadata-API tag fallback (D3)                                                   |
| 13 Boundary with authenticated non-indexing          | Depends on `phase-1-matching` Req 15; robots/sitemap stay consistent; no `(app)`/`/api/` change |

## Resolved review questions

1. **D1 / Option A vs B** — RESOLVED: **Option B** (`/login`, `/register` stay `noindex` and out of the sitemap; hygiene metadata only). Recorded in ADR 0007; `seo.md` and the requirements amended.
2. **`/about` scope** — RESOLVED: `/about` is in scope now; `/pricing` stays deferred until Phase 7.
3. **Search Console verification** — RESOLVED: DNS-TXT is the primary path, with the Metadata-API tag wired as a coded fallback (D3).
