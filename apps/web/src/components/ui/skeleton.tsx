import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Skeleton primitive — shadcn `new-york` style, remapped to MatchLayer tokens.
 *
 * The base placeholder block: a token-colored surface (`bg-bg-elevated`) with a
 * pulse animation. It is the building block for the `SkeletonLoader`
 * compositions (task 2.2), which arrange these into gauge/bar/field shapes and
 * own the 1.5s shimmer cadence (Req 17.1).
 *
 * As a loading indicator it is exempt from reduced-motion suppression
 * (design 10.5) — the pulse uses Tailwind's built-in `animate-pulse`, not the
 * `--motion-*` tokens, so it keeps animating under `prefers-reduced-motion`.
 *
 * Not interactive, so no focus ring. Accepts every native `<div>` attribute
 * plus `className`.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("animate-pulse rounded-card bg-bg-elevated", className)}
      {...props}
    />
  );
}

export { Skeleton };
