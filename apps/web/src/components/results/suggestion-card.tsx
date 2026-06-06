import { CircleCheck } from "lucide-react";
import * as React from "react";

import type { Suggestion } from "@matchlayer/shared-types";

import { cn } from "@/lib/utils";

/**
 * Props for {@link SuggestionCard}.
 *
 * The card consumes a single {@link Suggestion} — the curated re-export of the
 * backend `SuggestionOut` contract, which is **exactly** `{ keyword, text }`
 * (`packages/shared-types`). There is deliberately **no `title` and no
 * `priority`** prop: the backend supplies neither, and the redesign never
 * invents fields the contract does not provide (Req 11.5, 16.5, 20.3;
 * design Section 7.1 "SuggestionCard"). Adding such props here would be a
 * contract violation, so the prop surface is just the suggestion plus the
 * universal `className` composition hook.
 */
export interface SuggestionCardProps {
  /** A single backend suggestion — `{ keyword, text }`, nothing more. */
  suggestion: Suggestion;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * One improvement suggestion (or the single affirmative confirmation) on the
 * ATS Results page (Req 11.5, 12.4, 16.5, 20.3; design Section 7.1
 * "SuggestionCard").
 *
 * ## Two states, driven entirely by `keyword`
 *
 *   - **Improvement** (`keyword` non-empty): the suggestion addresses a missing
 *     keyword. A small `warning`-toned keyword label sits above the body so the
 *     card is visually associated with that term, then `text` renders the
 *     guidance. Ordering across cards is owned by the parent (the API already
 *     returns suggestions by descending missing-keyword weight).
 *   - **Affirmative** (`keyword` empty): the single positive suggestion the
 *     scorer emits when nothing is missing (design Section 5.4 / Req 12.4). It
 *     renders in a `success` style — distinct from improvement cards — with a
 *     check indicator and **no** missing-keyword label, so a good result reads
 *     as encouragement rather than a gap to fix.
 *
 * ## Token & contrast treatment (design Section 10.2)
 * The keyword label and the affirmative surface use the mandated **tinted-fill
 * + full-token border + primary-`text` label** pattern rather than colored
 * body text: `success`/`warning` as foreground fail light-mode AA (2.41/2.04),
 * so the hue carries semantic signal via fill/border while the readable copy
 * stays in `text`/`text-muted` (≥7:1 both themes). The check icon is a
 * graphical object and is `aria-hidden`, so meaning is never conveyed by color
 * alone. All styling is token-only — no hex, no inline color.
 *
 * ## Motion
 * This is a **presentational** component. The staggered fade-up entrance
 * (400ms total / 100ms between, snapping to final state under
 * `prefers-reduced-motion` — Req 11.7) is orchestrated by the parent
 * `results-view` (task 4.1), which wraps each card through the shared
 * `MotionSafe` chokepoint. Keeping the card itself motion-free means it has no
 * client-only dependencies and stays trivially composable.
 */
export function SuggestionCard({
  suggestion,
  className,
}: SuggestionCardProps): React.JSX.Element {
  // The affirmative suggestion is identified solely by an empty `keyword`
  // (`.trim()` guards against incidental whitespace). Everything else is an
  // improvement suggestion tied to a specific missing term.
  const keyword = suggestion.keyword.trim();
  const isAffirmative = keyword.length === 0;

  if (isAffirmative) {
    return (
      <article
        className={cn(
          "flex items-start gap-3 rounded-xl border border-success/30 bg-success/10 p-6 shadow-resting",
          className,
        )}
      >
        <CircleCheck
          aria-hidden="true"
          className="mt-0.5 size-5 shrink-0 text-success"
        />
        <p className="text-sm leading-relaxed text-text">{suggestion.text}</p>
      </article>
    );
  }

  return (
    <article
      className={cn(
        "flex flex-col gap-3 rounded-xl border border-border bg-bg-elevated p-6 shadow-resting",
        className,
      )}
    >
      <span className="inline-flex w-fit items-center rounded-pill border border-warning bg-warning/15 px-2.5 py-0.5 text-xs font-medium text-text">
        {keyword}
      </span>
      <p className="text-sm leading-relaxed text-text">{suggestion.text}</p>
    </article>
  );
}
