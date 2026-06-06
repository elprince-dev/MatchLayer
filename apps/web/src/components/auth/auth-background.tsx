"use client";

import { motion } from "framer-motion";
import * as React from "react";

import { useMotionSafeProps } from "@/components/motion-safe";

/**
 * Decorative auth-shell background (design Section 8.3, Req 8.5–8.7).
 *
 * Renders the "subtle gradient-mesh / noise" layer behind the centered
 * `AuthCard`. Split out of `(auth)/layout.tsx` as its own `'use client'`
 * island so the layout itself can be a Server Component that exports
 * `robots` metadata (Next.js forbids a `metadata` export from a
 * `"use client"` module). The layout owns the noindex contract; this
 * component owns the visual texture.
 *
 * Two stacked, purely decorative layers, both `aria-hidden`,
 * `pointer-events-none`, and at NEGATIVE z so they sit strictly behind the
 * auth content — they can **never overlay or intercept the card** (Req 8.6):
 *
 *   1. **Gradient mesh** (`-z-20`, static): two faint radial gradients in the
 *      brand violet / cyan, anchored to opposite corners. Drawn with the
 *      sanctioned token-driven gradient-stop pattern — `rgb(var(--color-brand)
 *      / <low-alpha>)` reads the same theme tokens defined in `globals.css`,
 *      so the mesh re-tints automatically in dark vs light and stays AA-safe
 *      (the auth surface is "restrained" per `design.md`; the stops sit at
 *      ≤7% opacity so foreground text contrast is unaffected). It is static
 *      by design — a fixed gradient is fine under `prefers-reduced-motion`.
 *
 *   2. **Fractal-noise overlay** (`-z-10`, animated): a grayscale
 *      `feTurbulence` texture encoded as a data-URL `background-image`, with a
 *      slow ~12s opacity "breath". The breath is routed through
 *      `useMotionSafeProps`, so under `prefers-reduced-motion: reduce` the
 *      `animate` collapses to `initial` and the transition duration zeroes —
 *      leaving a completely static texture, no animation frames (Req 8.7).
 *
 * Because the only motion lives in the noise layer and is gated by the shared
 * reduced-motion hook, the entire background is static for users who request
 * reduced motion, while the gradient mesh + static noise remain as quiet
 * texture.
 */

/**
 * Inline SVG turbulence pattern, encoded as a data URL for use as a
 * `background-image`. `feTurbulence type="fractalNoise"` produces a tileable
 * pseudo-random texture; `feColorMatrix type="saturate" values="0"` collapses
 * it to grayscale so the same noise reads consistently against both the light
 * (`#FFFFFF`) and dark (`#0A0A0B`) page backgrounds. `stitchTiles="stitch"`
 * makes adjacent tiles seam-free at any `background-size`.
 *
 * Built once at module scope — `encodeURIComponent` runs at import time, not
 * per render, so the render path stays allocation-free.
 */
const NOISE_SVG = encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">` +
    `<filter id="n">` +
    `<feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch"/>` +
    `<feColorMatrix type="saturate" values="0"/>` +
    `</filter>` +
    `<rect width="100%" height="100%" filter="url(#n)"/>` +
    `</svg>`,
);
const NOISE_DATA_URL = `url("data:image/svg+xml;utf8,${NOISE_SVG}")`;

/**
 * Token-driven gradient-mesh stops. Two soft radial gradients anchored to
 * opposite corners so the mesh frames the centered card without ever reaching
 * its surface. Opacities are kept ≤7% so the mesh is ambient texture, never a
 * gradient "paint" (design.md: "Gradient backgrounds on full sections" is an
 * anti-pattern; the auth mesh is a faint punctuation). `var(--color-brand)` /
 * `var(--color-brand-2)` resolve to the per-theme RGB triplets from
 * `globals.css`, so this is theme-aware without any hardcoded color.
 */
const GRADIENT_MESH =
  "radial-gradient(60% 55% at 12% 8%, rgb(var(--color-brand) / 0.07), transparent 70%)," +
  "radial-gradient(55% 50% at 88% 92%, rgb(var(--color-brand-2) / 0.06), transparent 70%)";

export function AuthBackground(): React.JSX.Element {
  // Slow opacity breath on the noise layer only. The keyframe range is
  // intentionally tight (0.04 ↔ 0.07) so the noise reads as ambient texture
  // rather than a visible pulse. When reduced-motion is set,
  // `useMotionSafeProps` collapses `animate` to `initial` and zeroes
  // `transition.duration`, leaving a fixed 0.04 opacity.
  const noiseMotion = useMotionSafeProps({
    initial: { opacity: 0.04 },
    animate: { opacity: [0.04, 0.07, 0.04] },
    transition: { duration: 12, repeat: Infinity, ease: "easeInOut" },
  });

  return (
    <>
      {/*
       * Static gradient-mesh layer. `fixed inset-0` covers the full viewport;
       * `-z-20` sits behind both the noise layer and the auth content;
       * `pointer-events-none` guarantees it never intercepts clicks/taps;
       * `aria-hidden` hides it from assistive tech.
       */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-20"
        style={{ backgroundImage: GRADIENT_MESH }}
      />
      {/*
       * Animated noise overlay. `fixed inset-0` covers the full viewport so
       * the texture reads continuously during scroll; `-z-10` sits behind the
       * auth content but above the gradient mesh; `pointer-events-none`
       * ensures the overlay never intercepts clicks/taps;
       * `mix-blend-mode: soft-light` blends the gray noise gently against
       * either the light or dark page background; `aria-hidden` hides it from
       * assistive tech.
       */}
      <motion.div
        aria-hidden="true"
        {...noiseMotion}
        className="pointer-events-none fixed inset-0 -z-10 [mix-blend-mode:soft-light]"
        style={{
          backgroundImage: NOISE_DATA_URL,
          backgroundSize: "200px 200px",
        }}
      />
    </>
  );
}
