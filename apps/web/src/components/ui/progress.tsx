"use client";

import * as React from "react";
import * as ProgressPrimitive from "@radix-ui/react-progress";

import { cn } from "@/lib/utils";

/**
 * Progress primitive — shadcn `new-york` style on Radix `Progress`, remapped to
 * MatchLayer tokens. Composed by the Upload `ProgressBar` (determinate, 0–100%).
 *
 * A progress bar is not a focusable control, so it carries no focus ring. As a
 * loading/progress indicator it is exempt from reduced-motion suppression
 * (design 10.5): the indicator translate transition uses a fixed duration here
 * rather than the `--motion-*` tokens, so it animates even under
 * `prefers-reduced-motion`.
 *
 * Token mapping: track → `bg-bg-elevated`; filled indicator → `bg-brand`.
 * Accepts every Radix `Progress.Root` prop plus `className`.
 */
function Progress({
  className,
  value,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root>) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-pill bg-bg-elevated",
        className,
      )}
      {...props}
    >
      <ProgressPrimitive.Indicator
        data-slot="progress-indicator"
        className="h-full w-full flex-1 bg-brand transition-transform duration-300 ease-out"
        style={{ transform: `translateX(-${100 - (value ?? 0)}%)` }}
      />
    </ProgressPrimitive.Root>
  );
}

export { Progress };
