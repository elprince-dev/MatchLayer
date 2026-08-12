import * as React from "react";

import { Info } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Props for {@link FallbackBadge}.
 *
 * Takes the envelope's machine-readable flag directly (Req 9.2 / 17.4) —
 * matching `LLMResultEnvelope.is_fallback` from `@matchlayer/shared-types` —
 * rather than the whole envelope, so the badge stays reusable across the
 * three feature envelopes without a generic parameter.
 */
export interface FallbackBadgeProps {
  /**
   * The envelope's `is_fallback` field. The badge renders **iff** this is
   * `true` (Req 17.4: content not identified as a Fallback_Response must not
   * carry the label).
   */
  isFallback: boolean;
  /** Composition hook — extends (never replaces) the base pill styling. */
  className?: string;
}

/**
 * FallbackBadge — the "Generated without AI assistance" label (Req 17.4;
 * design §Frontend: "`FallbackBadge` shows exactly when `is_fallback ===
 * true`").
 *
 * ## Exactness contract
 * The show/hide decision lives *inside* the component (`isFallback` gate)
 * instead of at the call sites, so the "exactly when" rule is enforced in one
 * place: passing `false` renders nothing, passing `true` renders the label.
 * Task 11.7's both-branches test targets this single gate.
 *
 * ## Presentation
 * A calm pill (`rounded-pill`, per design.md badge radius) on the elevated
 * surface with muted text — the label is honest metadata, not an alarm, so it
 * uses neutral tokens rather than `warning`/`danger`. The icon is
 * `aria-hidden` because the text carries the full meaning; the badge itself
 * is plain static text, so no live region or role is needed beyond reading
 * order.
 */
export function FallbackBadge({
  isFallback,
  className,
}: FallbackBadgeProps): React.JSX.Element | null {
  if (!isFallback) {
    return null;
  }

  return (
    <span
      data-slot="fallback-badge"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border border-border-strong bg-bg-elevated px-2.5 py-0.5 text-xs font-medium text-text-muted",
        className,
      )}
    >
      <Info aria-hidden="true" className="size-3.5 shrink-0" />
      Generated without AI assistance
    </span>
  );
}
