"use client";

import { CircleAlert } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * A single recovery action for an {@link ErrorState}.
 *
 * Exactly one of `onClick` / `href` is expected per call site:
 *   - `onClick` → an in-place recovery (e.g. **[Retry]** re-runs a query
 *     without navigating away — Req 17.6).
 *   - `href` → a navigation recovery (rendered as a `next/link`).
 *
 * `label` is human copy the caller has already mapped to safe text.
 */
export interface ErrorStateAction {
  label: string;
  onClick?: () => void;
  href?: string;
}

/**
 * Props for the shared {@link ErrorState}.
 *
 * The component is deliberately **dumb**: it receives only mapped, safe copy
 * (`title` + `message`) and recovery affordances. It is given **no** raw error
 * object, status code, or RFC 7807 envelope, so there is structurally nothing
 * for it to leak (Req 17.4, security.md "no secrets/stack traces in error
 * responses").
 *
 * Copy contract (Req 17.3 — enforced by the caller / mapping layer, not at
 * runtime so a contract bug surfaces in tests rather than being silently
 * truncated):
 *   - `title`   ≤ 60 chars.
 *   - `message` ≤ 200 chars, plain language, no technical jargon.
 */
export interface ErrorStateProps {
  /** Short headline, ≤ 60 chars. Plain language. */
  title: string;
  /** Explanation, ≤ 200 chars. Plain language, no jargon. */
  message: string;
  /** Primary recovery action (retry callback or navigation link). */
  action?: ErrorStateAction;
  /** Optional secondary recovery link (e.g. `/upload`). */
  secondaryHref?: string;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * Shared, cross-screen error surface (Req 16.6, 17.3–17.7; design Section 7.3
 * "ErrorState", Error Handling, Section 10.2).
 *
 * Used by both the ATS_Results_Page (5xx / network / timeout / 404) and the
 * Upload_Page (transmission failure, extraction failure) so error UX is
 * identical everywhere.
 *
 * ## What it renders
 *   - A **danger-token indicator only** — a Lucide icon in a low-opacity
 *     tinted well (`bg-danger/10` + `border-danger/30`, icon `text-danger`).
 *     Per Section 10.2 mitigation #3, `danger` is an *indicator* color, not a
 *     body-text color: `danger on bg` is only 3.76:1, which clears the ≥3:1
 *     graphical/large threshold but fails the 4.5:1 body-text bar in light
 *     mode. The icon is `aria-hidden` because meaning is carried by the text,
 *     so the error is never conveyed by color alone.
 *   - `title` in the primary `text` token (≥7:1 both themes).
 *   - `message` in `text-muted` (≥7:1 both themes) — the explanatory copy.
 *   - At least one recovery action (Req 17.3): the `action` button and/or the
 *     `secondaryHref` link. If a caller supplies **neither**, we fall back to
 *     the universal `/upload` recovery target so a recovery path is *always*
 *     present.
 *
 * ## What it deliberately never renders
 *   - Technical error codes, stack traces, internal identifiers, or any
 *     RFC 7807 `type` / `request_id` field (Req 17.5). It has no prop through
 *     which such data could even be passed.
 *
 * ## Why a `<p>` (not a heading) for the title
 * The title is the region's strong line, but the component is rendered in
 * varying contexts (full-page on a fetch error, inline inside the upload
 * widget). Hardcoding an `<h2>` here would risk skipped heading levels and an
 * h2-without-h1 violation (design Section 10 requires sequential levels). The
 * owning page keeps responsibility for the heading outline; this region is
 * announced as a whole via `role="alert"`.
 *
 * Marked `"use client"` because the primary `action` attaches an `onClick`
 * handler (the in-place retry path).
 */
export function ErrorState({
  title,
  message,
  action,
  secondaryHref,
  className,
}: ErrorStateProps): React.JSX.Element {
  // Guarantee ≥1 recovery action (Req 17.3). When the caller provides neither
  // a primary action nor a secondary link, route to the universal recovery
  // target (`/upload`) rather than leaving the user stranded.
  const fallbackHref =
    action === undefined && secondaryHref === undefined ? "/upload" : undefined;

  return (
    <div
      role="alert"
      className={cn(
        "mx-auto flex max-w-md flex-col items-center gap-4 text-center",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-12 items-center justify-center rounded-full border border-danger/30 bg-danger/10 text-danger"
      >
        <CircleAlert className="size-6" />
      </span>

      <div className="space-y-2">
        <p className="text-lg font-semibold tracking-tight text-text">
          {title}
        </p>
        <p className="text-sm text-text-muted">{message}</p>
      </div>

      {(action !== undefined ||
        secondaryHref !== undefined ||
        fallbackHref !== undefined) && (
        <div className="flex flex-wrap items-center justify-center gap-3 pt-1">
          {action !== undefined &&
            (action.href !== undefined ? (
              <Button asChild>
                <Link href={action.href}>{action.label}</Link>
              </Button>
            ) : (
              <Button type="button" onClick={action.onClick}>
                {action.label}
              </Button>
            ))}

          {secondaryHref !== undefined && (
            <Button asChild variant="ghost">
              <Link href={secondaryHref}>
                {recoveryLinkLabel(secondaryHref)}
              </Link>
            </Button>
          )}

          {fallbackHref !== undefined && (
            <Button asChild variant="ghost">
              <Link href={fallbackHref}>{recoveryLinkLabel(fallbackHref)}</Link>
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Derive friendly link copy from a recovery `href` so the secondary link reads
 * as an action without the caller having to pass a label (the design's prop
 * shape is `secondaryHref: string` only).
 *
 * Strips any query/hash, takes the last path segment, and humanizes it:
 *   - `/upload`      → "Go to upload"
 *   - `/matches/123` → "Go to 123"  (not a documented target, but safe)
 *   - `/`            → "Go to the homepage"
 */
function recoveryLinkLabel(href: string): string {
  const path = href.split(/[?#]/)[0] ?? href;
  const segment = path.split("/").filter(Boolean).pop();
  if (segment === undefined) {
    return "Go to the homepage";
  }
  return `Go to ${segment.replace(/-/g, " ")}`;
}
