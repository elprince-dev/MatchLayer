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
  SITE_DEFAULT_TITLE,
  SITE_DESCRIPTION,
  SITE_NAME,
  SITE_URL,
} from "./site";

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
 * Build a complete, indexable `Metadata` object for a public marketing page.
 *
 * Emits: a resolved `metadataBase`, title, description, a self-referential
 * canonical URL, Open Graph tags (`og:title`/`description`/`url`/`type`, plus
 * `og:image` when supplied), and a Twitter `summary_large_image` card
 * (Req 7.1). It deliberately sets no `robots` directive: the absence of a
 * `noindex` directive on a public page is what keeps `/` indexable (Req 7.5),
 * while `(app)`/`(auth)` layouts assert `noindex` themselves.
 */
export function buildMarketingMetadata(
  input: MarketingMetadataInput = {},
): Metadata {
  const title = input.title ?? SITE_DEFAULT_TITLE;
  const description = input.description ?? SITE_DESCRIPTION;
  const path = input.path ?? "/";

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
      ...(input.ogImage ? { images: [{ url: input.ogImage }] } : {}),
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      ...(input.ogImage ? { images: [input.ogImage] } : {}),
    },
  };

  return metadata;
}
