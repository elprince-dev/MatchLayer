import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class names safely.
 *
 * `clsx` resolves conditionals/arrays into a single space-separated string;
 * `tailwind-merge` then strips conflicting Tailwind utilities so the last
 * class wins (e.g. `cn("p-2", isLarge && "p-4")` → `"p-4"`).
 *
 * Used by every shadcn primitive and any consumer that needs to merge a
 * `className` prop onto a base set of utilities.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
