/**
 * The single allowlist of PUBLIC, indexable marketing routes (Req 6.2, 6.5,
 * 2.4, 5.5; design → Public route table).
 *
 * This is the **one authority** the sitemap emits from and that tests assert
 * against, replacing the literal that previously lived inside `app/sitemap.ts`.
 * Centralizing it here is what lets the sitemap, each page's canonical URL, and
 * the robots policy agree on exactly one route set (Req 2.4, 6.5).
 *
 * DEFAULT-DENY (the rule that overrides convenience): a path appears here ONLY
 * after it has been classified **Public** per `seo.md` and ADR 0006. MatchLayer
 * renders Restricted PII (resume text, job descriptions, match results) on
 * authenticated surfaces, so listing an `(app)` or `/api/` path here would be a
 * PII-exfiltration vector, not merely an SEO mistake. If you cannot confidently
 * classify a route Public, it does not belong in this array.
 *
 * This array must therefore NEVER contain:
 *   - `(app)` authenticated routes: `/upload`, `/matches`, `/matches/[id]`,
 *     `/library`, `/dashboard`, `/settings`;
 *   - the `/api/` JSON surface;
 *   - the `(auth)` entry pages `/login` and `/register`.
 *
 * `/login` and `/register` are deliberately EXCLUDED (design D1, Option B).
 * Although `seo.md`'s route table once listed them as indexable, the project
 * keeps them `noindex` and out of the sitemap: they front the authentication
 * flow, carry no marketing value, and indexing sign-in/sign-up pages is a known
 * anti-pattern. That decision was made and tested by the `frontend-redesign`
 * spec; this spec preserves it. `proxy.ts` continues to stamp them
 * `X-Robots-Tag: noindex, nofollow` and the `(auth)` layout keeps its
 * `robots: { index: false, follow: false }` export. See `seo.md` → route
 * classification (the "Public-but-noindex" note) and ADR 0007.
 *
 * `/pricing` is intentionally absent — the product is pre-monetization until
 * Phase 7, so there is nothing to price; it joins the list when that page is
 * built (design → Scope note).
 */
export const PUBLIC_ROUTES = ["/", "/about", "/privacy", "/terms"] as const;

/** A root-relative path that has been classified Public and is indexable. */
export type PublicRoute = (typeof PUBLIC_ROUTES)[number];
