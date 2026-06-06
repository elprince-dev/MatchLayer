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

/**
 * Format a byte count as a short, human-readable size string (Req 9.3, 16.3).
 *
 * Consumes `ResumeResponse.byte_size` (an integer count of stored bytes) and
 * renders the size shown in the {@link FilePreviewCard}, e.g. `"243 KB"` for a
 * 248,913-byte resume (matches design Section 8.4 wireframe + Section 5.1
 * fixtures).
 *
 * ## Threshold + rounding decisions (asserted by the task 6.5 edge tests)
 *
 * - **Binary base (1024), conventional `B`/`KB`/`MB` labels.** Sizes step at
 *   1024, not 1000. The labels use the familiar `KB`/`MB` rather than the
 *   pedantic `KiB`/`MiB` because this is a user-facing UI string, not a
 *   technical spec. Files are capped at 5 MB (Req 9.1), so `MB` is the largest
 *   unit ever needed.
 * - **Bytes (`n < 1024`):** integer + `" B"` — e.g. `1023 → "1023 B"`. No
 *   decimals; sub-KB sizes read most naturally as a raw count.
 * - **Kilobytes (`1024 ≤ n < 1024²`):** `n / 1024` rounded to the **nearest
 *   integer** + `" KB"` — e.g. `1024 → "1 KB"`, `248913 → "243 KB"`. Whole-KB
 *   matches the design wireframe (no decimal noise at this scale).
 * - **Megabytes (`n ≥ 1024²`):** `n / 1024²` rounded to **one decimal place**
 *   + `" MB"` — e.g. `5242880 → "5.0 MB"`, `4733120 → "4.5 MB"`. One decimal
 *   gives useful resolution near the 5 MB limit without false precision.
 * - **Rounding carry guard:** if KB rounding would reach 1024 (e.g. a value
 *   just under 1 MB rounds up), the result is promoted to `MB` so we never emit
 *   a nonsensical `"1024 KB"`.
 * - **Non-finite / non-positive input → `"0 B"`.** Guards against `NaN`,
 *   `Infinity`, and negative values so the UI never renders `"NaN"` or a
 *   negative size (the visual-acceptance gate forbids `NaN` in the DOM).
 *   `0 → "0 B"`.
 *
 * @param bytes - A byte count (typically `ResumeResponse.byte_size`).
 * @returns A human-readable size string such as `"0 B"`, `"243 KB"`, `"5.0 MB"`.
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }

  const KB = 1024;
  const MB = KB * 1024;

  if (bytes < KB) {
    return `${Math.round(bytes)} B`;
  }

  if (bytes < MB) {
    const kb = Math.round(bytes / KB);
    // Carry guard: a value just below 1 MB can round up to 1024 KB — promote
    // it to MB rather than emit "1024 KB".
    if (kb >= KB) {
      return `${(bytes / MB).toFixed(1)} MB`;
    }
    return `${kb} KB`;
  }

  return `${(bytes / MB).toFixed(1)} MB`;
}
