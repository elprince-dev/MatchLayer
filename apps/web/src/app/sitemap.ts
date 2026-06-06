import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/seo";

/**
 * Generated sitemap for the MatchLayer web app (Req 7.5, 8.9, 8.10, 21.7;
 * `seo.md`; ADR 0006).
 *
 * THE ONE RULE THIS FILE ENFORCES: the sitemap lists **public routes only**.
 * MatchLayer renders Restricted PII (resume text, job descriptions, match
 * results) on authenticated surfaces, so listing those would be a
 * PII-exfiltration vector, not merely an SEO mistake. This sitemap therefore
 * uses an explicit **allowlist** (default-deny): only routes named below are
 * ever emitted. It must NEVER list:
 *   - `(app)` authenticated routes: `/upload`, `/matches`, `/matches/[id]`,
 *     `/library`, `/dashboard`;
 *   - `(auth)` routes: `/login`, `/register` (publicly reachable but kept out
 *     of the index per Req 8.9–8.10);
 *   - the `/api/` JSON surface.
 *
 * Pairing with `app/robots.ts`: robots.ts *disallows* `/api/` and the
 * authenticated app paths; this file *omits* them. The two controls are
 * independent layers of the same default-deny posture — neither relies on the
 * other. The landing page (`/`) is the only indexable surface among the MVP
 * screens (Req 7.5, 8.10).
 *
 * Adding a route here is a deliberate act: a route is added ONLY after it has
 * been classified Public per `seo.md`. If you cannot confidently classify a
 * route Public, it does not belong in this list.
 *
 * `lastModified` uses build time, which is sufficient for the single static
 * marketing page; per-page timestamps can be introduced when the
 * `seo-foundation` spec adds more public pages.
 */

/**
 * Allowlist of PUBLIC, indexable route paths (root-relative). Default-deny:
 * anything not in this array is excluded from the sitemap. Today this is just
 * the landing page; `/pricing`, `/about`, `/privacy`, `/terms` join it as the
 * `seo-foundation` spec builds those public pages.
 */
const PUBLIC_ROUTES = ["/"] as const;

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();

  return PUBLIC_ROUTES.map((path) => ({
    url: new URL(path, SITE_URL).toString(),
    lastModified,
  }));
}
