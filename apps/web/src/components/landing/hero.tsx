"use client";

import { animate, useReducedMotion, type MotionProps } from "framer-motion";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { MotionSafe } from "@/components/motion-safe";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Hero — the Landing_Page hero section (design Section 8.2; Req 3.1–3.8, 5.1,
 * 5.4).
 *
 * Renders, top to bottom: an eyebrow pill, the page `<h1>`, a supporting
 * subheadline, the primary "Get started — it's free" CTA → `/register` plus a
 * secondary in-page "See how it works" link, and a **self-contained,
 * illustrative** product-window demo preview. An ambient aurora (two faint
 * token-driven radial glows) plus a masked dot-grid sit behind the content.
 * All content elements reveal with a staggered fade-up; the demo gauge counts
 * up 0→sample. Everything renders in its final, static state instantly under
 * `prefers-reduced-motion`.
 *
 * ## The honesty contract (Req 5.1, 5.4, 3.7) — load-bearing
 *
 * The demo window is **purely illustrative**. The gauge is wired to a local
 * {@link SAMPLE_SCORE} constant, never to the API or a real `MatchResponse`,
 * and it is explicitly labelled as a sample "not a real analysis" so assistive
 * tech can never mistake it for a genuine score. The honesty note
 * "Semantic + keyword scoring — sample preview, not a real analysis" sits
 * directly beneath it. The hero copy describes the scoring as semantic +
 * keyword (shipped in phase-2-nlp-embeddings) and never as AI- or LLM-powered
 * — that remains unshipped roadmap (Req 5.1's honesty rule, updated).
 *
 * ## Motion (Req 3.6, 3.8) — reduced-motion correctness
 *
 * Two animations run here, both gated on Framer Motion's `useReducedMotion()`:
 *
 *   1. **Staggered fade-up entrance.** The five elements animate through the
 *      shared {@link MotionSafe} reduced-motion chokepoint with 75ms stagger
 *      and a 300ms per-item duration — 600ms total, the hero motion ceiling
 *      (design Section 4.8). `MotionSafe` forces `animate` to equal `initial`
 *      under reduced motion, so the per-item `initial` is set to the **final,
 *      visible** state when `reduced` is true; the element then paints visible
 *      immediately with zero motion (Req 3.8).
 *   2. **Demo gauge count-up.** Driven by an imperative Framer `animate()`
 *      tween (0→`SAMPLE_SCORE` over 1200ms), the same one-value-drives-number-
 *      and-stroke pattern the flagship `ScoreGauge` uses. Under reduced motion
 *      the tween never starts and the gauge renders the final value + filled
 *      stroke instantly (Req 3.8).
 *
 * ## Styling notes
 *
 * The aurora glows, headline halo, and dot-grid are drawn with the sanctioned
 * token-driven inline-style exception (`rgb(var(--color-brand) / <alpha>)`) —
 * the same pattern the flagship `ScoreGauge` uses for its SVG gradient stops —
 * so every color stays theme-aware with no hex and no arbitrary bracket color
 * utilities (Req 21.2). All decorative layers are `aria-hidden`,
 * `pointer-events-none`, and their alphas sit ≤12%, so foreground text keeps
 * full WCAG AA contrast against the page background.
 *
 * `'use client'` is required for both the reduced-motion hook and the
 * imperative tween.
 */

/** Hero-reveal easing from design Section 4.8 — ease-out exponential. */
const HERO_EASE = [0.16, 1, 0.3, 1] as const;

/** Per-item fade-up duration in seconds. Five items at 75ms stagger plus this
 *  duration keeps the sequence's total within the 600ms hero ceiling
 *  (0.3s last delay + 0.3s duration = 0.6s). */
const ITEM_SECONDS = 0.3;

/** Stagger delay between successive hero elements, in seconds (Req 3.6). */
const STAGGER_SECONDS = 0.075;

/**
 * The illustrative sample score the demo gauge counts up to (design Section 8.2
 * wireframe shows 78). This is **placeholder data for visual demonstration
 * only** — it is never a real analysis result (Req 3.4, 5.1).
 */
const SAMPLE_SCORE = 78;

/** Demo gauge count-up duration in seconds (1200ms; Req 3.4). */
const GAUGE_SECONDS = 1.2;

/** Gauge geometry in the `0 0 100 100` user space, mirroring the flagship
 *  `ScoreGauge` so the demo reads as the same visual language. */
const RADIUS = 42;
const STROKE_WIDTH = 8;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export interface HeroProps {
  /** Composition hook — extends (never replaces) the section's base layout. */
  className?: string;
}

/**
 * Build the staggered fade-up motion props for the hero element at `index`.
 * See the original notes: under reduced motion `initial` is the final visible
 * state so the `MotionSafe` `animate = initial` override resolves visible.
 */
function fadeUp(index: number, reduced: boolean | null): MotionProps {
  if (reduced) {
    return {
      initial: { opacity: 1, y: 0 },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0 },
    };
  }
  return {
    initial: { opacity: 0, y: 12 },
    animate: { opacity: 1, y: 0 },
    transition: {
      duration: ITEM_SECONDS,
      delay: index * STAGGER_SECONDS,
      ease: HERO_EASE,
    },
  };
}

/**
 * Hero — see file header.
 */
export function Hero({ className }: HeroProps): React.JSX.Element {
  const reduced = useReducedMotion();

  return (
    <section
      id="hero"
      className={cn(
        // `isolate` scopes the decorative layers' stacking context to the
        // section so they never bleed over sibling sections.
        "relative isolate overflow-hidden",
        className,
      )}
    >
      <HeroBackdrop />

      <div className="relative z-10 mx-auto flex max-w-7xl flex-col items-center gap-6 px-6 pb-20 pt-28 text-center md:min-h-[78vh] md:justify-center md:pt-32">
        {/* Eyebrow pill — quiet product framing above the headline. */}
        <MotionSafe as="div" {...fadeUp(0, reduced)}>
          <span className="inline-flex items-center gap-2 rounded-pill border border-border bg-bg-elevated/70 px-3.5 py-1.5 text-xs font-medium text-text-muted shadow-resting backdrop-blur-sm">
            <span
              aria-hidden="true"
              className="size-1.5 rounded-full bg-gradient-to-r from-brand to-brand-2"
            />
            Transparent, explainable resume scoring
          </span>
        </MotionSafe>

        {/* The page's single <h1>. 48–72px, tracking-tight, font-semibold —
            Req 3.1, 3.7. The exact sentence is pinned by the visual gates. */}
        <MotionSafe
          as="h1"
          className="max-w-4xl text-balance text-5xl font-semibold leading-[1.05] tracking-tight text-text sm:text-6xl lg:text-7xl"
          {...fadeUp(1, reduced)}
        >
          See how real ATS systems evaluate your resume
        </MotionSafe>

        {/* Subheadline ≤150 chars at text-muted (Req 3.2). The "semantic +
            keyword" wording keeps the scoring description honest (Req 5.1) —
            semantic similarity shipped in phase-2-nlp-embeddings. */}
        <MotionSafe
          as="p"
          className="max-w-2xl text-pretty text-lg leading-relaxed text-text-muted sm:text-xl"
          {...fadeUp(2, reduced)}
        >
          Upload a resume and a job description. Get a transparent, semantic +
          keyword ATS score in seconds.
        </MotionSafe>

        {/* CTA row: primary → /register (≥44px tall, Signature_Gradient on
            hover — Req 3.3) + a quiet secondary in-page link. */}
        <MotionSafe
          as="div"
          className="flex flex-col items-center gap-3 sm:flex-row"
          {...fadeUp(3, reduced)}
        >
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
          <Button asChild variant="ghost" size="lg" className="h-12 px-6">
            <Link href="#how-it-works">See how it works</Link>
          </Button>
        </MotionSafe>

        {/* Self-contained, illustrative product-window preview (Req 3.4, 5.4). */}
        <MotionSafe
          as="div"
          className="mt-6 w-full max-w-3xl"
          {...fadeUp(4, reduced)}
        >
          <HeroDemoPreview reduced={reduced} />
        </MotionSafe>
      </div>
    </section>
  );
}

/**
 * The ambient hero backdrop (Req 3.5): two soft brand-token radial glows
 * ("aurora") layered under a dot-grid that fades out radially via a CSS mask.
 *
 * Decorative (`aria-hidden`) and non-interactive. All colors are token-driven
 * (`rgb(var(--color-*) / alpha)`) so the backdrop re-tints per theme; alphas
 * are ≤12% so it stays ambient texture — never gradient "paint" — and never
 * dents foreground contrast.
 */
function HeroBackdrop(): React.JSX.Element {
  return (
    <>
      {/* Aurora glows: violet top-center halo + cyan lower-right wash. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(52% 42% at 50% 0%, rgb(var(--color-brand) / 0.12), transparent 70%)," +
            "radial-gradient(38% 34% at 78% 68%, rgb(var(--color-brand-2) / 0.08), transparent 72%)," +
            "radial-gradient(34% 30% at 18% 62%, rgb(var(--color-brand) / 0.06), transparent 72%)",
        }}
      />
      {/* Dot-grid, masked so it dissolves toward the edges instead of tiling
          the whole band uniformly. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(rgb(var(--color-text-subtle) / 0.1) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
          maskImage:
            "radial-gradient(70% 60% at 50% 38%, black 30%, transparent 100%)",
          WebkitMaskImage:
            "radial-gradient(70% 60% at 50% 38%, black 30%, transparent 100%)",
        }}
      />
    </>
  );
}

interface HeroDemoPreviewProps {
  /** The resolved `prefers-reduced-motion` decision from the parent. */
  reduced: boolean | null;
}

/**
 * Illustrative sample metric rows shown beside the demo gauge. Purely
 * decorative content inside the labelled demo window — plain divs, never
 * `role="progressbar"` (the real breakdown progressbars are a results-page
 * contract; the hero must not imitate their semantics, only their look).
 */
const SAMPLE_METRICS = [
  { label: "Text similarity", value: 82 },
  { label: "Keyword coverage", value: 71 },
] as const;

/** Illustrative keyword pills for the demo window. */
const SAMPLE_KEYWORDS = [
  { label: "react", matched: true },
  { label: "typescript", matched: true },
  { label: "aws", matched: true },
  { label: "kubernetes", matched: false },
] as const;

/**
 * The illustrative product-window preview: a faux browser frame containing the
 * gradient score gauge, two sample metric bars, and sample keyword pills, with
 * the mandated honesty note beneath it.
 *
 * Accessibility: the gauge graphic is exposed as a single `role="img"` element
 * whose label states plainly that it is an illustrative sample and **not a
 * real analysis**. The inner SVG, the count-up number, and the decorative
 * metric/keyword content are `aria-hidden` where they could read as real data;
 * the visible "Sample preview" caption and the honesty note are normal text in
 * the accessibility tree.
 */
function HeroDemoPreview({ reduced }: HeroDemoPreviewProps): React.JSX.Element {
  const [display, setDisplay] = React.useState(0);
  const gradientId = React.useId();

  React.useEffect(() => {
    if (reduced) {
      return;
    }

    const controls = animate(0, SAMPLE_SCORE, {
      duration: GAUGE_SECONDS,
      ease: HERO_EASE,
      onUpdate: (value) => setDisplay(Math.round(value)),
    });

    return () => controls.stop();
  }, [reduced]);

  // Reduced motion shows the resolved sample immediately — no first-frame
  // flash of 0 (Req 3.8). Otherwise track the tween.
  const shown = reduced ? SAMPLE_SCORE : display;
  const dashOffset = CIRCUMFERENCE * (1 - shown / 100);

  return (
    <div className="relative">
      {/* Soft brand glow bleeding out from behind the window (decorative). */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -inset-6 -z-10 rounded-hero blur-2xl"
        style={{
          backgroundImage:
            "linear-gradient(135deg, rgb(var(--color-brand) / 0.16), rgb(var(--color-brand-2) / 0.12))",
        }}
      />

      <div className="overflow-hidden rounded-hero border border-border-strong bg-bg-elevated text-left shadow-elevated">
        {/* Faux window chrome. */}
        <div className="flex items-center gap-2 border-b border-border bg-bg/60 px-4 py-3">
          <span aria-hidden="true" className="flex gap-1.5">
            <span className="size-2.5 rounded-full bg-danger/50" />
            <span className="size-2.5 rounded-full bg-warning/50" />
            <span className="size-2.5 rounded-full bg-success/50" />
          </span>
          <span className="mx-auto hidden rounded-pill border border-border bg-bg px-3 py-0.5 font-mono text-xs text-text-subtle sm:block">
            matchlayer.net/matches
          </span>
          <span className="ml-auto rounded-pill border border-border bg-bg px-2.5 py-0.5 text-xs font-medium text-text-subtle sm:ml-0">
            Sample preview
          </span>
        </div>

        {/* Window body: gauge + sample breakdown. */}
        <div className="grid items-center gap-8 p-6 sm:grid-cols-[auto_1fr] sm:p-8">
          <div
            role="img"
            aria-label="Illustrative sample ATS gauge — a demonstration only, not a real analysis."
            className="relative mx-auto size-36 md:size-40"
          >
            <svg
              viewBox="0 0 100 100"
              className="size-full"
              role="presentation"
              aria-hidden="true"
            >
              <defs>
                <linearGradient
                  id={gradientId}
                  gradientUnits="userSpaceOnUse"
                  x1="0"
                  y1="0"
                  x2="100"
                  y2="100"
                >
                  <stop
                    offset="0%"
                    style={{ stopColor: "rgb(var(--color-brand))" }}
                  />
                  <stop
                    offset="100%"
                    style={{ stopColor: "rgb(var(--color-brand-2))" }}
                  />
                </linearGradient>
              </defs>

              <circle
                cx="50"
                cy="50"
                r={RADIUS}
                strokeWidth={STROKE_WIDTH}
                className="fill-none stroke-bg"
              />
              <circle
                cx="50"
                cy="50"
                r={RADIUS}
                strokeWidth={STROKE_WIDTH}
                strokeLinecap="round"
                stroke={`url(#${gradientId})`}
                strokeDasharray={CIRCUMFERENCE}
                strokeDashoffset={dashOffset}
                transform="rotate(-90 50 50)"
                className="fill-none"
              />
            </svg>

            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span
                aria-hidden="true"
                className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-mono text-5xl font-semibold tabular-nums tracking-tight text-transparent"
              >
                {shown}
              </span>
              <span
                aria-hidden="true"
                className="text-xs font-medium text-text-subtle"
              >
                match score
              </span>
            </div>
          </div>

          {/* Sample metric bars + keyword pills. Decorative illustration —
              plain divs, no progressbar/list semantics (see note above). */}
          <div aria-hidden="true" className="flex flex-col gap-5">
            {SAMPLE_METRICS.map((metric) => (
              <div key={metric.label} className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm font-medium text-text">
                    {metric.label}
                  </span>
                  <span className="font-mono text-xs tabular-nums text-text-subtle">
                    {metric.value}%
                  </span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-pill bg-bg">
                  <div
                    className="h-full rounded-pill bg-gradient-to-r from-brand to-brand-2"
                    style={{ width: `${metric.value}%` }}
                  />
                </div>
              </div>
            ))}

            <div className="flex flex-wrap gap-2">
              {SAMPLE_KEYWORDS.map((keyword) => (
                <span
                  key={keyword.label}
                  className={cn(
                    "inline-flex items-center rounded-pill border px-2.5 py-0.5 font-mono text-xs text-text",
                    keyword.matched
                      ? "border-success bg-success/15"
                      : "border-warning bg-warning/15",
                  )}
                >
                  {keyword.label}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* The mandated honesty note (Req 5.4) — semantic scoring shipped in
          phase-2-nlp-embeddings; the sample itself is still illustrative. */}
      <p className="mt-4 text-center text-sm text-text-muted">
        Semantic + keyword scoring — sample preview, not a real analysis
      </p>
    </div>
  );
}
