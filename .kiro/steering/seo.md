# MatchLayer — SEO & Indexing Policy

Always-loaded baseline for search-engine optimization **and** the privacy-critical rule that governs what must never be indexed. Read this alongside `security.md`. Full rationale in `docs/adr/0006-seo-and-indexing-policy.md`.

## The one rule that overrides everything

**SEO is for public pages. PII pages must never be indexed.** MatchLayer renders Restricted PII (resume text, job descriptions, match results) on authenticated surfaces. Making those crawlable would be a PII-exfiltration vector (`security.md` threat model). SEO and PII-page indexing point in opposite directions — never conflate them.

## Route classification (mandatory, no default)

Every route is classified before merge. There is no "unclassified" state.

| Class             | Examples                                                                             | Indexing                                                 |
| ----------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------- |
| **Public**        | `/`, `/pricing`, `/about`, `/privacy`, `/terms`, `/login`, `/register`               | Full SEO, indexable, in sitemap                          |
| **Authenticated** | everything in the `(app)` route group: `/upload`, `/matches/[id]`, library, settings | `noindex, nofollow`, robots-disallowed, never in sitemap |
| **API**           | `/api/v1/*`                                                                          | `X-Robots-Tag: noindex, nofollow`                        |

When adding a route, if you cannot confidently classify it Public, it is Authenticated. Default-deny.

## Non-indexing controls for Authenticated + API surfaces

- **Metadata API:** the `(app)` route-group layout exports `robots: { index: false, follow: false }`, inherited by all nested authenticated routes.
- **Response header:** authenticated HTML responses and every `/api/v1/*` response set `X-Robots-Tag: noindex, nofollow`.
- **robots.txt:** disallow `/api/` and the authenticated app paths (`/upload`, `/matches`, library, settings).
- **Sitemap:** PII-bearing and authenticated routes are excluded from `sitemap.xml` entirely.
- **No share-by-default.** A match result is private. Any future "public shareable result" feature requires a new ADR and must strip PII before exposure.

## Public-page SEO baseline (marketing surface)

Owned by the `seo-foundation` spec; realized as marketing pages are built. Baseline expectations:

- **Source of truth:** the Next.js **Metadata API** (`metadata` / `generateMetadata` exports). No hand-placed `<head>` tags scattered in components. Shared helpers live in `apps/web/src/lib/seo/`.
- **Per page:** unique `<title>` (≤ 60 chars), meta description (≤ 155 chars), a self-referential **canonical URL**.
- **Social:** Open Graph (`og:title`, `og:description`, `og:image`, `og:url`, `og:type`) and Twitter Card (`summary_large_image`) tags. A default branded OG image; per-page overrides where it matters.
- **Sitemap & robots:** generated via `app/sitemap.ts` and `app/robots.ts` (not static files), so they stay in sync with the route table.
- **Semantic HTML:** one `<h1>` per page, logical heading order, landmark elements (`<header>`, `<nav>`, `<main>`, `<footer>`), descriptive `alt` text. This overlaps with the `design.md` accessibility rules — accessible markup is also good SEO.
- **Performance / Core Web Vitals:** lean toward Server Components, `next/image`, `next/font` (Geist is already configured), and avoid layout shift. Targets: LCP < 2.5s, CLS < 0.1, INP < 200ms on the marketing pages.
- **Crawlability:** clean, human-readable URLs; no orphan public pages; internal links between public pages.

## Structured data (JSON-LD)

- **Deferred in Phase 1.** No marketing pages rich enough to warrant it yet.
- **When it ships:** JSON-LD is injected via `<script type="application/ld+json">` using a **per-request CSP nonce** wired into `script-src`. Never relax CSP to `'unsafe-inline'` or a broad hash allowance for structured data. See `security.md` → security headers and ADR 0006.
- Likely types when added: `Organization`, `WebSite` + `SearchAction`, `SoftwareApplication` / `Product` for pricing.

## Analytics & consent

- Any SEO/analytics tag (e.g., Search Console verification, privacy-respecting analytics) is a **Public-page-only** concern and must honor the cookie/consent rule in `security.md` → Privacy. No analytics on authenticated PII pages without an explicit, documented decision.
- Search Console verification via DNS TXT or a Metadata API verification tag — never an inline script that weakens CSP.

## Anti-patterns to refuse

- Adding `sitemap` entries, canonical tags, or OG tags to any `(app)`/PII route.
- "SEO everywhere" changes that touch authenticated routes.
- Inline JSON-LD without a nonce.
- Hand-written `<head>`/`<meta>` tags in page components instead of the Metadata API.
- Indexing a route because it "seems harmless" without classifying it.
- Relying on auth alone to keep PII out of search — `noindex` is required regardless (defense in depth).
