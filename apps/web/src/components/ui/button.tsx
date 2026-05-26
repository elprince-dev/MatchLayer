import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Button primitive — shadcn `new-york` style, remapped to MatchLayer brand tokens.
 *
 * Token mapping (no shadcn default tokens like `bg-primary`/`bg-foreground` are
 * referenced — we resolve everything against `globals.css` from task 4.2):
 *   - default      → `bg-brand` + white text (violet brand reads cleanly with
 *                    pure white in both themes; `text-bg` would invert in dark
 *                    mode and lose contrast against the violet)
 *   - destructive  → `bg-danger` + white text
 *   - outline      → `bg-bg-elevated` over `border-strong`, with `text-text`
 *   - secondary    → `bg-bg-elevated` + `text-text`
 *   - ghost        → transparent → `bg-bg-elevated` on hover
 *   - link         → `text-brand` underline
 *
 * Focus ring is `ring-brand` with a `ring-offset-bg` halo so the ring stays
 * visible against any surface in both themes (per design.md a11y rules:
 * "visible, branded focus rings").
 *
 * Disabled, svg sizing, and aria-invalid styling follow the canonical
 * shadcn `new-york` Button.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all outline-none disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg aria-invalid:border-danger aria-invalid:ring-2 aria-invalid:ring-danger/30",
  {
    variants: {
      variant: {
        default: "bg-brand text-white shadow-sm hover:bg-brand/90",
        destructive: "bg-danger text-white shadow-sm hover:bg-danger/90",
        outline:
          "border border-border-strong bg-bg-elevated text-text shadow-sm hover:bg-bg-elevated/80",
        secondary: "bg-bg-elevated text-text shadow-sm hover:bg-bg-elevated/80",
        ghost: "text-text hover:bg-bg-elevated",
        link: "text-brand underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 rounded-md gap-1.5 px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
