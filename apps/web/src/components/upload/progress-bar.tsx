"use client";

import * as React from "react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/**
 * ProgressBar — the Upload page's determinate transmission indicator
 * (design Section 7.2 "ProgressBar"; Req 9.5, 17.2).
 *
 * ## What it renders
 * A determinate, animated bar reusing the `ui/progress.tsx` primitive (task 1.5)
 * paired with a numeric percentage readout. The percentage uses `font-mono` +
 * `tabular-nums` so the digits never reflow as the value climbs 0 → 100
 * (design typography: tabular figures for numeric readouts). An optional `label`
 * (e.g. "uploading…") renders below the bar to give the progress context, per
 * the Section 8.4 wireframe.
 *
 * ## Reduced-motion exemption (deliberate)
 * This is a loading/progress **indicator**, so it is **exempt** from the
 * `prefers-reduced-motion` suppression that gates decorative motion (design
 * Section 6.5; Req 15.4). It is therefore **not** wrapped in `MotionSafe` and is
 * **not** gated behind the `--motion-*` duration tokens. The animated fill comes
 * entirely from the underlying `Progress` indicator, whose translate transition
 * uses a fixed `duration-300` rather than a motion token — so the bar keeps
 * animating even when the user has reduced motion enabled. Conveying upload
 * progress is functional feedback, not ornament.
 *
 * ## Client Component
 * The composed `ui/progress.tsx` primitive is a `'use client'` Radix component,
 * and `ProgressBar` is only ever shown mid-transmission inside the client
 * `UploadWidget` (task 6.3). It carries the `'use client'` directive to follow
 * the primitive it wraps.
 *
 * ## Accessibility
 * The underlying Radix `Progress.Root` exposes `role="progressbar"` with
 * `aria-valuenow`/`aria-valuemax`, so the value is announced to assistive tech;
 * it is given an accessible name via `aria-label` (the `label` when provided,
 * else a sensible default). The visible percentage text is `aria-hidden` to
 * avoid a duplicate announcement of the same value.
 *
 * ## Styling
 * Token-only Tailwind throughout (no hex, no inline color styles): the bar's
 * track/fill tokens come from the primitive, and the readout/label use the
 * `text`/`text-muted` foreground tokens — so it renders correctly in both
 * Light_Mode and Dark_Mode.
 */
export interface ProgressBarProps {
  /** Completion percentage, 0–100. Values are clamped and rounded for display. */
  value: number;
  /** Optional contextual caption rendered below the bar (e.g. "uploading…"). */
  label?: string;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/** Clamp an arbitrary number into a 0–100 integer percentage. */
function toPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

export function ProgressBar({
  value,
  label,
  className,
}: ProgressBarProps): React.JSX.Element {
  const percent = toPercent(value);

  return (
    <div className={cn("w-full space-y-1.5", className)}>
      <div className="flex items-center gap-3">
        <Progress
          value={percent}
          aria-label={label ?? "Upload progress"}
          className="flex-1"
        />
        <span
          aria-hidden="true"
          className="font-mono text-sm tabular-nums text-text-muted"
        >
          {percent}%
        </span>
      </div>

      {label ? <p className="text-xs text-text-muted">{label}</p> : null}
    </div>
  );
}
