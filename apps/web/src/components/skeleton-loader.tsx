import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * SkeletonLoader — the primary loading pattern for MatchLayer (Req 17.2).
 *
 * Composes the `ui/skeleton.tsx` primitive into placeholder shapes that mirror
 * the layout of the content they replace, so loading never causes a spinner-only
 * state and content swap-in produces no layout shift:
 *
 *   - `results` → a gauge circle + qualitative-label bar, two breakdown-bar
 *     placeholders, and matched/missing keyword pill rows — matching the ATS
 *     Results success layout shape (Req 10.5, 13.4).
 *   - `upload` → a drop-zone block plus job-description field placeholders and a
 *     submit-button block — matching the Upload page shape.
 *
 * ## Shimmer cadence (Req 17.1)
 * The SkeletonLoader owns the 1.5s shimmer cadence. The base `Skeleton` ships
 * Tailwind's `animate-pulse` (a 2s cycle); here every descendant skeleton's
 * `animation-duration` is re-pointed to 1.5s via a descendant variant. The
 * generated selector (`.<container> [data-slot=skeleton]`) has higher specificity
 * than the single-class `.animate-pulse`, so it wins deterministically without
 * `!important` and without altering the shared primitive.
 *
 * ## Reduced motion
 * As a loading indicator the shimmer is intentionally **exempt** from
 * reduced-motion suppression (design 10.5): it keeps animating under
 * `prefers-reduced-motion` because it reads neither the `--motion-*` duration
 * tokens nor `useMotionSafeProps` — only Tailwind's built-in `animate-pulse`.
 *
 * Token-only styling throughout (surfaces via the primitive's `bg-bg-elevated`;
 * `rounded-card/-hero/-pill` radius tokens) — no hex, no inline color styles.
 */
export interface SkeletonLoaderProps {
  /** Which content shape to mimic while data loads. */
  variant: "results" | "upload";
  /** Optional classes merged onto the loader root. */
  className?: string;
}

/**
 * Re-points every descendant `Skeleton`'s pulse to a 1.5s cycle (Req 17.1).
 * Higher specificity than `.animate-pulse`, so it overrides the 2s default.
 */
const SHIMMER_CADENCE = "[&_[data-slot=skeleton]]:[animation-duration:1.5s]";

/** Representative matched-keyword pill widths (varied to read as real tags). */
const MATCHED_PILL_WIDTHS = [
  "w-20",
  "w-24",
  "w-16",
  "w-28",
  "w-20",
  "w-24",
  "w-14",
  "w-20",
] as const;

/** Representative missing-keyword pill widths. */
const MISSING_PILL_WIDTHS = ["w-24", "w-20", "w-28", "w-16"] as const;

export function SkeletonLoader({ variant, className }: SkeletonLoaderProps) {
  return (
    <div
      data-slot="skeleton-loader"
      data-variant={variant}
      role="status"
      aria-busy="true"
      aria-label="Loading"
      className={cn(SHIMMER_CADENCE, className)}
    >
      <span className="sr-only">Loading…</span>
      {variant === "results" ? <ResultsSkeleton /> : <UploadSkeleton />}
    </div>
  );
}

/**
 * Mirrors the ATS Results success layout: gauge + label on the left, the two
 * score-breakdown bars on the right (stacked below the gauge under `lg`), then
 * the matched and missing keyword pill groups.
 */
function ResultsSkeleton() {
  return (
    <div aria-hidden className="mx-auto w-full max-w-7xl space-y-8">
      {/* Gauge (+ qualitative label) on the left; breakdown bars on the right. */}
      <div className="grid gap-8 lg:grid-cols-2">
        <div className="flex flex-col items-center gap-4">
          {/* Gauge circle — ≥120px on mobile, 160px from sm up. */}
          <Skeleton className="size-32 rounded-full sm:size-40" />
          <Skeleton className="h-6 w-28 rounded-pill" />
        </div>

        <div className="space-y-6">
          {/* Two labeled breakdown bars (similarity + keyword coverage). */}
          <div className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-full rounded-pill" />
          </div>
          <div className="space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-full rounded-pill" />
          </div>
        </div>
      </div>

      {/* Matched keywords: heading + pill row. */}
      <div className="space-y-4">
        <Skeleton className="h-5 w-44" />
        <div className="flex flex-wrap gap-2">
          {MATCHED_PILL_WIDTHS.map((width, index) => (
            <Skeleton
              key={`matched-${index}`}
              className={cn("h-7 rounded-pill", width)}
            />
          ))}
        </div>
      </div>

      {/* Missing keywords: heading + pill row. */}
      <div className="space-y-4">
        <Skeleton className="h-5 w-40" />
        <div className="flex flex-wrap gap-2">
          {MISSING_PILL_WIDTHS.map((width, index) => (
            <Skeleton
              key={`missing-${index}`}
              className={cn("h-7 rounded-pill", width)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * Mirrors the Upload page shape: heading/guidance lines, the drag-and-drop zone,
 * the job-description field (label + textarea + character count), and the
 * "Analyze Match" submit button.
 */
function UploadSkeleton() {
  return (
    <div aria-hidden className="mx-auto w-full max-w-3xl space-y-6">
      {/* Heading + guidance copy. */}
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-80" />
      </div>

      {/* Drop-zone. */}
      <Skeleton className="h-48 w-full rounded-hero" />

      {/* Job-description field: label, textarea, and right-aligned char count. */}
      <div className="space-y-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-32 w-full rounded-card" />
        <div className="flex justify-end">
          <Skeleton className="h-3 w-24" />
        </div>
      </div>

      {/* Submit button (≥44px tall touch target). */}
      <Skeleton className="h-11 w-full rounded-card" />
    </div>
  );
}
