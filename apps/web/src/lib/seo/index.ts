/**
 * Public barrel for the marketing SEO helpers (`apps/web/src/lib/seo/`).
 *
 * Shared metadata builders for the **Public** `(marketing)` route group, per
 * `seo.md` ("Shared SEO helpers live in `apps/web/src/lib/seo/`") and design
 * Section 6.2. Import from `@/lib/seo` rather than the individual modules so
 * the helper surface stays stable as it grows (OG-image helpers land with the
 * `seo-foundation` spec).
 *
 * These helpers are for public pages only — never import them into an
 * `(app)`/`(auth)` route (ADR 0006).
 */

export {
  buildMarketingMetadata,
  type MarketingMetadataInput,
} from "./metadata";
export {
  SITE_DEFAULT_TITLE,
  SITE_DESCRIPTION,
  SITE_NAME,
  SITE_URL,
} from "./site";
