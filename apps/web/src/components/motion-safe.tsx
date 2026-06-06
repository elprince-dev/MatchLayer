"use client";

import type * as React from "react";

import { motion, useReducedMotion, type MotionProps } from "framer-motion";

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

/**
 * Props for the {@link MotionSafe} wrapper.
 *
 * The surface mirrors the design's spec (design.md Section 7.3): the Framer
 * Motion prop set (`MotionProps` — `initial`, `animate`, `exit`, `transition`,
 * `variants`, `while*`, …) plus an `as` selector, `children`, and the
 * universal `className`. Element-specific attributes are intentionally not
 * widened per-tag; `MotionSafe` is a thin reduced-motion chokepoint for
 * entrance / reveal / layout containers, not a full polymorphic element.
 */
export interface MotionSafeProps extends MotionProps {
  /**
   * The intrinsic HTML element the underlying `motion` component renders.
   * Defaults to `"div"`.
   */
  as?: keyof React.JSX.IntrinsicElements;
  className?: string;
  children?: React.ReactNode;
}

/**
 * The single chokepoint that makes entrance / reveal / layout animations
 * respect `prefers-reduced-motion`.
 *
 * `MotionSafe` renders a Framer Motion element (`motion.div` by default, or
 * `motion[as]` for any other intrinsic tag) with its motion props funneled
 * through {@link useMotionSafeProps}. Under `prefers-reduced-motion: reduce`
 * that hook forces `animate` to equal `initial` and `transition` to
 * `{ duration: 0 }`, so the element renders in its final, static state
 * instantly — no fade-up, no count-up, no layout tween (design.md Section 6.5,
 * Section 7.3; Req 1.9, 15.4, 15.6, 16.11, 19.6). With no preference set, props
 * pass through untouched and animations play normally.
 *
 * Centralizing the reduced-motion decision here means individual animated
 * components never re-implement the check: they render through `MotionSafe`
 * and inherit compliance uniformly (Req 15.6, 16.11).
 *
 * **Exemptions (do NOT route through this wrapper):** loading / progress
 * indicators (e.g. the skeleton shimmer, the upload `ProgressBar`) and
 * focus-ring transitions must keep animating even under reduced motion
 * (Req 15.4, 19.6). Those are intentionally implemented outside `MotionSafe`
 * — it governs decorative/entrance motion, not feedback indicators.
 *
 * @example
 *   "use client";
 *   import { MotionSafe } from "@/components/motion-safe";
 *
 *   export function Hero() {
 *     return (
 *       <MotionSafe
 *         as="h1"
 *         initial={{ opacity: 0, y: 8 }}
 *         animate={{ opacity: 1, y: 0 }}
 *         transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
 *       >
 *         MatchLayer
 *       </MotionSafe>
 *     );
 *   }
 */
export function MotionSafe({ as = "div", ...motionProps }: MotionSafeProps) {
  // Funnel every motion prop through the shared hook so the reduced-motion
  // override lives in exactly one place. The hook returns the same shape it
  // receives, so `className`/`children` pass through alongside the motion
  // props.
  const safeProps = useMotionSafeProps(motionProps);

  // `motion` is indexed by tag name to obtain the corresponding motion
  // component (`motion.div`, `motion.h1`, …). The default `"div"` matches the
  // prop default. The cast bridges the dynamic string index to a concrete
  // component type; the spread props are a structural subset of any motion
  // element's accepted props (all element attributes are optional).
  const Component = motion[as as keyof typeof motion] as React.ComponentType<
    MotionProps & { className?: string; children?: React.ReactNode }
  >;

  return <Component {...safeProps} />;
}
