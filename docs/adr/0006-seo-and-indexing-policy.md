# 0006 — SEO strategy and the public/authenticated indexing split

**Status:** Accepted
**Date:** 2026-05-29
**Applies to:** Phase 1+ (policy), public marketing SEO realized as marketing pages are built

## Context

MatchLayer collects Restricted PII (resume bytes, extracted resume text, job-description text) and renders it on authenticated surfaces — the upload page, the per-match results page (`/matches/[id]`), and the resume/match library. We also want the product to be discoverable: the marketing surface (`/`, `/pricing`, `/about`, privacy/ToS) should rank in search engines.

A naive "add SEO everywhere" instruction is dangerous here. SEO makes pages crawlable and indexable; PII pages must be the opposite. Treating SEO as one undifferentiated goal risks a search engine indexing a results page that contains resume-derived content — a PII-exfiltration vector in our `security.md` threat model. The two goals point in opposite directions and must be governed by one explicit policy rather than left implicit.

Separately, rich structured data (JSON-LD) is normally injected as an inline `<script type="application/ld+json">`. That collides with the strict `Content-Security-Policy` baseline in `security.md`, which disallows arbitrary inline script. A decision is needed on how to reconcile the two before any structured data ships.

## Decision

1. **Every route is explicitly classified as Public or Authenticated.** There is no unclassified default.
2. **Authenticated surfaces are never indexed.** All routes in the Next.js `(app)` route group and every `/api/v1/*` response set `noindex, nofollow` (via the Next.js Metadata API `robots` field on the `(app)` layout and an `X-Robots-Tag: noindex, nofollow` response header), and `robots.txt` disallows the app and API paths. PII-bearing routes are excluded from `sitemap.xml`.
3. **Public marketing surfaces carry full SEO.** Canonical URLs, title/description metadata, Open Graph + Twitter Card tags, semantic HTML landmarks, `app/sitemap.ts`, `app/robots.ts`, and Core Web Vitals budgets — owned by a dedicated `seo-foundation` spec and realized as marketing pages are built.
4. **SEO source of truth is the Next.js Metadata API.** Metadata is defined in `generateMetadata`/`metadata` exports and shared SEO helpers under `apps/web/src/lib/seo/`, not hand-placed `<head>` tags scattered in components.
5. **JSON-LD is allowed only via a CSP nonce.** When structured data ships, it uses a per-request nonce wired into the CSP `script-src`. **JSON-LD is deferred in Phase 1** (no marketing pages rich enough to warrant it yet); the nonce strategy is the adopted mechanism so it is not re-litigated when that work lands.

## Rationale

- **Closes a real privacy gap.** The original `phase-1-matching` requirements never stated that PII pages must not be indexed. Making non-indexing an explicit, testable requirement removes an unstated assumption.
- **Separation of concerns.** Public SEO is a marketing/discoverability concern with its own lifecycle; bolting it onto an authenticated, PII-heavy feature spec mixes incompatible goals. A dedicated spec keeps each surface's intent clear.
- **Policy before code.** The web app is not yet scaffolded. Setting the indexing classification now means every future route inherits a default-deny indexing posture rather than retrofitting it.
- **Security stays authoritative.** Deferring JSON-LD while pre-deciding the nonce mechanism avoids taking on a CSP change for code that does not exist yet, without leaving the decision open.

## Consequences

**Positive**

- PII pages cannot be silently indexed; non-indexing is verifiable in tests (header + meta + robots.txt).
- Marketing SEO can be built confidently without auditing every page for accidental PII exposure.
- One documented place (`seo.md`) defines the rule for every new route.

**Negative**

- Every new route must be classified Public or Authenticated before merge — a small added review step.
- A future "shareable public match result" feature (Phase 7+) would need its own ADR, because it deliberately crosses the PII/indexing boundary and must strip PII first.
- The CSP nonce adds minor complexity to the rendering pipeline when JSON-LD eventually ships.

## Alternatives considered

- **Add SEO uniformly to all pages (including matching):** rejected. Directly enables PII indexing; contradicts `security.md`.
- **Rely on auth alone to keep PII pages out of search:** rejected. Auth gates access, not crawling of shared/leaked links; defense-in-depth wants explicit `noindex` regardless.
- **Fold public SEO into `phase-1-matching`:** rejected. Mixes authenticated PII concerns with public discoverability; muddies both specs.
- **Ship JSON-LD now with `'unsafe-inline'` or a hash allowance:** rejected. Weakens CSP for no Phase 1 benefit; nonce is the durable answer.
