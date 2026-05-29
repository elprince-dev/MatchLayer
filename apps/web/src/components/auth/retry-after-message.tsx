import * as React from "react";

/**
 * User-facing 429-rate-limit message for Auth_Page forms (Requirement 12.8).
 *
 * Requirement 12.8 reads: "WHEN the Auth_State_Hook's `signIn` mutation
 * receives an HTTP 429 response, THE Web_App SHALL render a user-facing
 * message that includes the rounded-up number of seconds returned in the
 * `Retry-After` response header." §14.2/§14.3 supply the literal copy:
 * "Too many attempts — please try again in N seconds." and refer to this
 * component by name as `<RetryAfterMessage seconds={retryAfter}>`.
 *
 * The component performs the rounding here, on the rendering boundary, so
 * page-level call sites can pass the raw header value through `Number(...)`
 * without each caller re-implementing the rounding rule. `Math.ceil` matches
 * the "rounded-up" wording in both Requirement 12.8 and Requirement 9
 * acceptance criteria 9.4 (`Math.ceil` on the server side too); fractional
 * seconds round to the next whole second so the user never under-waits.
 *
 * Why this is its own component (not a string literal in each form):
 *
 *   - The literal copy is referenced from multiple Auth_Pages (register,
 *     login, forgot-password). Centralising it here keeps the wording
 *     byte-for-byte identical across pages, which matters for the §8.3
 *     "no account enumeration" timing/copy invariants and for translation
 *     down the line.
 *   - The "1 second" vs "N seconds" pluralization rule lives in one place,
 *     not three. Spec text uses "N seconds" generically; the implementation
 *     produces grammatically correct output for the common N=1 edge case.
 *   - Page error blocks just write `<RetryAfterMessage seconds={retryAfter}/>`
 *     inside `<FormError>` without juggling math.
 *
 * Defensive numeric handling:
 *
 *   - The `Retry-After` header can technically be HTTP-date-formatted, but
 *     the API in this spec always returns a decimal-seconds value (Phase 1
 *     Auth design §10.x). The component still defends against `NaN`,
 *     negative values, and non-finite inputs by clamping to a minimum of 1,
 *     so a malformed header never produces "in NaN seconds" or "in -3
 *     seconds" copy.
 *   - The `seconds` prop type is `number`, not `string`. Pages parse the
 *     header at the boundary (e.g., via `Number(headers.get("retry-after"))`
 *     in the API client) so the component contract stays simple.
 *
 * Server component by default — pure function of props, no state, no effects.
 *
 * @example
 *   <FormError>
 *     <RetryAfterMessage seconds={retryAfter} />
 *   </FormError>
 */
export function RetryAfterMessage({
  seconds,
}: {
  seconds: number;
}): React.JSX.Element {
  // Clamp the input. `Math.ceil(NaN) === NaN` and `Math.max(NaN, 1) === NaN`,
  // so explicitly handle the non-finite case first. After ceiling, a value
  // ≤ 0 collapses to 1 — telling the user to wait "0 seconds" is meaningless
  // and waiting 1s aligns with the API's own minimum bucket size.
  const safeSeconds =
    Number.isFinite(seconds) && seconds > 0
      ? Math.max(1, Math.ceil(seconds))
      : 1;

  const unit = safeSeconds === 1 ? "second" : "seconds";

  return (
    <span>
      Too many attempts — please try again in {safeSeconds} {unit}.
    </span>
  );
}
