"use client";

import { useReducedMotion } from "framer-motion";
import { ClipboardPaste, FileUp, Gauge, type LucideIcon } from "lucide-react";
import * as React from "react";

import { MotionSafe } from "@/components/motion-safe";
import { cn } from "@/lib/utils";

/**
 * HowItWorks — the Landing_Page "How it works" section (design Section 8.2;
 * Req 4.2, 4.8, 4.9).
 *
 * A numbered **three-step flow** — upload your resume → paste the job
 * description → get your ATS score — with **visual connectors** (lines)
 * between the steps. The flow lays out **horizontally above 768px** and
 * **vertically at or below 768px** (Req 4.2), matching the desktop/mobile
 * wireframes in design Section 8.2.
 *
 * `'use client'` because the section owns a scroll-driven reveal: when it
 * enters **20% of the viewport** its content fades up over the
 * `--motion-layout` (**400ms**) timing (Req 4.8). Under
 * `prefers-reduced-motion` the content renders in its final state immediately —
 * no fade-up (Req 4.9; see {@link Reveal}).
 *
 * ## Honesty (Req 5.1, 5.4)
 * Step 3 describes the output as a **keyword and TF-IDF** match score, never
 * "semantic", "AI", or "LLM" — the current MVP scorer is keyword/TF-IDF based,
 * and this copy must not overstate it.
 *
 * ## Markup & accessibility
 *   - A `<section id="how-it-works">` so the GlassNav `#how-it-works` anchor
 *     (design Section 7.3 GlassNav, Req 6.3) targets it; labelled by the
 *     section `<h2>` — the correct level under the page `<h1>` (hero).
 *   - The steps are an `<ol>` (an ordered flow), so the 1→2→3 sequence is
 *     conveyed to assistive technology by list semantics, not by the visual
 *     number badge alone. Step titles are `<h3>` (under the section `<h2>`).
 *   - Connectors and icons are decorative (`aria-hidden`); the readable number
 *     badge stays in the accessibility tree as reinforcement.
 *
 * All styling is token-only — no hex, no inline color, no arbitrary bracket
 * color utilities (Req 21.2). The number badge and icon well reuse the
 * tinted-fill + full-token-border + primary-`text` treatment established by
 * {@link import("./feature-card").FeatureCard} so they pass WCAG AA in both
 * themes (design Section 10.2).
 */

/** One step in the flow. Content is author-owned marketing copy — there are no
 *  backend fields here. */
interface Step {
  /** 1-based step number shown in the badge. */
  readonly number: number;
  /** Short step title (`<h3>`). */
  readonly title: string;
  /** Short supporting blurb. */
  readonly description: string;
  /** Decorative Lucide icon for the step's icon well. */
  readonly icon: LucideIcon;
}

const STEPS: readonly Step[] = [
  {
    number: 1,
    title: "Upload your resume",
    description:
      "Drop in a PDF or DOCX. We extract the text to analyze — the file never goes anywhere else.",
    icon: FileUp,
  },
  {
    number: 2,
    title: "Paste the job description",
    description:
      "Add the role you're targeting so we know exactly what to score your resume against.",
    icon: ClipboardPaste,
  },
  {
    number: 3,
    title: "Get your ATS score",
    description:
      "See a transparent keyword and TF-IDF match score, with matched and missing terms, in seconds.",
    icon: Gauge,
  },
];

export interface HowItWorksProps {
  className?: string;
}

export function HowItWorks({ className }: HowItWorksProps): React.JSX.Element {
  return (
    <section
      id="how-it-works"
      aria-labelledby="how-it-works-heading"
      className={cn("py-16 md:py-24", className)}
    >
      <div className="mx-auto max-w-7xl px-6">
        <Reveal>
          <div className="flex flex-col items-center gap-4 text-center">
            <h2
              id="how-it-works-heading"
              className="text-3xl font-semibold tracking-tight text-text md:text-4xl"
            >
              How it works
            </h2>
            <p className="max-w-2xl text-text-muted">
              Three steps from upload to a transparent ATS score — no guesswork
              about how your resume is read.
            </p>
          </div>

          {/* Numbered flow. Horizontal (row) above 768px, vertical (stacked)
              at or below 768px (Req 4.2). The `<ol>` carries the sequence
              semantics; connectors are decorative. */}
          <ol className="mt-12 flex list-none flex-col md:flex-row md:items-stretch">
            {STEPS.map((step, index) => (
              <li
                key={step.number}
                className="flex flex-1 flex-col items-center md:flex-row md:items-stretch"
              >
                <div className="flex h-full w-full flex-col items-center gap-4 rounded-hero border border-border bg-bg-elevated p-6 text-center shadow-resting md:flex-1">
                  <div className="relative">
                    <span className="flex size-12 items-center justify-center rounded-hero border border-border-strong bg-bg text-brand">
                      <step.icon aria-hidden="true" className="size-6" />
                    </span>
                    <span className="absolute -right-2 -top-2 flex size-6 items-center justify-center rounded-full border border-brand bg-brand/15 text-xs font-semibold tabular-nums text-text">
                      {step.number}
                    </span>
                  </div>
                  <h3 className="text-lg font-semibold tracking-tight text-text">
                    {step.title}
                  </h3>
                  <p className="text-sm text-text-muted">{step.description}</p>
                </div>

                <Connector isLast={index === STEPS.length - 1} />
              </li>
            ))}
          </ol>
        </Reveal>
      </div>
    </section>
  );
}

/**
 * Decorative connector between two steps (Req 4.2 "visual connectors").
 *
 * A thin `border-strong` line: **vertical** between stacked steps at ≤768px,
 * **horizontal** between steps in the ≥768px row. The trailing connector after
 * the final step is removed on mobile (`hidden`) and kept space-preserving but
 * invisible on desktop (`md:invisible`) so every step card keeps an identical
 * width in the horizontal row.
 */
function Connector({ isLast }: { isLast: boolean }): React.JSX.Element {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex shrink-0 items-center justify-center",
        // Vertical gap on mobile; fixed horizontal slot on desktop.
        "h-8 w-full md:h-auto md:w-12",
        isLast && "hidden md:invisible",
      )}
    >
      <span className="block h-full w-px bg-border-strong md:h-px md:w-full" />
    </span>
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
 * Reduced motion is handled by branching on `useReducedMotion()` here rather
 * than relying solely on the `MotionSafe` chokepoint: with a scroll trigger,
 * neutralizing only the `transition` would still leave the content stuck at its
 * `initial` (`opacity: 0`) state until it scrolled into view. Branching to a
 * plain element guarantees the final, visible state renders immediately (Req
 * 4.9). The animated branch still flows through `MotionSafe` for a single,
 * consistent motion entry point (design Section 6.5).
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
