"use client";

import { animate, useReducedMotion } from "framer-motion";
import * as React from "react";

import { scoreLabel } from "@/lib/score-label";
import { cn } from "@/lib/utils";

/**
 * ScoreGauge + co-located ScoreLabel — the flagship score reveal of the ATS
 * Results page (design Section 7.1; Req 10.1–10.4, 10.6, 10.7, 12.1, 16.1,
 * 18.5).
 *
 * Renders a circular SVG gauge whose stroke fills clockwise from 12 o'clock
 * (0 = empty ring, 100 = full ring) using the Signature_Gradient (violet→cyan)
 * over a `bg-elevated` well, with the score number layered in the center in
 * Geist Mono `text-6xl` painted with the same gradient via `bg-clip-text`. A
 * count-up animates 0→`score` over 600ms with the ease-out-exponential curve
 * `[0.16, 1, 0.3, 1]`; that single animated value drives BOTH the number and
 * the stroke fill, so they stay perfectly in sync.
 *
 * Consumes ONLY `MatchResponse.score` (an integer in `[0, 100]`). It reads no
 * other Match_Result field — `score_breakdown`, keywords, and suggestions are
 * the concern of sibling components.
 *
 * ## Reduced motion
 *
 * The reveal is gated by Framer Motion's `useReducedMotion()` — the same
 * primitive the shared `MotionSafe` / `useMotionSafeProps` chokepoint
 * (`components/motion-safe.tsx`) is built on. When the user prefers reduced
 * motion the count-up never runs and the rendered value is taken directly from
 * `score`, so the final number and the fully-filled stroke appear instantly
 * with zero motion (Req 10.6, 15.4). An imperative tween is used here (rather
 * than the declarative `MotionSafe` wrapper) precisely because one numeric
 * value must drive both a React text node and the SVG `stroke-dashoffset`
 * geometry — a shape the declarative entrance/reveal wrapper can't express —
 * while still honoring the same reduced-motion decision.
 *
 * ## Tokens
 *
 * Styling is token-only (Req 16.9). The gauge well uses the `stroke-bg-elevated`
 * Tailwind utility and the number uses the `from-brand`/`to-brand-2`
 * gradient-clip recipe shared with the wordmark. The one sanctioned exception
 * to "no inline styles / no hex" is the SVG `<linearGradient>` stop colors:
 * SVG `stop-color` only resolves `var()` when set as a CSS property, so the
 * stops are set via inline `style` to `rgb(var(--color-brand))` /
 * `rgb(var(--color-brand-2))`. These reference the exact `globals.css` brand
 * triplets, so the gradient stays token-driven AND theme-aware (the `.dark`
 * overrides flow through automatically) without ever hardcoding a hex value.
 */

/** Hero-reveal easing from design Section 4.8 ("Motion") — ease-out exponential.
 *  A const tuple so Framer Motion's `Easing` type narrows correctly. */
const SCORE_EASE = [0.16, 1, 0.3, 1] as const;

/** Score reveal duration in seconds (design: 600ms hero reveal; Req 10.2). */
const REVEAL_SECONDS = 0.6;

/** Gauge geometry in the `0 0 100 100` user space. The stroke (width 8,
 *  centered on r=42) spans radius 38–46, leaving a 4-unit margin to the edge. */
const RADIUS = 42;
const STROKE_WIDTH = 8;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** Clamp an arbitrary numeric score into the canonical `[0, 100]` display
 *  domain. A non-finite value collapses to 0 so the gauge never renders `NaN`
 *  geometry. The qualitative label is derived from the raw `score` separately
 *  (`scoreLabel` owns its own out-of-domain handling). */
function clampScore(score: number): number {
  if (!Number.isFinite(score)) {
    return 0;
  }
  return Math.min(100, Math.max(0, score));
}

/** Props for {@link ScoreGauge}. */
export interface ScoreGaugeProps {
  /** The Match_Result score — an integer in `[0, 100]`. */
  score: number;
  /** Optional extra classes merged onto the gauge container. */
  className?: string;
}

/**
 * The circular score gauge with its centered count-up number and the
 * co-located {@link ScoreLabel} directly below.
 */
export function ScoreGauge({
  score,
  className,
}: ScoreGaugeProps): React.JSX.Element {
  const reduced = useReducedMotion();
  const target = clampScore(score);

  // The animated value that drives both the number and the stroke. Starts at 0
  // so the count-up reads 0→score; reduced-motion users never see this state
  // because `shown` below short-circuits to the final value.
  const [display, setDisplay] = React.useState(0);

  // A unique gradient id per instance so multiple gauges on a page can't
  // collide on `url(#...)` references.
  const gradientId = React.useId();

  React.useEffect(() => {
    // Reduced motion (or the SSR/unknown `null` case resolving to reduced):
    // run no tween at all. `shown` below already resolves to the final value
    // in render for this branch, so there is nothing to set here.
    if (reduced) {
      return;
    }

    const controls = animate(0, target, {
      duration: REVEAL_SECONDS,
      ease: SCORE_EASE,
      onUpdate: (value) => setDisplay(Math.round(value)),
    });

    return () => controls.stop();
  }, [target, reduced]);

  // Reduced motion shows the resolved score immediately regardless of effect
  // timing — no first-frame flash of 0 (Req 10.6). Otherwise track the tween.
  const shown = reduced ? target : display;

  // Stroke fills clockwise from the top: the dash offset shrinks from the full
  // circumference (empty) toward 0 (full) as `shown` rises (Req 10.1, 10.7).
  const dashOffset = CIRCUMFERENCE * (1 - shown / 100);

  return (
    <div
      className={cn("flex flex-col items-center gap-3", className)}
      data-testid="score-gauge"
    >
      {/* Mobile diameter 120px (size-30); desktop 160px (size-40) — Req 18.5. */}
      <div className="relative size-30 md:size-40">
        <svg
          viewBox="0 0 100 100"
          className="size-full"
          role="presentation"
          aria-hidden="true"
        >
          <defs>
            {/* userSpaceOnUse keeps the 135°-style top-left→bottom-right sweep
                fixed to the canvas, so rotating the progress arc to start at
                12 o'clock does not rotate the gradient with it. */}
            <linearGradient
              id={gradientId}
              gradientUnits="userSpaceOnUse"
              x1="0"
              y1="0"
              x2="100"
              y2="100"
            >
              {/* Sanctioned token-driven, theme-aware stops (see file header). */}
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

          {/* The gauge well — the unfilled track, rendered in `bg-elevated`. */}
          <circle
            cx="50"
            cy="50"
            r={RADIUS}
            strokeWidth={STROKE_WIDTH}
            className="fill-none stroke-bg-elevated"
          />

          {/* The progress arc — Signature_Gradient stroke, filling clockwise
              from the top. Rotated -90° so the dash starts at 12 o'clock. */}
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

        {/* The score number, layered over the gauge well. `text-6xl` Geist Mono
            with the gradient text-clip recipe shared with the wordmark. */}
        <div className="absolute inset-0 flex items-center justify-center">
          <span
            aria-hidden="true"
            className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-mono text-6xl font-semibold tabular-nums tracking-tight text-transparent"
          >
            {shown}
          </span>
        </div>
      </div>

      <ScoreLabel score={score} />

      {/* Announced once by assistive tech as the resolved result; the animated
          number above is aria-hidden so intermediate frames are never read. */}
      <span className="sr-only">{`Match score: ${Math.round(target)} out of 100.`}</span>
    </div>
  );
}

/** Props for {@link ScoreLabel}. */
export interface ScoreLabelProps {
  /** The Match_Result score — an integer in `[0, 100]`. */
  score: number;
}

/**
 * The qualitative band label rendered directly below the gauge (design
 * Section 7.1; Req 10.4, 12.1). Maps `score` → "Excellent" / "Good" / "Fair" /
 * "Needs Work" via the shared `scoreLabel()` mapping. Always present for a
 * successful result — a score of 0 still yields "Needs Work", never a blank
 * (Req 12.1).
 */
export function ScoreLabel({ score }: ScoreLabelProps): React.JSX.Element {
  return (
    <p className="text-lg font-semibold tracking-tight text-text">
      {scoreLabel(score)}
    </p>
  );
}
