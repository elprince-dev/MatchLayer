"use client";

import { useReducedMotion } from "framer-motion";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { MotionSafe } from "@/components/motion-safe";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * FinalCTA — the Landing_Page closing call-to-action section (design
 * Section 8.2 "FINAL CTA"; Req 4.5, 4.8, 4.9).
 *
 * The last band above the footer: a short `<h2>`, the primary sign-up button →
 * `/register`, and a single ≤80-char supporting line. It is the page's final
 * conversion prompt, mirroring the hero CTA so a visitor who scrolled the whole
 * page lands on the same "Get started" affordance.
 *
 * ## CTA button (Req 4.5)
 * Reuses the shared {@link Button} primitive (`asChild` over a Next `<Link>`)
 * exactly as the {@link import("./hero").Hero} CTA and the GlassNav "Get
 * started" button do, for one consistent primary action across the page. It is
 * `size="lg"` with `h-11` (44px) and `px-6`, so it clears the **≥44×44px**
 * touch-target floor. On hover the solid `bg-brand` base is overlaid with the
 * **Signature_Gradient** (violet→cyan) via
 * `hover:bg-gradient-to-r hover:from-brand hover:to-brand-2` — the same
 * gradient-on-hover recipe the hero CTA uses, keeping the gradient as
 * punctuation (design Section 4.2) rather than a painted surface.
 *
 * ## Supporting line (Req 4.5)
 * A single line of supporting text kept to **≤80 characters** and honest: it
 * describes only what the MVP does (upload a resume + job description → a
 * keyword/TF-IDF ATS score) and never claims semantic, AI-, or LLM-powered
 * analysis (Req 5.1). The ceiling is a content-authoring constraint documented
 * here and verified in review, not enforced by truncation — the same approach
 * as {@link import("./feature-card").FeatureCard}'s copy bounds.
 *
 * ## Scroll-reveal (Req 4.8, 4.9)
 * `'use client'` because the section owns a scroll-driven reveal: when it
 * enters **20% of the viewport** the content fades up over the `--motion-layout`
 * (**400ms**) timing with the ease-out-exponential curve. This matches the
 * {@link import("./how-it-works").HowItWorks} reveal pattern (`whileInView` +
 * `viewport={{ once: true, amount: 0.2 }}` routed through {@link MotionSafe}),
 * so every marketing section reveals identically.
 *
 * Reduced motion is handled by branching on `useReducedMotion()` here rather
 * than relying solely on the `MotionSafe` chokepoint: with a scroll trigger,
 * neutralizing only the `transition` would leave the content stuck at its
 * `initial` (`opacity: 0`) state until scrolled into view. Branching to a plain
 * element guarantees the final, visible state renders immediately (Req 4.9).
 *
 * ## Markup & accessibility
 * A `<section>` labelled by its `<h2>` — the correct level under the page
 * `<h1>` (hero) in the marketing-page outline (sequential heading levels,
 * design Section 10.3). All styling is token-only: no hex, no inline color, no
 * arbitrary bracket color utilities (Req 21.2). There are **no backend fields**
 * here — this is author-owned marketing content, not bound to the API contract.
 */
export interface FinalCTAProps {
  className?: string;
}

export function FinalCTA({ className }: FinalCTAProps): React.JSX.Element {
  return (
    <section
      aria-labelledby="final-cta-heading"
      className={cn("py-16 md:py-24", className)}
    >
      <div className="mx-auto max-w-7xl px-6">
        <Reveal className="mx-auto flex max-w-2xl flex-col items-center gap-6 text-center">
          <h2
            id="final-cta-heading"
            className="text-3xl font-semibold tracking-tight text-text md:text-4xl"
          >
            Ready to see your ATS score?
          </h2>

          {/* Single supporting line, ≤80 chars, honest about the MVP (Req 4.5,
              5.1). */}
          <p className="text-base text-text-muted">
            Free to try — upload a resume and job description to see your ATS
            score.
          </p>

          {/* Primary sign-up CTA → /register. h-11 (44px) + px-6 clears the
              ≥44×44px touch target; Signature_Gradient on hover over the solid
              brand base (Req 4.5), matching the hero CTA. */}
          <Button
            asChild
            size="lg"
            className="h-11 gap-2 px-6 text-base hover:bg-gradient-to-r hover:from-brand hover:to-brand-2"
          >
            <Link href="/register">
              Get started — it&apos;s free
              <ArrowRight aria-hidden="true" className="size-4" />
            </Link>
          </Button>
        </Reveal>
      </div>
    </section>
  );
}

/**
 * Scroll-driven fade-up reveal for the section (Req 4.8, 4.9).
 *
 * When the wrapped content enters **20% of the viewport** it fades up over the
 * 400ms layout-transition timing with the ease-out-exponential curve
 * (`viewport={{ once: true, amount: 0.2 }}`, `duration: 0.4`,
 * `ease: [0.16, 1, 0.3, 1]`). It plays once.
 *
 * Under `prefers-reduced-motion` it branches to a plain element so the final,
 * visible state renders immediately rather than being stranded at `opacity: 0`
 * (Req 4.9). The animated branch flows through {@link MotionSafe} for a single,
 * consistent motion entry point (design Section 6.5) — the same `Reveal`
 * pattern {@link import("./how-it-works").HowItWorks} uses.
 */
function Reveal({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}): React.JSX.Element {
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
