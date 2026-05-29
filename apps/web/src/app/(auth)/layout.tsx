"use client";

import { motion } from "framer-motion";
import * as React from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { useMotionSafeProps } from "@/components/motion-safe";

/**
 * Route-group layout for `/register`, `/login`, `/forgot-password`, and
 * `/reset-password` per Auth Pages Design §14.1.
 *
 * Renders the centered-card auth shell:
 *
 *   - Top: the "MatchLayer" wordmark in the violet → cyan brand gradient,
 *     using the same `bg-gradient-to-br from-brand to-brand-2 bg-clip-text
 *     text-transparent` recipe as the foundation landing page (foundation §7.8
 *     and `apps/web/src/components/hero-text.tsx`). Sized smaller here than on
 *     the landing page because the auth shell is "restrained" per
 *     `design.md` — the wordmark is a brand mark, not a hero.
 *
 *   - Card slot: `<AuthCard>` wraps the route's `{children}`. The card chrome
 *     (`max-w-md`, `rounded-2xl`, `border-strong`, `bg-bg-elevated`, layered
 *     shadow) is owned by `AuthCard` so each individual auth page is just a
 *     form, not a card-builder.
 *
 *   - Background: a subtle grayscale fractal-noise overlay rendered via an
 *     inline SVG turbulence filter, animated with a slow ~12s opacity breath
 *     using Framer Motion. Motion props are passed through the foundation's
 *     `useMotionSafeProps` hook (which wraps framer-motion's
 *     `useReducedMotion`) so users who set `prefers-reduced-motion` see a
 *     completely static texture — no opacity changes, no animation frames.
 *     This satisfies the §14.1 requirement that the background "respects
 *     `prefers-reduced-motion` via the foundation `motion-safe.tsx` hook".
 *
 * The wordmark is rendered as a `<span>` (not an anchor) — §14.1 says auth
 * pages have "no navigation chrome other than a link between sibling auth
 * pages where relevant", and a clickable brand mark is navigation chrome.
 *
 * The root `app/layout.tsx` already supplies `<html>`, `<body>`, the Geist
 * fonts, and the next-themes provider; this layout therefore renders only the
 * auth-shell DOM and inherits everything else.
 *
 * `'use client'` is required because the noise overlay calls
 * `useMotionSafeProps`, which in turn calls a framer-motion hook. The layout
 * has no data-fetching needs, so the client boundary is the cheapest path —
 * adding a separate client island would mean a new file outside the four the
 * task explicitly enumerates.
 */

/**
 * Inline SVG turbulence pattern, encoded as a data URL for use as a
 * `background-image`. `feTurbulence type="fractalNoise"` produces a tileable
 * pseudo-random texture; the `feColorMatrix type="saturate" values="0"`
 * collapses it to grayscale so the same noise reads consistently against both
 * the light (`#FFFFFF`) and dark (`#0A0A0B`) page backgrounds.
 * `stitchTiles="stitch"` makes adjacent tiles seam-free at any
 * `background-size`.
 *
 * Built once at module scope — `encodeURIComponent` runs at import time, not
 * per render, so the layout's render path stays allocation-free.
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

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  // Slow opacity breath. The keyframe range is intentionally tight (0.04 ↔
  // 0.07) so the noise reads as ambient texture rather than a visible pulse.
  // When reduced-motion is set, `useMotionSafeProps` collapses `animate` to
  // `initial` and zeroes `transition.duration`, leaving a fixed 0.04 opacity.
  const noiseMotion = useMotionSafeProps({
    initial: { opacity: 0.04 },
    animate: { opacity: [0.04, 0.07, 0.04] },
    transition: { duration: 12, repeat: Infinity, ease: "easeInOut" },
  });

  return (
    <div className="relative flex min-h-screen flex-col bg-bg text-text">
      {/*
       * Animated noise overlay. `fixed inset-0` covers the full viewport
       * (not just the layout subtree) so the texture reads continuously
       * during scroll; `-z-10` sits behind the auth content;
       * `pointer-events-none` ensures the overlay never intercepts
       * clicks/taps; `mix-blend-mode: soft-light` blends the gray noise
       * gently against either the light or dark page background;
       * `aria-hidden` hides it from assistive tech.
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

      <main className="flex flex-1 flex-col items-center justify-center px-6 py-16">
        <span
          aria-label="MatchLayer"
          className="mb-8 bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-3xl font-semibold tracking-tight text-transparent"
        >
          MatchLayer
        </span>
        <AuthCard>{children}</AuthCard>
      </main>
    </div>
  );
}
