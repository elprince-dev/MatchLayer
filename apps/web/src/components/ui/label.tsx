"use client";

import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";

import { cn } from "@/lib/utils";

/**
 * Label primitive — shadcn `new-york` style on Radix `Label`, remapped to
 * MatchLayer tokens.
 *
 * A `<label>` is not itself focusable, so it carries no focus ring (the 2px
 * branded ring lives on the associated Input/Textarea). It does react to a
 * disabled peer/group: `peer-disabled`/`group-data-[disabled]` dim it and drop
 * pointer events so the field/label read as one unit.
 *
 * Text uses `text-text` (primary, AA in both themes — design 10.1). Accepts
 * every Radix `Label.Root` prop plus `className`.
 */
function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-sm font-medium text-text leading-none select-none",
        "group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50",
        "peer-disabled:pointer-events-none peer-disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export { Label };
