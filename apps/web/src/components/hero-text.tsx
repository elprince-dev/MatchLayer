"use client";

import { motion } from "framer-motion";

import { useMotionSafeProps } from "@/components/motion-safe";

/**
 * "Hero reveal" easing curve from `design.md` ("Motion" section): a smooth
 * ease-out comparable to easeOutExpo. Declared as a const tuple so framer-motion's
 * `Easing` type ([number, number, number, number]) narrows correctly — a plain
 * array literal would be inferred as `number[]` and rejected by the typed `ease`
 * prop.
 */
const HERO_EASE = [0.16, 1, 0.3, 1] as const;

/**
 * Animated hero wordmark for the placeholder landing page.
 *
 * Renders the literal text "MatchLayer" with the violet → cyan brand gradient
 * and a single fade-up entrance via Framer Motion, wired through
 * `useMotionSafeProps` so users with `prefers-reduced-motion` see the final
 * state instantly with no animation.
 *
 * Why this lives in its own client component instead of inline in `page.tsx`:
 * `page.tsx` should remain a server component (per `conventions.md`: "prefer
 * Server Components; mark `'use client'` only when needed"). Framer Motion's
 * `motion.*` primitives and the `useMotionSafeProps` hook both rely on browser
 * APIs / React state, so they're isolated to this small client island.
 *
 * Accessibility / contrast notes:
 *   - At `text-6xl font-semibold` (≈60px / 600 weight) the wordmark qualifies
 *     as "large text" under WCAG, where the AA contrast threshold drops to
 *     3:1. The brand gradient comfortably clears that bar against `bg-bg` in
 *     both light and dark themes; the readable body copy on the page is the
 *     tagline rendered in `text-text-muted` (which itself passes AA against
 *     `bg-bg` — ≈7.2:1 light, ≈9.4:1 dark).
 *   - The wordmark is the page's `<h1>`. Screen readers announce the literal
 *     text "MatchLayer" — `bg-clip-text` only paints the glyphs; it doesn't
 *     remove them from the accessibility tree.
 */
export function HeroText() {
  const motionProps = useMotionSafeProps({
    initial: { opacity: 0, y: 16 },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.6, ease: HERO_EASE },
  });

  return (
    <motion.h1
      {...motionProps}
      className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-5xl font-semibold tracking-tight text-transparent sm:text-6xl"
    >
      MatchLayer
    </motion.h1>
  );
}
