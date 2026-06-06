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

/**
 * Inline, per-field error display for Auth_Page form rows (Req 8.4, 19.4).
 *
 * Where {@link FormError} is the single form-level banner that surfaces
 * submit-time/server errors above the form, `FieldError` is the **field-level**
 * variant the redesign adds: it renders **adjacent to** an individual input and
 * announces client-side validation failures (email format, password length,
 * confirm-password match) via `aria-live="polite"` (Req 8.4: "display the error
 * message inline adjacent to the relevant field with an `aria-live="polite"`
 * announcement"; Req 19.4: form errors announced through a live region).
 *
 * Usage contract:
 *
 *   - **Always mounted.** Like `FormError`, the element stays in the DOM even
 *     when there is no error so assistive tech treats the next non-empty render
 *     as a mutation of an *existing* live region (a region added to the DOM at
 *     the same time as its content is frequently missed). React Hook Form's
 *     `errors.<field>?.message` is passed straight in; the empty case collapses
 *     to `null` content + `aria-hidden`.
 *
 *   - **Wired by `id`.** The caller gives the element a stable `id` and points
 *     the field's `aria-describedby` at it (plus `aria-invalid` on the input),
 *     so the error is programmatically associated with its control, not merely
 *     visually adjacent.
 *
 *   - **Rendered as a `<p>`** (a phrasing-level block) so it sits naturally
 *     beneath the input row without implying it is a standalone alert region
 *     the way the form-level banner does; `aria-live="polite"` is sufficient
 *     for the field-level announcement and avoids stacking multiple
 *     `role="alert"` regions on one form.
 *
 * Server component by default — no state, no effects, no browser APIs.
 *
 * @example
 *   <Input id="email" aria-invalid={!!errors.email} aria-describedby="email-error" {...register("email")} />
 *   <FieldError id="email-error">{errors.email?.message}</FieldError>
 */
export function FieldError({
  className,
  children,
  ...props
}: React.ComponentProps<"p">): React.JSX.Element {
  const isEmpty =
    children === null ||
    children === undefined ||
    children === false ||
    children === "";

  return (
    <p
      aria-live="polite"
      aria-hidden={isEmpty ? "true" : undefined}
      className={cn("mt-1 text-sm text-danger", className)}
      {...props}
    >
      {isEmpty ? null : children}
    </p>
  );
}
