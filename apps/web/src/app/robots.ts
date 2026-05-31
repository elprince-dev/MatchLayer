import type { MetadataRoute } from "next";

/**
 * Generated robots rules for the MatchLayer web app (Requirement 15.4;
 * `seo.md`; ADR 0006).
 *
 * This is a privacy control, not just an SEO one. The authenticated `(app)`
 * route group renders Restricted PII — resume text, job descriptions, and
 * match results — and the `/api/` surface returns it as JSON. None of those
 * paths may ever be crawled or indexed, so we disallow them here as defense in
 * depth alongside the `(app)` layout's `noindex, nofollow` metadata
 * (Requirement 15.2) and the API's `X-Robots-Tag` response header
 * (Requirement 15.3). Authentication gating alone is not treated as sufficient
 * (Requirement 15.7).
 *
 * Per `seo.md` the eventual full public-page robots/sitemap is owned by the
 * `seo-foundation` spec; this route only encodes the disallow rules for the
 * PII-bearing authenticated and API paths. No `sitemap`/`host` entry is added
 * here so that no PII route is ever exposed via a sitemap reference.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: ["/api/", "/upload", "/matches", "/library", "/dashboard"],
    },
  };
}
