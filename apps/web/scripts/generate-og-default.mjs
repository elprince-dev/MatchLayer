/**
 * Reproducible generator for the branded default Open Graph image
 * (`seo-foundation` task 2.1; Req 4.2; design D2).
 *
 * Renders the 1200×630 default social-preview card — the violet→cyan brand
 * gradient (design.md "Signature gradient") with the MatchLayer wordmark and
 * tagline — and writes it to `public/og/og-default.png`. Committing the output
 * as a static asset keeps the per-request cost at zero (the product.md $20/mo
 * ceiling) while the generator keeps the asset on-brand and reproducible: run
 *
 *     pnpm --filter @matchlayer/web og:generate
 *
 * to regenerate after a brand-token change.
 *
 * Implemented with `React.createElement` (no JSX) so it runs under plain Node
 * without a compile step. `next/og`'s `ImageResponse` uses Satori, which
 * supports a focused CSS subset — hence the explicit flex layout and inline
 * styles rather than Tailwind classes.
 */

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { ImageResponse } from "next/og.js";

const WIDTH = 1200;
const HEIGHT = 630;

// Brand tokens (design.md → Color). Kept literal here because this build-time
// script has no access to the runtime CSS custom properties.
const BRAND_VIOLET = "#7C3AED";
const BRAND_CYAN = "#06B6D4";
const INK = "#0A0A0B";

const h = React.createElement;

const element = h(
  "div",
  {
    style: {
      width: "100%",
      height: "100%",
      display: "flex",
      flexDirection: "column",
      alignItems: "flex-start",
      justifyContent: "center",
      padding: "96px",
      backgroundColor: INK,
      backgroundImage: `linear-gradient(135deg, ${BRAND_VIOLET} 0%, ${BRAND_CYAN} 100%)`,
      color: "#FFFFFF",
      fontFamily: "sans-serif",
    },
  },
  h(
    "div",
    {
      style: {
        fontSize: 108,
        fontWeight: 700,
        letterSpacing: "-0.03em",
        lineHeight: 1,
      },
    },
    "MatchLayer",
  ),
  h(
    "div",
    {
      style: {
        marginTop: 32,
        fontSize: 44,
        fontWeight: 500,
        maxWidth: 900,
        opacity: 0.95,
      },
    },
    "See how ATS systems score your resume — transparent keyword & TF-IDF matching.",
  ),
);

const image = new ImageResponse(element, { width: WIDTH, height: HEIGHT });
const buffer = Buffer.from(await image.arrayBuffer());

const here = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(here, "../public/og");
const outFile = path.join(outDir, "og-default.png");

await mkdir(outDir, { recursive: true });
await writeFile(outFile, buffer);

console.log(`Wrote ${outFile} (${buffer.length} bytes, ${WIDTH}x${HEIGHT})`);
