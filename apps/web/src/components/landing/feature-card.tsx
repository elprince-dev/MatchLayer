import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Props for {@link FeatureCard} (design Section 7.3 "FeatureCard"; Req 4.1,
 * 4.6, 5.2, 5.3, 5.5, 16.10).
 *
 * All content is author-supplied on the marketing page — there are **no
 * backend fields** here (this is a presentational marketing card, not bound to
 * the API contract). The `title` (≤40 chars) and `description` (≤120 chars)
 * length ceilings come from Req 4.1; they are a content-authoring constraint
 * documented here rather than enforced by truncation, so a too-long string is
 * caught in review instead of being silently clipped (same approach as
 * `ErrorState`'s ≤60 / ≤200 copy bounds).
 */
export interface FeatureCardProps {
  /**
   * A Lucide icon **component** (not an element), rendered in the card's icon
   * well — e.g. `icon={ShieldCheck}`. Typed as `LucideIcon` so callers pass the
   * icon by reference, matching {@link import("../upload/file-preview-card")}.
   */
  icon: LucideIcon;
  /** Feature name. Kept to ≤40 chars (Req 4.1). */
  title: string;
  /** One-line feature blurb. Kept to ≤120 chars (Req 4.1). */
  description: string;
  /**
   * When present, marks this card as a **Roadmap_Feature** — a planned, not-yet
   * available capability — and renders the matching non-interactive label pill
   * (Req 5.2, 5.3). Omit entirely for a current MVP capability.
   */
  badge?: "Coming soon" | "Planned";
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * FeatureCard — a single feature tile on the Landing_Page features grid
 * (design Section 7.3 "FeatureCard"; Req 4.1, 4.6, 5.2, 5.3, 5.5, 16.10).
 *
 * Renders a Lucide icon, a short title, a short description, and — only when a
 * `badge` is supplied — a "Coming soon" / "Planned" roadmap label.
 *
 * ## Honesty contract (Req 5.2, 5.3, 5.5) — the load-bearing rule
 * A `badge` marks a feature that does **not** exist in the current MVP. To
 * avoid misleading a visitor into thinking a planned capability is usable now,
 * the card renders **no enabled control of any kind**:
 *
 *   - The badge is a **non-interactive `<span>` pill**, never a button, link,
 *     or toggle (Req 5.5).
 *   - The **whole card is non-interactive** — a plain `<div>`, not a link or
 *     button. It is presentational marketing content, so there is nothing to
 *     activate whether the feature is current or roadmap. This keeps a roadmap
 *     card from ever exposing an affordance implying availability.
 *
 * Because the card has no state, effects, or event handlers and the hover
 * affordance is pure CSS, it is a **Server Component** (no `"use client"`).
 *
 * ## Hover affordance (Req 4.6, design Section 7.3, 4.7–4.8)
 * On hover the card raises its shadow (`shadow-resting` → `shadow-elevated`)
 * and highlights its border (`border-strong` → `brand`), transitioned over the
 * `--motion-micro` (200ms) duration with the `--motion-ease`
 * (ease-out-exponential) curve. Driving the duration from the token rather than
 * a hardcoded `duration-200` means the `@media (prefers-reduced-motion: reduce)`
 * override in `globals.css` (which re-points `--motion-micro` to `0ms`) makes
 * the state change apply **instantly** — satisfying the "no hover animation
 * under reduced motion" rule (Req 4.9) with no extra JS.
 *
 * ## Badge pill treatment (light-mode contrast mitigation)
 * The pill reuses the binding {@link import("../results/keyword-tag")} pattern
 * (design Section 10.2): a **tinted fill** (`bg-brand/15`) + a **full-token
 * border** (`border-brand`) + the label in the **primary `text` token** —
 * never colored text. The label being primary `text` keeps it ≥7:1 in both
 * themes, so the pill passes WCAG AA where colored-on-tint text would fail
 * Light_Mode. A **brand**-tinted (not `success`/`warning`/`danger`) hue is used
 * deliberately: a roadmap label is forward-looking and informational, so it
 * reads as on-brand rather than borrowing the matched/missing/error semantics
 * those status tokens carry elsewhere in the app.
 *
 * ## Layout / responsiveness
 * The card fills its grid cell (`h-full w-full`, `flex-col`) so it composes
 * cleanly however the parent sizes the column. The **responsive features grid
 * itself — 1 column <640px, 2 columns 640–1024px, 4 columns >1024px (Req 4.1)
 * — is the parent section's responsibility** (task 8.7), not this card's; the
 * card only guarantees it stretches to whatever cell it is placed in.
 *
 * Heading level: the title is an `<h3>`, the correct level under the page
 * `<h1>` (hero) → `<h2>` (features section) outline the marketing page
 * assembles (sequential heading levels — design Section 10.3).
 */
export function FeatureCard({
  icon: Icon,
  title,
  description,
  badge,
  className,
}: FeatureCardProps): React.JSX.Element {
  return (
    <div
      data-slot="feature-card"
      className={cn(
        // Fill the parent-owned grid cell; the grid columns are the parent's job.
        "flex h-full w-full flex-col gap-4 rounded-hero border border-border-strong bg-bg-elevated p-6 shadow-resting",
        // Hover affordance (Req 4.6): elevate shadow + highlight border over the
        // 200ms micro token with the ease-out curve. Token-driven duration means
        // reduced-motion (→ 0ms) makes this instant with no animation.
        "transition-[box-shadow,border-color] duration-[var(--motion-micro)] ease-[var(--motion-ease)]",
        "hover:border-brand hover:shadow-elevated",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-10 shrink-0 items-center justify-center rounded-card border border-border bg-bg text-brand"
      >
        <Icon className="size-5" />
      </span>

      <div className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-base font-semibold tracking-tight text-text">
            {title}
          </h3>
          {badge ? (
            <span
              data-slot="feature-card-badge"
              // Non-interactive roadmap label — never a control (Req 5.5).
              // Tinted-fill + full-token border + primary `text` label so it
              // passes AA in both themes (design Section 10.2).
              className="inline-flex shrink-0 items-center rounded-pill border border-brand bg-brand/15 px-2.5 py-0.5 text-xs font-medium text-text"
            >
              {badge}
            </span>
          ) : null}
        </div>
        <p className="text-sm text-text-muted">{description}</p>
      </div>
    </div>
  );
}
