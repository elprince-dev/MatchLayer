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
 * Honesty constraint (Req 5.1): the current scoring is keyword + TF-IDF based.
 * This copy never describes it as semantic, embeddings, AI, or LLM powered.
 */
export const SITE_DESCRIPTION =
  "See how real ATS systems read your resume. MatchLayer scores it against any job description using transparent keyword and TF-IDF matching.";

/**
 * Default marketing `<title>` (≤ 60 chars per `seo.md` / Req 7.1).
 *
 * Acts as the fallback/default for the `(marketing)` group; individual public
 * pages (built in task 8.7) may override it via their own `metadata` export.
 */
export const SITE_DEFAULT_TITLE =
  "MatchLayer — See how ATS systems score your resume";
