# Requirements Document

## Introduction

The `seo-foundation` spec owns public-page search-engine optimization for the MatchLayer marketing surface (`matchlayer.net`). Its job is to make the public, non-PII pages — `/`, `/pricing`, `/about`, `/privacy`, `/terms`, and the auth entry pages — discoverable and well-presented in search results and social shares, while staying strictly inside the indexing policy defined in `seo.md` and ADR 0006.

This spec is deliberately separate from `phase-1-matching`. That spec owns the privacy-critical non-indexing of authenticated, PII-bearing surfaces (its Requirement 15). This spec owns only the public discoverability side of the same policy. The two surfaces point in opposite directions and are governed by one policy split across two specs; the authenticated non-indexing guarantee is referenced here as a dependency, never duplicated.

Scope is the public SEO baseline from `seo.md` → "Public-page SEO baseline": per-page metadata via the Next.js Metadata API, self-referential canonical URLs, Open Graph and Twitter Card tags with a branded default OG image, generated `app/robots.ts` and `app/sitemap.ts` that list only public routes, semantic HTML landmarks with a single-`h1` rule, Core Web Vitals budgets, and a shared SEO helper module under `apps/web/src/lib/seo/`. The CSP-nonce mechanism for JSON-LD is reserved (mechanism defined) but structured-data payloads are deferred per ADR 0006 Decision 5. Search Console verification is in scope only via DNS TXT or a Metadata API verification tag.

Out of scope / explicit non-goals: any change touching authenticated `(app)` routes or `/api/*` responses (owned by `phase-1-matching` Requirement 15); shipping JSON-LD structured-data payloads (deferred per ADR 0006 — only the nonce mechanism is reserved); paid SEO/analytics tooling (the Phases 1–5 $20/mo cost ceiling in `product.md`); backlink/off-page SEO; and content marketing.

Steering clauses are referenced rather than restated; where this document cites a rule (e.g., title length, CWV targets, CSP constraints), the cited steering document remains authoritative.

## Glossary

- **Marketing_Surface**: The set of Public routes per the route classification in `seo.md` — `/`, `/pricing`, `/about`, `/privacy`, `/terms`, and the auth entry pages (`/login`, `/register`). These are the only routes this spec applies SEO to. Realized in the `(marketing)` route group per `structure.md`.
- **Indexing_Policy**: The mandatory route-classification and non-indexing policy defined in `seo.md` and ADR 0006. Classifies every route as Public, Authenticated, or API, and is default-deny (an unclassifiable route is Authenticated).
- **Authenticated_Surface**: Every route in the Next.js `(app)` route group and every `/api/*` response, as classified by the Indexing_Policy. Owned for non-indexing by `phase-1-matching` Requirement 15; out of scope for this spec.
- **Metadata_API**: The Next.js App Router metadata mechanism (`metadata` and `generateMetadata` exports). Per `conventions.md`, the only sanctioned source of page metadata.
- **Metadata_Builder**: A shared helper in the SEO_Helper_Module that produces Metadata_API objects (title, description, canonical, Open Graph, Twitter Card) for a given Marketing_Surface page.
- **SEO_Helper_Module**: The shared module under `apps/web/src/lib/seo/` (per `structure.md` and `conventions.md`) that houses the Metadata_Builder, OG image helpers, and the canonical/site-URL configuration.
- **Sitemap_Generator**: The generated `app/sitemap.ts` (per `structure.md`) that emits `sitemap.xml`. Lists only Marketing_Surface routes.
- **Robots_Generator**: The generated `app/robots.ts` (per `structure.md`) that emits `robots.txt`. Allows public crawling and disallows the Authenticated_Surface paths.
- **Canonical_URL**: A self-referential, absolute `https://matchlayer.net` URL declared per page via the Metadata_API `alternates.canonical` field.
- **OG_Image**: The Open Graph / Twitter Card preview image. A default branded image using the violet→cyan brand gradient and Geist type per `design.md`, with per-page overrides where they add value.
- **Twitter_Card**: The Twitter Card metadata of type `summary_large_image` per `seo.md`.
- **Core_Web_Vitals_Budget**: The performance budget for Marketing_Surface pages from `seo.md`: Largest Contentful Paint (LCP) < 2.5s, Cumulative Layout Shift (CLS) < 0.1, Interaction to Next Paint (INP) < 200ms.
- **CSP_Nonce**: The per-request nonce wired into the Content-Security-Policy `script-src` directive, the only sanctioned mechanism for future JSON-LD per `security.md` and ADR 0006 Decision 5.
- **Search_Console_Verification**: Google Search Console site-ownership verification, performed via a DNS TXT record or a Metadata_API verification tag — never an inline script.
- **Web_App**: The Next.js application at `apps/web/` (per `structure.md`).

## Requirements

### Requirement 1: Per-Page Metadata via the Metadata API

**User Story:** As a job seeker searching for an ATS analysis tool, I want each MatchLayer marketing page to show a clear, distinct title and description in search results, so that I can tell what the page offers before clicking.

#### Acceptance Criteria

1. THE Metadata_Builder SHALL produce page metadata exclusively through the Metadata_API (`metadata` or `generateMetadata` exports), consistent with `conventions.md`.
2. WHERE a route belongs to the Marketing_Surface, THE Metadata_Builder SHALL emit a `<title>` of at most 60 characters.
3. WHERE a route belongs to the Marketing_Surface, THE Metadata_Builder SHALL emit a meta description of at most 155 characters.
4. THE Metadata_Builder SHALL assign each Marketing_Surface page a title that is unique across the Marketing_Surface.
5. THE Metadata_Builder SHALL assign each Marketing_Surface page a meta description that is unique across the Marketing_Surface.
6. IF a Marketing_Surface page is rendered without a resolvable title or description, THEN THE Metadata_Builder SHALL apply a defined site-level default title and description rather than emitting an empty value.

### Requirement 2: Self-Referential Canonical URLs

**User Story:** As a site owner, I want each public page to declare its own canonical URL, so that search engines consolidate ranking signals on one address and avoid duplicate-content penalties.

#### Acceptance Criteria

1. THE Metadata_Builder SHALL emit a self-referential Canonical_URL for every Marketing_Surface page via the Metadata_API `alternates.canonical` field.
2. THE Metadata_Builder SHALL construct each Canonical_URL as an absolute URL rooted at the configured `https://matchlayer.net` site origin.
3. THE SEO_Helper_Module SHALL read the site origin from a single configuration source, so that the Canonical_URL host is defined in one place.
4. THE Sitemap_Generator and THE Metadata_Builder SHALL derive page URLs from the same site-origin configuration source, so that a sitemap entry and its page's Canonical_URL agree for every Marketing_Surface page.

### Requirement 3: Open Graph and Twitter Card Tags

**User Story:** As a user sharing a MatchLayer link on social media, I want the shared link to render a rich preview, so that the post looks credible and communicates what MatchLayer does.

#### Acceptance Criteria

1. THE Metadata_Builder SHALL emit Open Graph tags `og:title`, `og:description`, `og:image`, `og:url`, and `og:type` for every Marketing_Surface page, per `seo.md`.
2. THE Metadata_Builder SHALL set `og:url` to the page's Canonical_URL.
3. THE Metadata_Builder SHALL emit Twitter_Card metadata of type `summary_large_image` for every Marketing_Surface page.
4. WHERE a Marketing_Surface page does not override its social title or description, THE Metadata_Builder SHALL reuse that page's `<title>` and meta description for the corresponding Open Graph and Twitter_Card fields.

### Requirement 4: Branded Default OG Image with Per-Page Overrides

**User Story:** As a brand owner, I want shared links to carry a consistent MatchLayer-branded preview image, so that shared content is recognizable as ours.

#### Acceptance Criteria

1. THE SEO_Helper_Module SHALL provide a default branded OG_Image that is applied to any Marketing_Surface page lacking a page-specific override.
2. THE default OG_Image SHALL use the violet→cyan brand gradient and Geist type per `design.md`.
3. WHERE a Marketing_Surface page supplies a page-specific OG_Image, THE Metadata_Builder SHALL use that override in place of the default OG_Image for that page.
4. THE Metadata_Builder SHALL reference each OG_Image by an absolute URL rooted at the configured site origin.
5. THE Metadata_Builder SHALL emit OG_Image dimension metadata (`og:image:width` and `og:image:height`) for the default OG_Image.

### Requirement 5: Robots Generator Restricted to Public Routes

**User Story:** As a privacy-conscious user, I want the site's robots rules to actively keep crawlers away from authenticated and API paths, so that my resume and match data can never be discovered through crawling.

#### Acceptance Criteria

1. THE Robots_Generator SHALL be implemented as a generated `app/robots.ts` per `structure.md`, not as a static `robots.txt` file.
2. THE Robots_Generator SHALL emit `Disallow` rules covering `/api/` and the Authenticated_Surface application paths defined by the Indexing_Policy (including `/upload`, `/matches`, library, and settings paths).
3. THE Robots_Generator SHALL NOT emit any Authenticated_Surface route path or `/api/` path inside an `Allow` rule; emitting such a path as allowed is a privacy defect, not solely an SEO defect.
4. THE Robots_Generator SHALL emit a `Sitemap` directive pointing at the absolute Sitemap_Generator URL rooted at the configured site origin.
5. IF a route cannot be confidently classified Public under the Indexing_Policy, THEN THE Robots_Generator SHALL treat that route as Authenticated and SHALL NOT expose it to crawling, consistent with the default-deny rule in `seo.md`.

### Requirement 6: Sitemap Generator Restricted to Public Routes

**User Story:** As a site owner, I want the sitemap to list only public marketing pages, so that search engines crawl what should be discoverable and never receive a pointer to a PII-bearing page.

#### Acceptance Criteria

1. THE Sitemap_Generator SHALL be implemented as a generated `app/sitemap.ts` per `structure.md`, not as a static `sitemap.xml` file.
2. THE Sitemap_Generator SHALL include every Marketing_Surface route as a sitemap entry.
3. THE Sitemap_Generator SHALL NOT emit any Authenticated_Surface route or `/api/` path as a sitemap entry; emitting such a path is a privacy defect, not solely an SEO defect.
4. THE Sitemap_Generator SHALL express each entry as an absolute URL rooted at the configured site origin, matching the page's Canonical_URL.
5. WHEN a new route is added to the Web_App, THE Sitemap_Generator SHALL include that route only WHERE the Indexing_Policy classifies it Public, so that the sitemap stays in sync with the route table without exposing non-public routes.

### Requirement 7: Semantic HTML Landmarks and Single-H1 Rule

**User Story:** As a search crawler and as a screen-reader user, I want public pages to use clear, semantic structure, so that page content and hierarchy are unambiguous.

#### Acceptance Criteria

1. THE Web_App SHALL render exactly one `<h1>` element on each Marketing_Surface page.
2. THE Web_App SHALL render headings on each Marketing_Surface page in non-skipping descending order (no heading level is skipped on the way down), per the semantic-HTML rule shared by `seo.md` and `design.md`.
3. THE Web_App SHALL render the landmark elements `<header>`, `<nav>`, `<main>`, and `<footer>` on each Marketing_Surface page.
4. THE Web_App SHALL provide descriptive `alt` text for every content image on a Marketing_Surface page, per `seo.md` and `design.md`.
5. THE Web_App SHALL place internal links between Marketing_Surface pages so that no Public page is an orphan, per the crawlability rule in `seo.md`.

### Requirement 8: Core Web Vitals Budgets on Public Pages

**User Story:** As a visitor on a typical connection, I want marketing pages to load fast and stay stable, so that the experience feels modern and I do not abandon the page.

#### Acceptance Criteria

1. THE Web_App SHALL meet a Largest Contentful Paint of less than 2.5 seconds on each Marketing_Surface page, per the Core_Web_Vitals_Budget in `seo.md`.
2. THE Web_App SHALL keep Cumulative Layout Shift below 0.1 on each Marketing_Surface page, per the Core_Web_Vitals_Budget in `seo.md`.
3. THE Web_App SHALL keep Interaction to Next Paint below 200 milliseconds on each Marketing_Surface page, per the Core_Web_Vitals_Budget in `seo.md`.
4. THE Web_App SHALL serve Marketing_Surface raster imagery through `next/image` and fonts through `next/font` (Geist), per `seo.md` and `design.md`, so that image loading and font loading do not introduce layout shift.

### Requirement 9: Shared SEO Helper Module

**User Story:** As a developer adding a new public page, I want a single shared place to build its metadata, so that every page gets correct, consistent SEO without copy-pasting head logic.

#### Acceptance Criteria

1. THE SEO_Helper_Module SHALL reside under `apps/web/src/lib/seo/`, per `structure.md` and `conventions.md`.
2. THE SEO_Helper_Module SHALL expose the Metadata_Builder as the shared entry point for producing Marketing_Surface page metadata.
3. THE SEO_Helper_Module SHALL centralize the site-origin configuration, the default OG_Image, and the site-level default title and description.
4. THE Metadata_Builder SHALL enforce the title length bound of Requirement 1 and the description length bound of Requirement 1 for every page it produces metadata for.

### Requirement 10: Metadata API as the Only Source of Truth

**User Story:** As a maintainer, I want all page metadata to flow through one mechanism, so that metadata is consistent, reviewable, and never silently diverges across components.

#### Acceptance Criteria

1. THE Web_App SHALL source all Marketing_Surface page metadata from the Metadata_API, per `conventions.md`.
2. THE Web_App SHALL NOT place hand-written `<head>`, `<meta>`, `<link rel="canonical">`, or social-tag elements inside page or component markup on the Marketing_Surface.
3. IF metadata is expressed through hand-placed head markup rather than the Metadata_API, THEN THE Web_App SHALL treat that as a rejected pattern per `seo.md` anti-patterns and the change SHALL NOT be merged.
4. THE Web_App SHALL NOT add any sitemap entry, Canonical_URL, Open Graph tag, or other discoverability metadata to an Authenticated_Surface route, per the anti-patterns in `seo.md`.

### Requirement 11: Reserved CSP-Nonce Mechanism for Deferred JSON-LD

**User Story:** As a security owner, I want the structured-data injection path decided in advance and CSP-safe, so that when JSON-LD eventually ships it cannot weaken the Content-Security-Policy.

#### Acceptance Criteria

1. THE Web_App SHALL NOT introduce `'unsafe-inline'` into the Content-Security-Policy `script-src` directive as part of any SEO change, per `security.md` and ADR 0006.
2. THE Web_App SHALL NOT ship JSON-LD structured-data payloads in this spec, per the deferral in ADR 0006 Decision 5 and `seo.md`.
3. WHERE structured data is later injected, THE Web_App SHALL inject it as `<script type="application/ld+json">` carrying a per-request CSP_Nonce wired into the CSP `script-src`, per `security.md` and ADR 0006.
4. WHERE a trivial site-identity tag is warranted, THE Web_App MAY emit `Organization` or `WebSite` structured data only through the CSP_Nonce mechanism of this requirement, following ADR 0006; any richer structured-data type remains deferred.

### Requirement 12: Search Console Verification Without Inline Script

**User Story:** As a site owner, I want to verify domain ownership in Google Search Console, so that I can monitor indexing and search performance for the public pages.

#### Acceptance Criteria

1. THE Web_App SHALL perform Search_Console_Verification using either a DNS TXT record or a Metadata_API verification tag, per `seo.md`.
2. THE Web_App SHALL NOT perform Search_Console_Verification through an inline script or any mechanism that requires relaxing the Content-Security-Policy, per `seo.md` and `security.md`.
3. WHERE a Metadata_API verification tag is used, THE Metadata_Builder SHALL emit it through the Metadata_API rather than hand-placed head markup, consistent with Requirement 10.
4. WHERE any SEO or analytics tag sets a non-essential cookie, THE Web_App SHALL honor the cookie/consent rule in `security.md` → Privacy, and SHALL apply such tags to the Marketing_Surface only.

### Requirement 13: Boundary With Authenticated Non-Indexing (Referenced Dependency)

**User Story:** As an architect, I want this spec to depend on, not duplicate, the authenticated non-indexing guarantee, so that the one indexing policy has a single owner per surface and the two specs cannot drift.

#### Acceptance Criteria

1. THE seo-foundation spec SHALL treat the non-indexing of the Authenticated_Surface as owned by `phase-1-matching` Requirement 15 and SHALL NOT restate or re-implement those acceptance criteria.
2. THE Robots_Generator and THE Sitemap_Generator SHALL remain consistent with `phase-1-matching` Requirement 15: the disallow and exclusion rules for `/api/` and the `(app)` paths SHALL hold even as public-SEO work lands.
3. THE seo-foundation spec SHALL NOT modify Authenticated_Surface routes or `/api/*` responses; any change to those surfaces is out of scope and belongs to `phase-1-matching`.
4. IF a conflict arises between a public-SEO change and the Indexing_Policy non-indexing controls, THEN THE seo-foundation spec SHALL defer to the Indexing_Policy in `seo.md` and ADR 0006, because non-indexing of PII surfaces overrides discoverability.
