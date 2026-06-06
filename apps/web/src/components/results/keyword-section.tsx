import * as React from "react";

import type { Keyword } from "@matchlayer/shared-types";

import { cn } from "@/lib/utils";

import { KeywordTag } from "./keyword-tag";

/**
 * KeywordSection — a labeled group of {@link KeywordTag} pills for either the
 * matched or the missing keyword set (design Section 7.1 "KeywordSection";
 * Req 11.3, 11.4, 11.6, 12.2, 12.3, 12.7, 16.4).
 *
 * ## Ordering (Req 11.3, 11.4)
 * The backend returns `matched_keywords` / `missing_keywords` **already sorted
 * by descending weight**. This component renders them in the order received and
 * performs no sorting of its own — the data layer owns ordering.
 *
 * ## Empty state (Req 12.2, 12.3, 12.7)
 * An empty keyword array is a **valid result**, not an error. When `keywords`
 * is empty the section renders the caller-supplied `emptyMessage` instead of a
 * blank list. The two sections pass distinct copy:
 *   - matched-empty → "no matching keywords were found" (Req 12.2),
 *   - missing-empty → "the resume covers the analyzed keywords" (Req 12.3).
 *
 * The empty message uses neutral text tokens (`text-muted`) and the section's
 * own neutral/success/warning treatment — it **never** uses the `danger` token,
 * so a sparse-but-valid result never reads as an error (Req 12.7).
 *
 * ## Layout (Req 11.6, design Section 8.1)
 * The pill list is a `flex flex-wrap` with `gap-2` (the design's "keyword pill
 * gap"), so tags wrap onto multiple rows and **never** cause horizontal
 * scrolling at any viewport width.
 *
 * Static and non-interactive, so it is a Server Component.
 */
export interface KeywordSectionProps {
  /** Section heading, e.g. "Matched keywords" / "Missing keywords". */
  title: string;
  /**
   * The keywords to render, in the order received from the API (already
   * weight-descending). May be empty, in which case `emptyMessage` is shown.
   */
  keywords: Keyword[];
  /** Pill treatment: `success` for matched, `warning` for missing. */
  variant: "success" | "warning";
  /**
   * Defined copy shown when `keywords` is empty (distinct per section). Must
   * read as a valid outcome — never an error.
   */
  emptyMessage: string;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

export function KeywordSection({
  title,
  keywords,
  variant,
  emptyMessage,
  className,
}: KeywordSectionProps): React.JSX.Element {
  const isEmpty = keywords.length === 0;

  return (
    <section
      data-slot="keyword-section"
      data-variant={variant}
      className={cn("space-y-4", className)}
    >
      <h3 className="text-lg font-semibold tracking-tight text-text">
        {title}
      </h3>

      {isEmpty ? (
        // Neutral copy — never the `danger` token (Req 12.7).
        <p className="text-sm text-text-muted">{emptyMessage}</p>
      ) : (
        // `gap-2` wrapped list — wraps rather than scrolling horizontally.
        <ul className="flex list-none flex-wrap gap-2 p-0">
          {keywords.map((keyword, index) => (
            <li key={`${keyword.term}-${index}`}>
              <KeywordTag keyword={keyword} variant={variant} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
