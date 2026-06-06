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
 * Renders, top to bottom: the page `<h1>`, a supporting subheadline, the
 * primary "Get started — it's free" CTA → `/register`, and a **self-contained,
 * illustrative** animated ATS demo preview. A faint dot-grid sits behind the
 * content. All four content elements reveal with a staggered fade-up; the demo
 * gauge counts up 0→sample. Everything renders in its final, static state
 * instantly under `prefers-reduced-motion`.
 *
 * ## The honesty contract (Req 5.1, 5.4, 3.7) — load-bearing
 *
 * The demo gauge is **purely illustrative**. It is wired to a local
 * {@link SAMPLE_SCORE} constant, never to the API or a real `MatchResponse`,
 * and it is explicitly labelled as a sample "not a real analysis" so assistive
 * tech can never mistake it for a genuine score. The honesty note
 * "Basic keyword match — semantic analysis coming soon" sits directly beneath
 * it. None of the hero copy describes the scoring as semantic, embeddings-,
 * AI-, or LLM-powered — Phase 1 scoring is keyword + TF-IDF, and the
 * subheadline says exactly that ("keyword-based ATS score").
 *
 * ## Motion (Req 3.6, 3.8) — reduced-motion correctness
 *
 * Two animations run here, both gated on Framer Motion's `useReducedMotion()`:
 *
 *   1. **Staggered fade-up entrance.** The four elements animate through the
 *      shared {@link MotionSafe} reduced-motion chokepoint with delays of 0 /
 *      100 / 200 / 300ms and a 300ms per-item duration — 600ms total, the hero
 *      motion ceiling (design Section 4.8). `MotionSafe` forces `animate` to
 *      equal `initial` under reduced motion, so the per-item `initial` is set
 *      to the **final, visible** state when `reduced` is true; the element then
 *      paints visible immediately with zero motion (Req 3.8) instead of being
 *      stranded at the hidden start state.
 *   2. **Demo gauge count-up.** Driven by an imperative Framer `animate()`
 *      tween (0→`SAMPLE_SCORE` over 1200ms), the same one-value-drives-number-
 *      and-stroke pattern the flagship `ScoreGauge` uses. Under reduced motion
 *      the tween never starts and the gauge renders the final value + filled
 *      stroke instantly (Req 3.8). This is built **here**, self-contained, and
 *      deliberately does NOT reuse the results `ScoreGauge` (which consumes a
 *      real `MatchResponse.score`) — it stays visually consistent via the same
 *      Signature_Gradient (violet→cyan) stroke and gradient-clipped number.
 *
 * `'use client'` is required for both the reduced-motion hook and the
 * imperative tween.
 */

/** Hero-reveal easing from design Section 4.8 — ease-out exponential. A const
 *  tuple so Framer Motion's `Easing` type narrows correctly. */
const HERO_EASE = [0.16, 1, 0.3, 1] as const;

/** Per-item fade-up duration in seconds. Four items at 100ms stagger plus this
 *  duration keeps the sequence's total within the 600ms hero ceiling
 *  (0.3s last delay + 0.3s duration = 0.6s). */
const ITEM_SECONDS = 0.3;

/** Stagger delay between successive hero elements, in seconds (Req 3.6). */
const STAGGER_SECONDS = 0.1;

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
 *
 * When `reduced` is true, `initial` is set to the **final** visible state so
 * the {@link MotionSafe} `animate = initial` reduced-motion override resolves
 * to a visible (not hidden) element, and the transition is zeroed — the element
 * paints in its final state instantly (Req 3.8). Otherwise the element starts
 * hidden + offset and fades up after `index × 100ms`.
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
        // `isolate` scopes the dot-grid's stacking context to the section so it
        // never bleeds over sibling sections; `overflow-hidden` clips the grid.
        "relative isolate overflow-hidden",
        className,
      )}
    >
      <HeroDotGrid />

      <div className="relative z-10 mx-auto flex max-w-7xl flex-col items-center gap-6 px-6 pb-16 pt-28 text-center md:min-h-[70vh] md:justify-center md:pt-32">
        {/* The page's single <h1>. 48–60px (text-5xl → text-6xl), tracking-tight,
            font-semibold (600) — Req 3.1, 3.7. */}
        <MotionSafe
          as="h1"
          className="max-w-4xl text-balance text-5xl font-semibold tracking-tight text-text sm:text-6xl"
          {...fadeUp(0, reduced)}
        >
          See how real ATS systems evaluate your resume
        </MotionSafe>

        {/* Subheadline ≤150 chars at text-muted (Req 3.2). The "keyword-based"
            wording keeps the scoring description honest (Req 5.1). */}
        <MotionSafe
          as="p"
          className="max-w-2xl text-pretty text-lg text-text-muted"
          {...fadeUp(1, reduced)}
        >
          Upload a resume and a job description. Get a transparent,
          keyword-based ATS score in seconds.
        </MotionSafe>

        {/* Primary CTA → /register, ≥44px tall (h-11), Signature_Gradient on
            hover layered over the solid brand base (Req 3.3). */}
        <MotionSafe as="div" {...fadeUp(2, reduced)}>
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
        </MotionSafe>

        {/* Self-contained, illustrative animated demo preview (Req 3.4, 5.4). */}
        <MotionSafe
          as="div"
          className="w-full max-w-md"
          {...fadeUp(3, reduced)}
        >
          <HeroDemoPreview reduced={reduced} />
        </MotionSafe>
      </div>
    </section>
  );
}

/**
 * The faint dot-grid behind the hero content (Req 3.5).
 *
 * Decorative (`aria-hidden`) and non-interactive. The grid geometry is set via
 * an inline `background-image` because Tailwind cannot express a repeating
 * radial-gradient dot pattern with token utilities; the dot **color** still
 * references the `--color-text-subtle` token (so it stays theme-aware) and the
 * dot alpha is baked at `0.08` — i.e. the pattern renders at ≤10% opacity, well
 * below the ceiling, so foreground text keeps its full WCAG AA contrast against
 * the page background. This is the same sanctioned token-driven inline-style
 * exception the flagship `ScoreGauge` uses for its SVG gradient stops.
 */
function HeroDotGrid(): React.JSX.Element {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0"
      style={{
        backgroundImage:
          "radial-gradient(rgb(var(--color-text-subtle) / 0.08) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }}
    />
  );
}

interface HeroDemoPreviewProps {
  /** The resolved `prefers-reduced-motion` decision from the parent. */
  reduced: boolean | null;
}

/**
 * The illustrative ATS demo preview card: a small gradient gauge counting up to
 * {@link SAMPLE_SCORE} with the mandated honesty note beneath it.
 *
 * Accessibility: the gauge graphic is exposed as a single `role="img"` element
 * whose label states plainly that it is an illustrative sample and **not a real
 * analysis**, so screen-reader users are never misled into reading the animated
 * number as a genuine score. The inner SVG and the count-up number are
 * `aria-hidden`. The visible "Sample preview" caption and the honesty note are
 * normal text in the accessibility tree.
 */
function HeroDemoPreview({ reduced }: HeroDemoPreviewProps): React.JSX.Element {
  const [display, setDisplay] = React.useState(0);
  const gradientId = React.useId();

  React.useEffect(() => {
    // Reduced motion (or the SSR/unknown `null` resolving to reduced): no tween
    // runs. `shown` below already resolves to the final value in render.
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

  // Reduced motion shows the resolved sample immediately — no first-frame flash
  // of 0 (Req 3.8). Otherwise track the tween.
  const shown = reduced ? SAMPLE_SCORE : display;

  // Stroke fills clockwise from 12 o'clock: the dash offset shrinks from the
  // full circumference (empty) toward 0 (full) as `shown` rises.
  const dashOffset = CIRCUMFERENCE * (1 - shown / 100);

  return (
    <div className="flex flex-col items-center gap-4 rounded-hero border border-border-strong bg-bg-elevated p-8 shadow-resting">
      <span className="rounded-pill border border-border bg-bg px-3 py-0.5 text-xs font-medium text-text-subtle">
        Sample preview
      </span>

      <div
        role="img"
        aria-label="Illustrative sample ATS gauge — a demonstration only, not a real analysis."
        className="relative size-32 md:size-36"
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
              {/* Sanctioned token-driven, theme-aware stops (see ScoreGauge). */}
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

          {/* The gauge well — the unfilled track, in `bg`. */}
          <circle
            cx="50"
            cy="50"
            r={RADIUS}
            strokeWidth={STROKE_WIDTH}
            className="fill-none stroke-bg"
          />

          {/* The progress arc — Signature_Gradient stroke, filling clockwise
              from the top (rotated -90° so the dash starts at 12 o'clock). */}
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

        {/* The count-up number, layered over the gauge well. Gradient text-clip
            + Geist Mono + tabular-nums, matching the flagship gauge. */}
        <div className="absolute inset-0 flex items-center justify-center">
          <span
            aria-hidden="true"
            className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-mono text-4xl font-semibold tabular-nums tracking-tight text-transparent"
          >
            {shown}
          </span>
        </div>
      </div>

      {/* The mandated honesty note (Req 5.4) — so the preview never overstates
          the current keyword/TF-IDF capability. */}
      <p className="text-sm text-text-muted">
        Basic keyword match — semantic analysis coming soon
      </p>
    </div>
  );
}
