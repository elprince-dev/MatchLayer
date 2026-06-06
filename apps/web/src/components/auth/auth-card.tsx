import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Centered-card container shared by every Auth_Page (§14.1).
 *
 * Owns the card chrome so each individual auth page renders only its form:
 *
 *   - `max-w-md` (448px) — the design-system width for auth forms
 *     (`design.md` Spacing & layout: "max-w-md (448px) for auth forms").
 *   - `rounded-2xl` — the design-system radius for "hero cards and feature
 *     surfaces"; auth uses it intentionally to match the §14.1 spec.
 *   - `border border-border-strong` over `bg-bg-elevated` — restrained
 *     elevation that reads in both themes via the brand tokens defined in
 *     `apps/web/src/app/globals.css`.
 *   - Layered soft shadow — the exact two-shadow stack from `design.md`
 *     ("Layer two shadows for depth: shadow-[0_1px_2px_rgba(0,0,0,0.04),
 *     0_8px_24px_rgba(0,0,0,0.08)]"). This is the only place hex-style values
 *     appear, and they're sourced verbatim from the design doc.
 *   - `p-8` — design-system component padding for cards (`design.md` Spacing
 *     & layout: "Component padding `p-6` or `p-8` for cards"); the wider
 *     value is chosen to give form rows comfortable breathing room.
 *
 * Server component by default — no state, no effects, no browser APIs. The
 * surrounding `(auth)/layout.tsx` is itself a Server Component (so it can
 * export `robots` metadata); the animated gradient-mesh/noise background lives
 * in the separate `<AuthBackground>` client island. The card stays
 * server-rendered.
 *
 * Accepts a `className` so individual pages can extend (not replace) the
 * chrome — e.g., add a wider `max-w` for a future multi-step flow — without
 * monkey-patching the shared container. `cn()` from `@/lib/utils` resolves
 * conflicting Tailwind utilities so caller-supplied classes win.
 */
export function AuthCard({
  className,
  children,
  ...props
}: React.ComponentProps<"section">): React.JSX.Element {
  return (
    <section
      className={cn(
        "w-full max-w-md rounded-2xl border border-border-strong bg-bg-elevated p-8",
        "shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_rgba(0,0,0,0.08)]",
        className,
      )}
      {...props}
    >
      {children}
    </section>
  );
}
