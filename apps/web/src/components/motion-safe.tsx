"use client";

import { useReducedMotion, type MotionProps } from "framer-motion";

/**
 * Wraps Framer Motion props to respect `prefers-reduced-motion`.
 *
 * When the user prefers reduced motion (system setting), animations are
 * neutralized in two reinforcing ways:
 *
 *   - `animate` is forced to equal `initial`, so the element starts in its
 *     final-but-static state and has nothing to animate toward.
 *   - `transition` is forced to `{ duration: 0 }`, so even derived motion
 *     (variants, `whileHover`, `whileTap`, gesture-driven props) resolves
 *     instantly with no perceptible movement.
 *
 * Otherwise the input props are returned unchanged.
 *
 * `useReducedMotion()` returns `boolean | null`. The `null` SSR/unknown case
 * is treated as "no preference set" — props pass through untouched. This
 * matches the design.md rule that motion respect `prefers-reduced-motion`
 * without breaking server rendering or first-render output.
 *
 * The hook is generic so the inferred prop shape (e.g. `HTMLMotionProps<"h1">`,
 * `HTMLMotionProps<"div">`) survives the wrap; consumers don't lose
 * element-specific typing on the returned object.
 *
 * @example
 *   "use client";
 *   import { motion } from "framer-motion";
 *   import { useMotionSafeProps } from "@/components/motion-safe";
 *
 *   export function Hero() {
 *     const safe = useMotionSafeProps({
 *       initial: { opacity: 0, y: 8 },
 *       animate: { opacity: 1, y: 0 },
 *       transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] },
 *     });
 *     return <motion.h1 {...safe}>MatchLayer</motion.h1>;
 *   }
 */
export function useMotionSafeProps<T extends MotionProps>(props: T): T {
  const reduced = useReducedMotion();

  if (!reduced) {
    return props;
  }

  // The cast preserves the caller-supplied generic shape (e.g.
  // `HTMLMotionProps<"h1">`). We only override two fields that are part of
  // every `MotionProps` shape, so widening to the base type and back is safe.
  return {
    ...props,
    animate: props.initial,
    transition: { duration: 0 },
  } as T;
}
