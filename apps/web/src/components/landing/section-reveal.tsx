"use client";

import { useReducedMotion } from "framer-motion";
import * as React from "react";

import { MotionSafe } from "@/components/motion-safe";

/**
 * SectionReveal — the shared scroll-driven fade-up wrapper for the marketing
 * page's section bands (design Section 8.2; Req 4.8, 4.9).
 *
 * Req 4.8 requires **every** below-the-hero section (features, how it works,
 * trust signals, about, final CTA) to fade up when it enters 20% of the
 * viewport, over the 400ms layout-transition timing. Two of those sections —
 * {@link import("./how-it-works").HowItWorks} and
 * {@link import("./final-cta").FinalCTA} — own that reveal internally. The
 * other three are Server Components that deliberately defer their reveal to
 * **page assembly** (task 8.7): the features grid is built by the page, and
 * {@link import("./trust-signals").TrustSignals} / `About` document that any
 * scroll motion is layered here "via the existing `MotionSafe` chokepoint".
 * This component is that chokepoint, so the page can wrap those three and give
 * the whole page one consistent reveal.
 *
 * It is a thin, layout-neutral wrapper: a `<div>` carrying only the motion (no
 * padding/spacing of its own), so wrapping a `<section>` never shifts the
 * section's own `py-*` rhythm or width.
 *
 * ## Reduced motion (Req 4.9)
 * Mirrors the `Reveal` pattern in `HowItWorks`/`FinalCTA`: with a `whileInView`
 * scroll trigger, neutralizing only the `transition` would leave the content
 * stranded at its `initial` (`opacity: 0`) state until it scrolled into view.
 * So under `prefers-reduced-motion` we branch to a plain `<div>` and the final,
 * visible state renders immediately. The animated branch still flows through
 * {@link MotionSafe} for a single, consistent motion entry point (design
 * Section 6.5).
 *
 * `'use client'` because it reads `useReducedMotion()` and uses Framer Motion's
 * viewport trigger.
 */
export interface SectionRevealProps {
  /** The section (or grid) to reveal on scroll. */
  children: React.ReactNode;
  /** Composition hook — extends (never replaces) the wrapper. */
  className?: string;
}

export function SectionReveal({
  children,
  className,
}: SectionRevealProps): React.JSX.Element {
  const reduced = useReducedMotion();

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  return (
    <MotionSafe
      className={className}
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </MotionSafe>
  );
}
