import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Input primitive — shadcn `new-york` style, remapped to MatchLayer brand tokens.
 *
 * Token mapping (no shadcn default tokens like `bg-input`/`border-input` are
 * referenced — everything resolves against `globals.css`):
 *   - surface       → `bg-bg-elevated` over `border-border-strong`
 *   - text          → `text-text`; placeholder → `text-text-subtle`
 *   - selection     → `bg-brand` + white text
 *   - aria-invalid  → `border-danger` + `ring-danger/30` (matches Button)
 *
 * Focus ring mirrors `button.tsx` exactly: `outline-none` is paired with a
 * visible 2px branded ring (`ring-2 ring-brand`) plus a `ring-offset-bg` halo so
 * the indicator stays ≥3:1 against any surface in both themes (design 10.3,
 * Req 19.2). `outline-none` is never used without this replacement.
 *
 * Accepts every native `<input>` attribute plus `className` for composition.
 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-9 w-full min-w-0 rounded-md border border-border-strong bg-bg-elevated px-3 py-1 text-base text-text shadow-sm transition-[color,box-shadow] outline-none md:text-sm",
        "placeholder:text-text-subtle selection:bg-brand selection:text-white",
        "file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-text",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "aria-invalid:border-danger aria-invalid:ring-2 aria-invalid:ring-danger/30",
        className,
      )}
      {...props}
    />
  );
}

export { Input };
