import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Textarea primitive — shadcn `new-york` style, remapped to MatchLayer brand
 * tokens. Used by the Upload page's job-description field; the live character
 * count lives in the page, not here (design 7.3 — "Textarea adds nothing
 * custom").
 *
 * Token mapping mirrors `input.tsx`: `bg-bg-elevated` over `border-border-strong`,
 * `text-text` with `text-text-subtle` placeholder, `bg-brand` selection, and the
 * `danger` aria-invalid treatment shared with Button/Input.
 *
 * Focus ring matches the other primitives exactly: `outline-none` paired with a
 * visible 2px branded ring (`ring-2 ring-brand`) and a `ring-offset-bg` halo
 * (design 10.3, Req 19.2) — never `outline:none` without a replacement.
 *
 * Accepts every native `<textarea>` attribute plus `className`.
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-16 w-full rounded-md border border-border-strong bg-bg-elevated px-3 py-2 text-base text-text shadow-sm transition-[color,box-shadow] outline-none field-sizing-content md:text-sm",
        "placeholder:text-text-subtle selection:bg-brand selection:text-white",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        "aria-invalid:border-danger aria-invalid:ring-2 aria-invalid:ring-danger/30",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
