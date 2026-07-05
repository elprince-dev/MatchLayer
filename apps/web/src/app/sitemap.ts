import type { MetadataRoute } from "next";

import { PUBLIC_ROUTES, SITE_URL } from "@/lib/seo";

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
 * `lastModified` uses build time, which is sufficient for the mostly-static
 * marketing pages; per-page timestamps can be introduced later if a public
 * page starts changing on its own cadence.
 *
 * The allowlist itself lives in `@/lib/seo` (`lib/seo/routes.ts`) as the single
 * `PUBLIC_ROUTES` source shared by this sitemap, each page's canonical URL, and
 * the tests — so the three can never drift (Req 2.4, 6.5). This file only maps
 * that allowlist to absolute-URL sitemap entries.
 */

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();

  return PUBLIC_ROUTES.map((path) => ({
    url: new URL(path, SITE_URL).toString(),
    lastModified,
  }));
}
