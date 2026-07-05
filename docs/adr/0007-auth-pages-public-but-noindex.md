# 0007 — Auth entry pages are Public-but-noindex

**Status:** Accepted
**Date:** 2026-07-05
**Applies to:** Phase 1+ (indexing policy); realized by the `seo-foundation` spec

## Context

ADR 0006 established the public/authenticated indexing split and named the indexable Public surface as the marketing pages (`/`, `/pricing`, `/about`, privacy/ToS). It did **not** classify the authentication entry pages `/login` and `/register`.

Two downstream documents then diverged:

- The `seo.md` steering route table listed `/login` and `/register` under **Public — "Full SEO, indexable, in sitemap."**
- The `seo-foundation` requirements folded `/login` and `/register` into the `Marketing_Surface` term, implying full SEO including sitemap inclusion.

Meanwhile the shipped code (from the `frontend-redesign` spec) did the opposite and was tested that way: the `(auth)` route-group layout exports `robots: { index: false, follow: false }`, `src/proxy.ts` stamps `X-Robots-Tag: noindex, nofollow` on `/login` and `/register`, and `app/sitemap.ts` excludes them. `apps/web/tests/non-indexing.test.ts` asserts all of this across both the Metadata-API and response-header layers.

Implementing the `seo-foundation` spec forced the contradiction to a decision: make the auth pages indexable (overturning the tested `frontend-redesign` decision and requiring an ADR reversal) versus keep them `noindex`.

## Decision

**`/login` and `/register` are classified `Public-but-noindex`.**

- They are publicly reachable and receive proper title/description/canonical metadata via the Next.js Metadata API (hygiene, consistency).
- They keep `robots: { index: false, follow: false }` (via the `(auth)` layout) and `X-Robots-Tag: noindex, nofollow` (via `proxy.ts`).
- They are **excluded** from `app/sitemap.ts` and from the `PUBLIC_ROUTES` allowlist in `apps/web/src/lib/seo/routes.ts`.
- `seo.md`'s route table and the `seo-foundation` requirements (`Marketing_Surface` glossary, Req 6.2) are amended to match.

This does not overturn ADR 0006; it fills the gap ADR 0006 left and corrects the `seo.md` table to agree with ADR 0006's scope and the shipped implementation.

## Rationale

- **Auth pages have no marketing value.** Sign-in/sign-up pages do not rank for meaningful queries; indexing them is a widely recognized SEO anti-pattern that can produce thin, duplicate, or confusing search results.
- **Preserves a tested, deliberate decision.** `frontend-redesign` chose `noindex` for these pages with documented rationale and a dedicated test matrix. Reversing it would delete that coverage and re-open a settled question for no benefit.
- **Consistent with ADR 0006 as written.** ADR 0006 only ever named the marketing pages as the indexable Public surface; the auth-page "indexable" entry in `seo.md` was an over-broad extrapolation, not an ADR decision.
- **Metadata hygiene is still delivered.** The pages get correct titles/descriptions/canonicals, so the SEO baseline is complete without making them crawlable.

## Consequences

**Positive**

- One coherent, tested policy across `seo.md`, the `seo-foundation` spec, and the code.
- No churn to the `non-indexing.test.ts` matrix beyond the robots-`Sitemap`-directive update this spec adds independently (Req 5.4).
- Introduces a reusable `Public-but-noindex` class for future reachable-but-non-indexable pages (e.g., `/logout`, error pages).

**Negative**

- A minor conceptual addition: three route classes instead of two (Public, Public-but-noindex, Authenticated/API).
- Requirements text for an approved spec had to be amended (annotated inline, not silently changed).

## Alternatives considered

- **Make `/login` and `/register` indexable (Option A):** rejected. Overturns a tested decision, deletes established non-indexing coverage, requires an ADR reversal, and indexes low-value auth pages against SEO best practice.
- **Leave the contradiction unresolved:** rejected. `seo.md`, the requirements, and the code disagreed; the spec could not be implemented coherently without picking one.
