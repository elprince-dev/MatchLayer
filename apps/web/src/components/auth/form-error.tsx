import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Live-region error display for Auth_Page forms (Requirement 12.2 and §14.1).
 *
 * The acceptance criterion is precise: every Auth_Page form "SHALL announce
 * server-side validation errors via `aria-live="polite"` on the form's error
 * region". §14.1 places the region "directly above the submit button" and
 * notes it is "empty when there are no errors. Reads validation errors and
 * server-side errors from the same region."
 *
 * Implementation choices:
 *
 *   - `role="alert"` + `aria-live="polite"`: the role marks the element as an
 *     error announcement; `polite` ensures assistive tech finishes the user's
 *     current utterance before reading the new error rather than forcibly
 *     interrupting (per §14.6: "polite means assistive tech announces server
 *     validation errors without forcibly interrupting").
 *
 *   - The element is rendered unconditionally (i.e., even when `children` is
 *     falsy). The live region must already exist in the DOM at the time its
 *     content changes; otherwise screen readers won't see the change as a
 *     mutation of an active live region and the announcement is missed.
 *     Pages therefore use `<FormError>{error?.detail}</FormError>` and let
 *     this component handle the empty case — it returns `null` content but
 *     keeps the wrapping element mounted via `aria-hidden` toggling.
 *
 *   - Styling: `text-danger` foreground reads against the `bg-bg-elevated`
 *     card surface in both themes (§14.6 lists this pair as AA-compliant).
 *     `text-sm` matches form-row text rhythm. A small top margin spacing is
 *     left to the page so the component composes cleanly above any submit
 *     button.
 *
 *   - `aria-hidden="true"` when empty: prevents AT from announcing an empty
 *     region as if it had content, while still keeping the live-region node
 *     present in the DOM so the next non-empty render is announced.
 *
 * Server component by default — no state, no effects, no browser APIs.
 *
 * @example
 *   <FormError>{error?.detail}</FormError>
 *   <FormError>{passwordTooShort && "Password must be at least 12 characters."}</FormError>
 */
export function FormError({
  className,
  children,
  ...props
}: React.ComponentProps<"div">): React.JSX.Element {
  // `children` is the standard React node prop; treat anything falsy/empty
  // (`null`, `undefined`, `false`, `""`) as "no error to display" so that
  // form pages can pass `error?.message` directly without conditionals.
  const isEmpty =
    children === null ||
    children === undefined ||
    children === false ||
    children === "";

  return (
    <div
      role="alert"
      aria-live="polite"
      aria-hidden={isEmpty ? "true" : undefined}
      className={cn("text-sm text-danger", className)}
      {...props}
    >
      {isEmpty ? null : children}
    </div>
  );
}
