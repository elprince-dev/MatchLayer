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
 * The last band above the footer: a showpiece panel carrying a short `<h2>`,
 * a single ≤80-char supporting line, and the primary sign-up button →
 * `/register`. It is the page's final conversion prompt, mirroring the hero
 * CTA so a visitor who scrolled the whole page lands on the same
 * "Get started" affordance.
 *
 * ## Panel treatment
 * The panel is an elevated `rounded-hero` card whose backdrop layers two faint
 * brand-token radial glows and a masked dot-grid — the same ambient recipe as
 * the hero backdrop, closing the page on the visual note it opened with. All
 * decorative layers are `aria-hidden`, token-driven (`rgb(var(--color-*) /
 * alpha)`, the sanctioned ScoreGauge inline-style exception), and ≤14% alpha,
 * so the gradient stays punctuation (design Section 4.2) and text contrast is
 * unaffected.
 *
 * ## CTA button (Req 4.5)
 * Reuses the shared {@link Button} primitive (`asChild` over a Next `<Link>`)
 * exactly as the hero CTA does. `h-12` clears the ≥44×44px touch-target floor,
 * and hover overlays the Signature_Gradient (violet→cyan) on the solid brand
 * base.
 *
 * ## Supporting line (Req 4.5)
 * Kept to ≤80 characters and honest: it describes only what the MVP does and
 * never claims semantic, AI-, or LLM-powered analysis (Req 5.1).
 *
 * ## Scroll-reveal (Req 4.8, 4.9)
 * Same `Reveal` pattern as HowItWorks: fades up over the 400ms layout timing
 * when 20% enters the viewport; under `prefers-reduced-motion` it branches to
 * a plain element so the final state renders immediately.
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
        <Reveal>
          <div className="relative isolate overflow-hidden rounded-hero border border-border-strong bg-bg-elevated px-6 py-16 shadow-elevated md:py-20">
            {/* Ambient panel backdrop — brand glows + masked dot-grid
                (decorative, token-driven, low alpha). */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 -z-10"
              style={{
                backgroundImage:
                  "radial-gradient(60% 90% at 50% 0%, rgb(var(--color-brand) / 0.14), transparent 70%)," +
                  "radial-gradient(40% 60% at 85% 100%, rgb(var(--color-brand-2) / 0.1), transparent 72%)",
              }}
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 -z-10"
              style={{
                backgroundImage:
                  "radial-gradient(rgb(var(--color-text-subtle) / 0.1) 1px, transparent 1px)",
                backgroundSize: "24px 24px",
                maskImage:
                  "radial-gradient(60% 80% at 50% 30%, black 20%, transparent 100%)",
                WebkitMaskImage:
                  "radial-gradient(60% 80% at 50% 30%, black 20%, transparent 100%)",
              }}
            />

            <div className="mx-auto flex max-w-2xl flex-col items-center gap-6 text-center">
              <h2
                id="final-cta-heading"
                className="text-balance text-3xl font-semibold tracking-tight text-text md:text-5xl"
              >
                Ready to see your ATS score?
              </h2>

              {/* Single supporting line, ≤80 chars, honest about the MVP
                  (Req 4.5, 5.1). */}
              <p className="text-base text-text-muted md:text-lg">
                Free to try — upload a resume and job description to see your
                ATS score.
              </p>

              {/* Primary sign-up CTA → /register. h-12 clears the ≥44×44px
                  touch target; Signature_Gradient on hover over the solid
                  brand base (Req 4.5), matching the hero CTA. */}
              <Button
                asChild
                size="lg"
                className="h-12 gap-2 px-7 text-base shadow-elevated hover:bg-gradient-to-r hover:from-brand hover:to-brand-2"
              >
                <Link href="/register">
                  Get started — it&apos;s free
                  <ArrowRight aria-hidden="true" className="size-4" />
                </Link>
              </Button>

              <p className="text-xs text-text-subtle">
                No credit card required.
              </p>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

/**
 * Scroll-driven fade-up reveal for the section (Req 4.8, 4.9). Same pattern as
 * {@link import("./how-it-works").HowItWorks}: `whileInView` at 20% viewport
 * over the 400ms layout timing; under `prefers-reduced-motion` it branches to
 * a plain element so the final, visible state renders immediately, and the
 * animated branch flows through {@link MotionSafe}.
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
