/**
 * Unit tests for the marketing Metadata_Builder (`seo-foundation` task 1.5;
 * Req 1.2, 1.3, 1.6, 2.1, 2.2, 3.1–3.4, 4.1, 4.3, 4.4, 4.5, 9.4, 12.3).
 *
 * The builder is the single source of public-page metadata (`seo.md`,
 * `conventions.md`). These tests pin the length budgets, the self-referential
 * canonical, the OG/Twitter shape, and the branded-default-OG-image behavior.
 * Pure module functions (no DOM) → default Vitest `node` environment.
 */

import { describe, expect, it } from "vitest";

import { buildMarketingMetadata } from "./metadata";
import {
  OG_DEFAULT,
  SITE_DEFAULT_TITLE,
  SITE_DESCRIPTION,
  SITE_URL,
} from "./site";

describe("buildMarketingMetadata — defaults and fallbacks", () => {
  it("falls back to the site title/description when none supplied (Req 1.6)", () => {
    const meta = buildMarketingMetadata({ path: "/" });
    expect(meta.title).toBe(SITE_DEFAULT_TITLE);
    expect(meta.description).toBe(SITE_DESCRIPTION);
  });

  it("defaults the canonical path to `/` when omitted", () => {
    const meta = buildMarketingMetadata();
    expect(meta.alternates?.canonical).toBe("/");
  });
});

describe("buildMarketingMetadata — canonical (Req 2.1, 2.2)", () => {
  it("emits a self-referential canonical for the given path", () => {
    const meta = buildMarketingMetadata({ path: "/about" });
    expect(meta.alternates?.canonical).toBe("/about");
  });

  it("sets metadataBase to the configured site origin so relative URLs resolve absolutely", () => {
    const meta = buildMarketingMetadata({ path: "/privacy" });
    expect(meta.metadataBase?.toString()).toBe(new URL(SITE_URL).toString());
  });
});

describe("buildMarketingMetadata — length budgets (Req 1.2, 1.3, 9.4)", () => {
  it("throws when the title exceeds 60 characters", () => {
    expect(() =>
      buildMarketingMetadata({ title: "x".repeat(61), path: "/" }),
    ).toThrow(/title/i);
  });

  it("throws when the description exceeds 155 characters", () => {
    expect(() =>
      buildMarketingMetadata({ description: "x".repeat(156), path: "/" }),
    ).toThrow(/description/i);
  });

  it("accepts values exactly at the budget boundary", () => {
    expect(() =>
      buildMarketingMetadata({
        title: "x".repeat(60),
        description: "y".repeat(155),
        path: "/",
      }),
    ).not.toThrow();
  });
});

describe("buildMarketingMetadata — Open Graph & Twitter (Req 3.1–3.4)", () => {
  const meta = buildMarketingMetadata({
    path: "/about",
    title: "About MatchLayer",
    description: "About the product.",
  });

  // Next's `OpenGraph`/`Twitter` types are wide unions; narrow to a plain
  // record for assertion (the builder only ever produces the `website`/
  // `summary_large_image` shapes).
  const og = meta.openGraph as Record<string, unknown>;
  const tw = meta.twitter as Record<string, unknown>;

  it("emits og:type website and reuses the page title/description (Req 3.1, 3.4)", () => {
    expect(og.type).toBe("website");
    expect(og.title).toBe("About MatchLayer");
    expect(og.description).toBe("About the product.");
  });

  it("sets og:url equal to the canonical path (Req 3.2)", () => {
    expect(og.url).toBe("/about");
  });

  it("emits a summary_large_image Twitter card reusing title/description (Req 3.3, 3.4)", () => {
    expect(tw.card).toBe("summary_large_image");
    expect(tw.title).toBe("About MatchLayer");
    expect(tw.description).toBe("About the product.");
  });
});

describe("buildMarketingMetadata — default OG image (Req 4.1, 4.4, 4.5)", () => {
  const meta = buildMarketingMetadata({ path: "/" });
  const images = meta.openGraph?.images;
  const image = Array.isArray(images) ? images[0] : images;

  it("applies the branded default OG image when no override is supplied", () => {
    expect(image).toMatchObject({
      url: OG_DEFAULT.path,
      width: OG_DEFAULT.width,
      height: OG_DEFAULT.height,
      alt: OG_DEFAULT.alt,
    });
  });

  it("also sets the default image on the Twitter card", () => {
    const twImages = meta.twitter?.images;
    const twImage = Array.isArray(twImages) ? twImages[0] : twImages;
    expect(twImage).toBe(OG_DEFAULT.path);
  });
});

describe("buildMarketingMetadata — per-page OG override (Req 4.3)", () => {
  const meta = buildMarketingMetadata({
    path: "/pricing",
    ogImage: "/og/pricing.png",
  });
  const images = meta.openGraph?.images;
  const image = Array.isArray(images) ? images[0] : images;

  it("uses the supplied ogImage in place of the default", () => {
    expect(image).toEqual({ url: "/og/pricing.png" });
  });
});

describe("buildMarketingMetadata — indexability & verification", () => {
  it("sets no robots directive, keeping public pages indexable (Req 7.5)", () => {
    const meta = buildMarketingMetadata({ path: "/" });
    expect(meta.robots).toBeUndefined();
  });

  it("emits no verification tag by default (DNS-TXT is the primary path, Req 12.1)", () => {
    // SEARCH_CONSOLE_VERIFICATION is unset by default, so no google tag.
    const meta = buildMarketingMetadata({ path: "/" });
    expect(meta.verification).toBeUndefined();
  });
});
