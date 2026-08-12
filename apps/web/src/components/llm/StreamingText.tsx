import * as React from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Props for {@link StreamingText}.
 *
 * Deliberately minimal and local: this component does not import from
 * `lib/llm/` (built by a sibling task). The panels (task 11.5) own the
 * `use-llm-stream` hook and feed this component the accumulated display
 * text plus a `streaming` flag.
 */
export interface StreamingTextProps {
  /**
   * The accumulated display-only text extracted from the SSE deltas so far
   * (or the final text once the terminal event resolves). Always rendered as
   * **plain text** — never parsed, never injected as HTML (Req 17.3).
   */
  text: string;
  /**
   * True while the LLM request is in flight (stream open, terminal event not
   * yet resolved). Drives the pre-first-delta skeleton (Req 17.6) and the
   * screen-reader progress announcement.
   */
  streaming: boolean;
  /** Accessible name for the streamed region. */
  label?: string;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * Re-points every descendant `Skeleton`'s pulse to the design system's 1.5s
 * shimmer cadence (same mechanism as `SkeletonLoader` — higher specificity
 * than `.animate-pulse`, no `!important`).
 */
const SHIMMER_CADENCE = "[&_[data-slot=skeleton]]:[animation-duration:1.5s]";

/**
 * Representative paragraph-line widths so the placeholder mirrors the shape
 * of the streamed prose it is standing in for (design.md: "skeletons that
 * match content shape", Req 17.6).
 */
const SKELETON_LINE_WIDTHS = [
  "w-full",
  "w-11/12",
  "w-full",
  "w-4/5",
  "w-2/3",
] as const;

/**
 * StreamingText — progressive plain-text rendering of LLM delta events
 * (Req 17.2, 17.3, 17.6; design §Frontend `components/llm/`).
 *
 * ## Rendering safety (Req 17.3, security.md "LLM output sanitization")
 * The streamed content is interpolated as a **React text child** — React
 * escapes it, so model output can never inject markup or script. There is no
 * `dangerouslySetInnerHTML`, no HTML parsing, and no markdown pipeline here
 * (`react-markdown` is not a project dependency; plain text is the sanctioned
 * rendering mode). `whitespace-pre-wrap` preserves the model's line breaks
 * without interpreting anything.
 *
 * ## Loading state (Req 17.6)
 * While `streaming` is true and no delta has arrived yet (`text === ""`),
 * paragraph-shaped skeleton lines render in place of the text so the panel
 * never shows a blank region or a spinner-only state (design.md
 * anti-patterns). The first delta swaps the skeleton for real text in the
 * same box, so there is no layout jump class change.
 *
 * ## Accessibility (design.md "Form errors announced via aria-live")
 * Announcing every token to a screen reader would be unusable noise, so the
 * live region is a visually hidden `role="status"` (implicit
 * `aria-live="polite"`) that announces only the state *transitions* —
 * "Generating AI response…" when the stream opens and "AI response ready"
 * when it settles. The visible text container carries `aria-busy` while
 * streaming so assistive tech knows the region is still updating.
 *
 * This is presentation-only (no state, no effects), so no `"use client"`
 * directive — it renders inside the client-side panels of task 11.5.
 */
export function StreamingText({
  text,
  streaming,
  label = "AI response",
  className,
}: StreamingTextProps): React.JSX.Element {
  const showSkeleton = streaming && text === "";

  return (
    <div
      data-slot="streaming-text"
      className={cn("min-w-0", SHIMMER_CADENCE, className)}
    >
      {/* Screen-reader progress announcements: transitions only, not tokens. */}
      <span role="status" className="sr-only">
        {streaming
          ? "Generating AI response…"
          : text !== ""
            ? "AI response ready"
            : ""}
      </span>

      {showSkeleton ? (
        <div aria-hidden="true" className="space-y-2.5 py-1">
          {SKELETON_LINE_WIDTHS.map((width, index) => (
            <Skeleton key={index} className={cn("h-4", width)} />
          ))}
        </div>
      ) : (
        <p
          aria-busy={streaming}
          aria-label={label}
          className="text-sm leading-relaxed whitespace-pre-wrap break-words text-text"
        >
          {text}
        </p>
      )}
    </div>
  );
}
