import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/seo";

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
 * The `Sitemap` directive (added by the `seo-foundation` spec, Req 5.4) points
 * crawlers at the generated `app/sitemap.ts`, which is itself a strict
 * public-route allowlist (`lib/seo/routes.ts`) — so advertising the sitemap
 * here can never expose a PII route. The `disallow` set stays scoped to the
 * `/api/` surface and the authenticated `(app)` paths; no `Allow` rule for a
 * non-public path is ever emitted (Req 5.2, 5.3, 5.5).
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: ["/api/", "/upload", "/matches", "/library", "/dashboard"],
    },
    sitemap: new URL("/sitemap.xml", SITE_URL).toString(),
  };
}
