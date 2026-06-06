import { Check, Plus } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import type { Keyword } from "@matchlayer/shared-types";

import { cn } from "@/lib/utils";

/**
 * KeywordTag — a single matched/missing keyword rendered as a pill
 * (design Section 7.1 "KeywordTag", Section 10.2; Req 11.3, 11.4, 11.6, 16.4).
 *
 * ## The light-mode contrast mitigation (design Section 10.2, mandate #1)
 * `success`/`warning` used as *foreground* on a light surface fail WCAG AA
 * (measured 2.41:1 and 2.04:1 in Section 10.1). Colored-text pills would
 * therefore be unreadable in Light_Mode. The binding mitigation — applied
 * uniformly in both themes for consistency — is:
 *
 *   - a **tinted background fill** at low opacity (`bg-success/15` /
 *     `bg-warning/15`),
 *   - a **full-token border** (`border-success` / `border-warning`) carrying
 *     the semantic hue, and
 *   - the **label in the primary `text` token** — never in the status color.
 *
 * This keeps the success/warning hue as the semantic signal while the label
 * itself is high-contrast (`text` ≥ 7:1 in both themes), so the pill passes AA
 * in Light_Mode and Dark_Mode alike. This is the exact treatment the Section
 * 9.3 / 3.6 tests assert, so it must not regress to colored text.
 *
 * A small leading icon (`✓` matched / `+` missing) mirrors the Section 8.1
 * wireframe and reinforces the matched-vs-missing distinction without relying
 * on color alone (Req 11.6). It is decorative (`aria-hidden`) — the meaning is
 * already carried by the owning {@link KeywordSection}'s heading and the visible
 * term.
 *
 * ## Data contract (Req 16.4, 20.1)
 * Consumes a single {@link Keyword} (`{ term, weight }`) straight from the
 * generated `@matchlayer/shared-types`. `term` is the visible label; `weight`
 * is surfaced only as a hover/tooltip `title` (its display is optional —
 * ordering is **not** this component's job, the parent passes keywords already
 * sorted by descending weight).
 *
 * Static and non-interactive, so it is a Server Component (no `"use client"`)
 * and carries no focus ring.
 */
const keywordTagVariants = cva(
  // Uniform height + consistent padding/gap so every pill in a group lines up
  // (Req 11.6). Label is always the primary `text` token — never the status
  // color (Section 10.2).
  "inline-flex h-7 items-center gap-1.5 rounded-pill border px-3 text-sm font-medium text-text",
  {
    variants: {
      variant: {
        // Tinted fill + full-token border; the hue is the semantic signal.
        success: "border-success bg-success/15",
        warning: "border-warning bg-warning/15",
      },
    },
    defaultVariants: {
      variant: "success",
    },
  },
);

/** The decorative leading glyph per variant (matched check / missing plus). */
const VARIANT_ICON = {
  success: Check,
  warning: Plus,
} as const;

export interface KeywordTagProps extends VariantProps<
  typeof keywordTagVariants
> {
  /** One analyzed keyword from the API contract: `{ term, weight }`. */
  keyword: Keyword;
  /** `success` for matched keywords, `warning` for missing ones. */
  variant: "success" | "warning";
  /** Composition hook — extends (never replaces) the base pill utilities. */
  className?: string;
}

export function KeywordTag({
  keyword,
  variant,
  className,
}: KeywordTagProps): React.JSX.Element {
  const Icon = VARIANT_ICON[variant];

  return (
    <span
      data-slot="keyword-tag"
      data-variant={variant}
      // `weight` is surfaced as an unobtrusive tooltip only (optional display).
      title={`Weight: ${keyword.weight.toFixed(2)}`}
      className={cn(keywordTagVariants({ variant }), className)}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span>{keyword.term}</span>
    </span>
  );
}
