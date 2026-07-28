/**
 * Site-identity constants for the MatchLayer public marketing surface
 * (`seo.md`; ADR 0006; design Section 6.2).
 *
 * These values seed the Next.js Metadata API for the **Public**, indexable
 * `(marketing)` route group only. They are deliberately NOT used by any
 * `(app)`/`(auth)` route: authenticated, PII-bearing surfaces carry no
 * canonical/Open Graph chrome (the `(app)` layout exports
 * `robots: { index: false, follow: false }`), and they are excluded from the
 * sitemap entirely. SEO is for public pages; PII pages are never indexed.
 *
 * Why a hard-coded constant rather than an env var: the canonical origin is a
 * stable property of the product (`matchlayer.net`, see `product.md`). Reading
 * it from a public (`NEXT_PUBLIC_`-prefixed) env var would add an entry to the
 * committed `.env.example` contract (enforced by `tools/check_env_drift.py`) for a value
 * that does not vary per environment in any way the marketing metadata cares
 * about. When the `seo-foundation` spec needs per-environment canonical hosts
 * (e.g. a staging origin that must stay `noindex`), this can be promoted to a
 * public env var at that point.
 */

/**
 * Canonical production origin. Used as the `metadataBase` for resolving
 * relative canonical/Open Graph URLs and as the base for `app/sitemap.ts`.
 * No trailing slash so `new URL(path, SITE_URL)` composes cleanly.
 */
export const SITE_URL = "https://matchlayer.net";

/** Brand name used in `<title>` and `og:site_name`. */
export const SITE_NAME = "MatchLayer";

/**
 * Default marketing meta description (≤ 155 chars per `seo.md` / Req 7.1).
 *
 * Honesty constraint (Req 5.1): scoring is semantic (sentence embeddings,
 * shipped in phase-2-nlp-embeddings) plus keyword coverage. This copy never
 * describes it as AI or LLM powered — that remains unshipped roadmap.
 */
export const SITE_DESCRIPTION =
  "See how real ATS systems read your resume. MatchLayer scores it against any job description using semantic similarity and keyword matching.";

/**
 * Default marketing `<title>` (≤ 60 chars per `seo.md` / Req 7.1).
 *
 * Acts as the fallback/default for the `(marketing)` group; individual public
 * pages (built in task 8.7) may override it via their own `metadata` export.
 */
export const SITE_DEFAULT_TITLE =
  "MatchLayer — See how ATS systems score your resume";

/**
 * The branded default Open Graph / Twitter-card image (Req 4.1, 4.2, 4.5;
 * design D2). This is the single source for the default social preview: the
 * `buildMarketingMetadata` builder applies it to any public page that does not
 * supply a page-specific `ogImage` override.
 *
 * The asset is a committed static `1200×630` PNG at
 * `apps/web/public/og/og-default.png`, generated reproducibly by
 * `apps/web/scripts/generate-og-default.tsx` (violet→cyan brand gradient +
 * Geist type per `design.md`). Serving it statically keeps the per-request
 * cost at zero (the `product.md` $20/mo ceiling) and gives fixed dimensions so
 * `og:image:width`/`og:image:height` can be emitted (Req 4.5), which crawlers
 * and social scrapers use to lay out the preview without a round-trip.
 *
 * `path` is root-relative; the builder resolves it to an absolute URL against
 * {@link SITE_URL} via `metadataBase` (Req 4.4).
 */
export const OG_DEFAULT = {
  path: "/og/og-default.png",
  width: 1200,
  height: 630,
  alt: "MatchLayer — transparent ATS resume scoring",
} as const;

/**
 * Google Search Console site-ownership verification token (Req 12.1, 12.3;
 * design D3).
 *
 * Left `undefined` by default: the **primary** verification path is a DNS TXT
 * record at the registrar (no code, no Content-Security-Policy impact, honors
 * Req 12.2 trivially — see the release runbook). This constant is the coded
 * **fallback**: when set, `buildMarketingMetadata` emits a
 * `metadata.verification.google` tag through the Next.js Metadata API on the
 * home page only — never an inline script (Req 12.2) and never a cookie
 * (Req 12.4). It stays a constant rather than a `NEXT_PUBLIC_*` env var for the
 * same reason as {@link SITE_URL} (design D5); promote it if a per-environment
 * token is ever needed.
 */
export const SEARCH_CONSOLE_VERIFICATION: string | undefined = undefined;
