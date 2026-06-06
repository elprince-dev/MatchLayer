import * as React from "react";

/**
 * Skip-navigation link (Req 19.8; design Section 10.3, 10.4).
 *
 * Renders the **first focusable element** on a page: a link that lets
 * keyboard and screen-reader users bypass repeated chrome (glass nav, app
 * header, auth wordmark) and jump straight to the page's `<main>` landmark.
 * It is wired as the first child of each route-group layout so it precedes
 * every other interactive element in tab order.
 *
 * Behavior:
 *   - Visually hidden by default via `sr-only` so it never disrupts the
 *     visual design, then revealed on `:focus` (`focus:not-sr-only`) as a
 *     fixed, top-left chip — the canonical "visible-on-focus" skip link.
 *   - A plain in-page anchor to `#<targetId>`. Activating it moves focus to
 *     the `<main id={targetId}>` landmark, which carries `tabIndex={-1}` so
 *     the element is a programmatic focus target (not in the tab sequence).
 *     Because this is a pure anchor with no state, effects, or browser APIs,
 *     the component needs no `'use client'` directive and renders fine inside
 *     both Server- and Client-Component layouts (`conventions.md`).
 *
 * Styling is token-only (Req 21.2): surface/border/text tokens plus the
 * **2px branded focus ring** (`ring-2 ring-brand` + offset, design Section
 * 10.3) — `outline-none` is paired with the ring so focus is never removed
 * without a replacement.
 */
interface SkipNavProps {
  /**
   * The `id` of the `<main>` landmark this link moves focus to. Defaults to
   * `"main"`, which every layout uses for its main-content landmark.
   */
  targetId?: string;
  /** Visible link label. Defaults to "Skip to main content". */
  children?: React.ReactNode;
}

export function SkipNav({
  targetId = "main",
  children = "Skip to main content",
}: SkipNavProps): React.JSX.Element {
  return (
    <a
      href={`#${targetId}`}
      data-slot="skip-nav"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-card focus:border focus:border-border-strong focus:bg-bg-elevated focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-text focus:shadow-elevated focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-2 focus:ring-offset-bg"
    >
      {children}
    </a>
  );
}
