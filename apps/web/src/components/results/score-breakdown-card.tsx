import * as React from "react";
import type { ScoreBreakdown } from "@matchlayer/shared-types";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/**
 * ScoreBreakdownCard — the explainable, two-component breakdown behind the ATS
 * score (design Section 7.1 "ScoreBreakdownCard"; Req 11.1, 11.2, 16.2).
 *
 * ## Contract (Req 20 — never invent fields)
 * The card consumes the curated `ScoreBreakdown` type generated from the FastAPI
 * OpenAPI spec (`@matchlayer/shared-types`). That type carries **exactly** five
 * fields — `similarity_component`, `keyword_coverage_component`,
 * `weight_similarity`, `weight_keyword`, `final_score` — and this component
 * renders **exactly two** progress bars from the two component values. There is
 * structurally **no third scoring dimension** here, and no field outside the
 * generated contract is read or displayed.
 *
 * ## What it renders
 *   - Two labeled determinate progress bars, reusing the `ui/progress.tsx`
 *     primitive (task 1.5):
 *       1. **"TF-IDF similarity"**  ← `similarity_component`
 *       2. **"Keyword coverage"**   ← `keyword_coverage_component`
 *     Each backend value is a raw `[0, 1]` fraction (per the contract docs), so
 *     it is scaled to a 0–100% integer for **both** the bar fill and the
 *     displayed percentage. Numeric readouts use `font-mono` + `tabular-nums`
 *     so digits don't reflow (design typography: tabular figures for scores).
 *   - Each bar shows its **associated weight** (`weight_similarity` /
 *     `weight_keyword`) so the composition of the final score is explainable
 *     (Req 11.2).
 *   - A one-line explainer stating the final score is the weighted sum of the
 *     two components, using `final_score` (the 0–100 integer that equals the
 *     enclosing `MatchResponse.score`).
 *
 * ## Server Component (no `'use client'`)
 * This card is static — no state, effects, browser APIs, or Framer Motion. The
 * bars do not self-animate a count-up; the staggered entrance of the results
 * cards is owned by the parent `results-view` (task 4.1) through `MotionSafe`.
 * It therefore stays a Server Component and simply composes the `Progress`
 * client primitive, which is the standard RSC pattern (conventions.md: prefer
 * Server Components; mark `'use client'` only when needed).
 *
 * ## Styling
 * Token-only Tailwind throughout (no hex, no inline color styles): `bg-elevated`
 * surface, `border`/`border` tokens, `rounded-card`, `shadow-resting`, and the
 * `text`/`text-muted`/`text-subtle` foreground tokens — so the card renders
 * correctly in both Light_Mode and Dark_Mode (Req 16.9, 16.10).
 *
 * ## Degenerate (0/0) case
 * When both components are 0 the parent supersedes this card with
 * `EmptyResultState` (Req 12.5), so this component is only mounted for a
 * non-degenerate result.
 */
export interface ScoreBreakdownCardProps {
  /** The explainable two-component breakdown from the match result. */
  breakdown: ScoreBreakdown;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/** Scale a raw `[0, 1]` component value to a 0–100 integer percentage. */
function toPercent(value: number): number {
  return Math.round(value * 100);
}

export function ScoreBreakdownCard({
  breakdown,
  className,
}: ScoreBreakdownCardProps): React.JSX.Element {
  const {
    similarity_component,
    keyword_coverage_component,
    weight_similarity,
    weight_keyword,
    final_score,
  } = breakdown;

  return (
    <section
      aria-label="Score breakdown"
      className={cn(
        "w-full space-y-6 rounded-card border border-border bg-bg-elevated p-6 shadow-resting",
        className,
      )}
    >
      <h2 className="text-lg font-semibold tracking-tight text-text">
        Score breakdown
      </h2>

      <div className="space-y-6">
        <BreakdownBar
          label="TF-IDF similarity"
          value={similarity_component}
          weight={weight_similarity}
        />
        <BreakdownBar
          label="Keyword coverage"
          value={keyword_coverage_component}
          weight={weight_keyword}
        />
      </div>

      <p className="text-sm text-text-muted">
        Your final score of{" "}
        <span className="font-mono tabular-nums text-text">{final_score}</span>{" "}
        is the weighted sum of these two components.
      </p>
    </section>
  );
}

interface BreakdownBarProps {
  /** Human-readable component name shown above the bar. */
  label: string;
  /** Raw `[0, 1]` component value. */
  value: number;
  /** The weight applied to this component in the final score. */
  weight: number;
}

/**
 * One labeled component bar: name + scaled percentage on top, the determinate
 * `Progress` bar in the middle, and the associated weight below. The same
 * scaled integer drives both the visible percentage and the bar fill so they
 * never disagree.
 */
function BreakdownBar({
  label,
  value,
  weight,
}: BreakdownBarProps): React.JSX.Element {
  const percent = toPercent(value);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-text">{label}</span>
        <span className="font-mono text-sm tabular-nums text-text-muted">
          {percent}%
        </span>
      </div>

      <Progress value={percent} aria-label={`${label}: ${percent} percent`} />

      <p className="text-xs text-text-subtle">
        weight{" "}
        <span className="font-mono tabular-nums text-text-muted">{weight}</span>
      </p>
    </div>
  );
}
