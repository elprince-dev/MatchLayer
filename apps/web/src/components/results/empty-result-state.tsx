import { FileSearch } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Props for {@link EmptyResultState}.
 *
 * The content is **fixed copy** — there are no backend fields to pass. The
 * trigger condition
 * (`score_breakdown.similarity_component === 0 && keyword_coverage_component === 0`,
 * Req 12.5) is evaluated by the parent `results-view`; this component only
 * renders the empty-but-valid message and the recovery action.
 */
export interface EmptyResultStateProps {
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * The **degenerate-but-valid** ATS result surface (Req 12.5, 12.6, 12.7;
 * design Section 7.1 "EmptyResultState").
 *
 * Shown when a match was successfully returned but both score components are
 * zero — the case produced when a resume or job description had insufficient
 * extractable text. This is a **valid result, not an error** (Req 12.6), so it
 * is deliberately distinct from the shared {@link ErrorState}:
 *
 *   - **No `danger` token anywhere** (Req 12.7). The indicator uses neutral
 *     surface tokens (`bg-bg-elevated` + `border-border-strong`, icon in
 *     `text-text-muted`) so a sparse-but-valid outcome never reads as a
 *     failure. `ErrorState`, by contrast, carries a `danger`-tinted indicator
 *     and `role="alert"`.
 *   - Announced politely via `role="status"` (informational), not assertively.
 *   - A neutral `FileSearch` glyph (not an alert/warning icon), marked
 *     `aria-hidden` because the meaning is carried entirely by the text — the
 *     outcome is never conveyed by icon or color alone.
 *
 * It explains, in encouraging plain language, that not enough readable content
 * was available to produce a meaningful match, and offers a single
 * "Analyze another job" action routing to `/upload`.
 *
 * ## Why a `<p>` (not a heading) for the title
 * Like `ErrorState`, this region renders inside the results composition where
 * the owning page owns the heading outline. Hardcoding a heading here would
 * risk skipped levels / an h2-without-h1 violation (design Section 10 requires
 * sequential heading levels), so the strong line is a styled `<p>` and the
 * region is grouped via `role="status"`.
 *
 * No client-only features (no state, effects, or handlers) — the action is a
 * plain `next/link`, so the component needs no `"use client"` directive and
 * works in either a Server or Client context.
 */
export function EmptyResultState({
  className,
}: EmptyResultStateProps): React.JSX.Element {
  return (
    <div
      role="status"
      className={cn(
        "mx-auto flex max-w-md flex-col items-center gap-4 rounded-hero border border-border bg-bg-elevated p-8 text-center shadow-resting",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-12 items-center justify-center rounded-full border border-border-strong bg-bg-elevated text-text-muted"
      >
        <FileSearch className="size-6" />
      </span>

      <div className="space-y-2">
        <p className="text-lg font-semibold tracking-tight text-text">
          Not enough to analyze yet
        </p>
        <p className="text-sm text-text-muted">
          We couldn&apos;t find enough readable text in your resume and job
          description to produce a meaningful match. Try another file or paste a
          job description with more detail.
        </p>
      </div>

      <div className="pt-1">
        <Button asChild>
          <Link href="/upload">Analyze another job</Link>
        </Button>
      </div>
    </div>
  );
}
