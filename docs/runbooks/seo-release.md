# Runbook — SEO release checks (`seo-foundation`)

Operational steps for the public marketing surface that cannot be fully automated in CI: the manual Core Web Vitals pass and Google Search Console verification. Policy lives in `.kiro/steering/seo.md`; the design is in `.kiro/specs/seo-foundation/design.md`. See ADR 0006 (indexing split) and ADR 0007 (auth pages are Public-but-noindex).

## Scope reminder

Only the **indexable public routes** get SEO chrome: `/`, `/about`, `/privacy`, `/terms` (the `PUBLIC_ROUTES` allowlist in `apps/web/src/lib/seo/routes.ts`). `/login` and `/register` are **Public-but-noindex** — reachable, but kept out of the index and sitemap (ADR 0007). Never add SEO chrome, sitemap entries, or canonicals to `(app)` / `/api/` routes.

## 1. Core Web Vitals (Req 8.1–8.3) — manual Lighthouse pass

The structural guarantees (Server Components first, `next/font` for Geist, `next/image` with explicit dimensions, the `@next/next/no-img-element` lint guard) are enforced in code and CI. The numeric budgets are verified manually before a marketing release:

1. Build and start the app: `pnpm --filter @matchlayer/web build && pnpm --filter @matchlayer/web start`.
2. In Chrome DevTools → Lighthouse, run a **mobile** audit against each public route: `/`, `/about`, `/privacy`, `/terms`.
3. Confirm the budgets from `seo.md`:
   - Largest Contentful Paint (LCP) < **2.5 s**
   - Cumulative Layout Shift (CLS) < **0.1**
   - Interaction to Next Paint (INP) < **200 ms**
4. If a page misses a budget, the usual culprits are an unsized image (add `next/image` width/height), a heavy client island (push work to a Server Component), or a font swap (ensure `next/font`). Fix and re-run.

> An optional Lighthouse CI job may report these numbers on PRs as a **warning-only** signal. Lab CWV is environment-noisy, so it never gates the build; this manual pass is authoritative.

## 2. Google Search Console verification (Req 12) — DNS TXT (primary)

Verification uses a **DNS TXT record** — no code, no Content-Security-Policy impact, no cookies.

1. In Google Search Console, add the `matchlayer.net` property and choose **Domain** (DNS) verification.
2. Add the provided `google-site-verification=...` value as a TXT record at the `matchlayer.net` registrar/zone.
3. Wait for propagation, then click **Verify** in Search Console.
4. Submit the sitemap: `https://matchlayer.net/sitemap.xml`.

### Coded fallback (only if DNS is impractical)

A Metadata-API verification tag is wired but disabled by default:

1. Set `SEARCH_CONSOLE_VERIFICATION` in `apps/web/src/lib/seo/site.ts` to the token from the **URL-prefix → HTML tag** method.
2. `buildMarketingMetadata` emits it as `metadata.verification.google` on the **home page only**, via the Metadata API (never an inline script — Req 12.2, no CSP change).
3. Redeploy, verify, then submit the sitemap as above.

Never use an inline `<script>` verification snippet — it would require relaxing the CSP (`security.md`, ADR 0006).

## 3. Post-deploy smoke

- `curl -s https://matchlayer.net/robots.txt` — shows the `Disallow` rules for `/api/` and the `(app)` paths **and** a `Sitemap: https://matchlayer.net/sitemap.xml` line.
- `curl -s https://matchlayer.net/sitemap.xml` — lists exactly `/`, `/about`, `/privacy`, `/terms` as absolute URLs; no `(app)`/`/api/`/`/login`/`/register` entries.
- View-source each public page: unique `<title>` and `<meta name="description">`, a self-referential `<link rel="canonical">`, Open Graph + Twitter tags, and the `og:image` resolving to `https://matchlayer.net/og/og-default.png`.
- `curl -sI https://matchlayer.net/login | grep -i x-robots-tag` — confirms `noindex, nofollow` still stamped on the auth pages.

## 4. Regenerating the default OG image

The branded default social image is a committed static asset at `apps/web/public/og/og-default.png` (1200×630). To regenerate after a brand-token change:

```bash
pnpm --filter @matchlayer/web og:generate
```

Commit the updated PNG. Per-page overrides are supplied via the `ogImage` argument to `buildMarketingMetadata`.
