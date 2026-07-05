/**
 * Shared metadata builders for the MatchLayer **Public** marketing surface
 * (Req 7.1, 7.5; `seo.md`; design Section 6.2; ADR 0006).
 *
 * The Next.js **Metadata API** is the single source of page metadata for the
 * project (`conventions.md`, `seo.md`). No component hand-places `<head>` or
 * `<meta>` tags. These builders centralize the title / description / canonical
 * / Open Graph / Twitter-card shape so every public page is consistent and
 * each field stays within the `seo.md` length budgets.
 *
 * SCOPE GUARD: these helpers exist for `(marketing)` routes ONLY. They must
 * never be imported into an `(app)` or `(auth)` route — doing so would attach
 * canonical/OG discoverability chrome to a PII-bearing page, which `seo.md`
 * and ADR 0006 forbid. Authenticated routes keep
 * `robots: { index: false, follow: false }` and add no SEO metadata.
 */

import type { Metadata } from "next";

import {
  OG_DEFAULT,
  SEARCH_CONSOLE_VERIFICATION,
  SITE_DEFAULT_TITLE,
  SITE_DESCRIPTION,
  SITE_NAME,
  SITE_URL,
} from "./site";

/** `seo.md` / Req 1.2 — a marketing `<title>` must be at most 60 characters. */
const TITLE_MAX_LENGTH = 60;
/** `seo.md` / Req 1.3 — a meta description must be at most 155 characters. */
const DESCRIPTION_MAX_LENGTH = 155;

/**
 * Inputs for a single public page's metadata. Every field is optional; the
 * builder falls back to the site defaults so a page can opt in to just a
 * canonical path and inherit sensible title/description.
 */
export interface MarketingMetadataInput {
  /**
   * Page `<title>`. Should be ≤ 60 characters (Req 7.1). Defaults to the
   * site-wide title when omitted.
   */
  title?: string;
  /**
   * Meta description. Should be ≤ 155 characters (Req 7.1). Defaults to the
   * site-wide description when omitted.
   */
  description?: string;
  /**
   * Self-referential canonical path, root-relative (e.g. `"/"`, `"/pricing"`).
   * Resolved against {@link SITE_URL} via `metadataBase`. Defaults to `"/"`.
   */
  path?: string;
  /**
   * Open Graph image path or absolute URL. Resolved against `metadataBase`
   * when relative. Optional until a branded OG asset is produced by the
   * `seo-foundation` spec; when absent, no `og:image` is emitted.
   */
  ogImage?: string;
}

/**
 * Assert a resolved title/description stays within the `seo.md` length budgets
 * (Req 1.2, 1.3, 9.4). Throwing here is deliberate: metadata is evaluated
 * during `next build` and by the Vitest suite, so an over-long value fails the
 * build/test loudly rather than shipping a truncated snippet to search results.
 * The offending value is safe to echo (marketing copy, not a secret).
 */
function assertLengthBudgets(title: string, description: string): void {
  if (title.length > TITLE_MAX_LENGTH) {
    throw new Error(
      `Marketing <title> is ${title.length} chars; the limit is ${TITLE_MAX_LENGTH} (seo.md, Req 1.2): ${JSON.stringify(title)}`,
    );
  }
  if (description.length > DESCRIPTION_MAX_LENGTH) {
    throw new Error(
      `Marketing meta description is ${description.length} chars; the limit is ${DESCRIPTION_MAX_LENGTH} (seo.md, Req 1.3): ${JSON.stringify(description)}`,
    );
  }
}

/**
 * Build a complete, indexable `Metadata` object for a public marketing page.
 *
 * Emits: a resolved `metadataBase`; title and description (falling back to the
 * site defaults, Req 1.6); a self-referential canonical URL (Req 2.1, 2.2);
 * Open Graph tags (`og:title`/`description`/`url`/`type` + `og:image` with
 * explicit width/height/alt, Req 3.1, 4.1, 4.4, 4.5); and a Twitter
 * `summary_large_image` card (Req 3.3). `og:url` equals the canonical
 * (Req 3.2) and the social title/description reuse the page's own (Req 3.4).
 *
 * The default branded OG image ({@link OG_DEFAULT}) is applied whenever the
 * page supplies no `ogImage` override (Req 4.1, 4.3), so no public page ever
 * ships without a preview. Title/description length budgets are enforced
 * (Req 9.4) before the object is returned.
 *
 * Search Console verification (Req 12.3): when {@link SEARCH_CONSOLE_VERIFICATION}
 * is set, the `google` verification tag is attached on the **home page only**
 * (`path === "/"`), through the Metadata API — never inline script (Req 12.2).
 *
 * It deliberately sets no `robots` directive: the absence of a `noindex`
 * directive on a public page is what keeps it indexable (Req 7.5), while
 * `(app)`/`(auth)` layouts assert `noindex` themselves.
 */
export function buildMarketingMetadata(
  input: MarketingMetadataInput = {},
): Metadata {
  const title = input.title ?? SITE_DEFAULT_TITLE;
  const description = input.description ?? SITE_DESCRIPTION;
  const path = input.path ?? "/";

  assertLengthBudgets(title, description);

  // Per-page override wins; otherwise apply the branded default (Req 4.1, 4.3).
  // The absolute URL is resolved by `metadataBase`; width/height/alt come from
  // the shared OG_DEFAULT so `og:image:width`/`height` are emitted (Req 4.5).
  const ogImage = input.ogImage
    ? { url: input.ogImage }
    : {
        url: OG_DEFAULT.path,
        width: OG_DEFAULT.width,
        height: OG_DEFAULT.height,
        alt: OG_DEFAULT.alt,
      };
  const twitterImage = input.ogImage ?? OG_DEFAULT.path;

  const metadata: Metadata = {
    metadataBase: new URL(SITE_URL),
    title,
    description,
    alternates: {
      canonical: path,
    },
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      title,
      description,
      url: path,
      images: [ogImage],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [twitterImage],
    },
  };

  // Home-page-only Search Console verification tag (Req 12.3; design D3).
  if (path === "/" && SEARCH_CONSOLE_VERIFICATION) {
    metadata.verification = { google: SEARCH_CONSOLE_VERIFICATION };
  }

  return metadata;
}
