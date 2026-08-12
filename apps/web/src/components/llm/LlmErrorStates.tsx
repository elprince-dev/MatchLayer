"use client";

import * as React from "react";

import { CircleAlert, Clock, PowerOff, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The three LLM-specific error surfaces of Req 17.5 / 17.11 (design
 * §Frontend: "`LlmErrorStates.tsx` — 429 quota / 503 spend /
 * interrupted-stream states"), expressed as a discriminated union so a panel
 * can map an outcome to exactly one state:
 *
 *   - `"quota"`       → the 429 Daily_Quota rejection (Req 17.5).
 *   - `"unavailable"` → the 503 Spend_Circuit_Breaker / LLM_Unavailable
 *                       rejection (Req 17.5).
 *   - `"interrupted"` → the SSE connection closed without a terminal event
 *                       (Req 17.11) — always paired with a retry action.
 */
export type LlmErrorKind = "quota" | "unavailable" | "interrupted";

/** Props for {@link LlmErrorState}. */
export interface LlmErrorStateProps {
  /** Which of the three error surfaces to render. */
  kind: LlmErrorKind;
  /**
   * The user-safe RFC 7807 `detail` string from the API response, when one
   * exists. For the 429 this is preferred over the default copy because the
   * backend's detail states the *configured* daily limit and the UTC reset
   * (Req 13.5) — the frontend never hardcodes the limit. Only ever pass the
   * `detail` field, never the raw error object (security.md: no stack
   * traces/internals in the UI).
   */
  detail?: string;
  /**
   * Retry callback for the `"interrupted"` state (Req 17.11: "an action to
   * retry the LLM_Feature request"). Ignored by the other kinds — quota and
   * spend rejections are not retryable until their windows reset.
   */
  onRetry?: () => void;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * Static copy per error kind. All strings are plain language, pre-mapped, and
 * safe to display — this component is never handed a raw error object, so
 * there is structurally nothing for it to leak.
 *
 * Default messages are the spec-mandated phrasings:
 *   - quota: states the daily limit concept and the UTC reset (Req 17.5) —
 *     the API's RFC 7807 `detail` (which names the exact configured limit)
 *     replaces this generic copy whenever it is available.
 *   - unavailable: "AI features are temporarily disabled" (Req 17.5, exact
 *     phrase).
 *   - interrupted: "the response was interrupted" (Req 17.11).
 */
const ERROR_COPY: Record<
  LlmErrorKind,
  {
    title: string;
    message: string;
    Icon: React.ComponentType<{ className?: string }>;
  }
> = {
  quota: {
    title: "Daily AI limit reached",
    message:
      "You've used today's AI request allowance. Your daily limit resets at midnight UTC.",
    Icon: Clock,
  },
  unavailable: {
    title: "AI features unavailable",
    message:
      "AI features are temporarily disabled. Your match results are still available, and AI features will return automatically.",
    Icon: PowerOff,
  },
  interrupted: {
    title: "Response interrupted",
    message:
      "The AI response was interrupted before it finished. The partial output was discarded — you can retry the request.",
    Icon: CircleAlert,
  },
};

/**
 * LlmErrorState — the shared inline error surface for the LLM panels
 * (Req 17.5, 17.11).
 *
 * ## Why not reuse the global `ErrorState`
 * `ErrorState` is a full-page, centered surface that *guarantees* a recovery
 * action (falling back to a `/upload` link). Quota and spend rejections have
 * no meaningful in-place recovery — sending the user to `/upload` from an LLM
 * tab would be wrong — and these states render *inside* a panel next to
 * still-useful match content, so they need a calmer, left-aligned inline
 * treatment.
 *
 * ## Accessibility
 * The container is `role="alert"` (implicit `aria-live="assertive"`,
 * matching the existing `ErrorState` pattern and design.md's "errors
 * announced via aria-live"), so the message is announced when the state
 * appears. The icon is `aria-hidden`; meaning is carried entirely by text —
 * never by color alone. The `danger` token is used only as an indicator tint
 * (icon + well), not for body text, mirroring `error-state.tsx`'s contrast
 * rationale.
 *
 * Marked `"use client"` because the interrupted state's retry button attaches
 * an `onClick` handler.
 */
export function LlmErrorState({
  kind,
  detail,
  onRetry,
  className,
}: LlmErrorStateProps): React.JSX.Element {
  const { title, message, Icon } = ERROR_COPY[kind];

  // Prefer the API's user-safe RFC 7807 detail (it names the configured
  // limit / reset for the 429 per Req 13.5); fall back to the generic copy.
  const body = detail !== undefined && detail !== "" ? detail : message;

  return (
    <div
      data-slot="llm-error-state"
      data-kind={kind}
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-card border border-border bg-bg-elevated p-4",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border border-danger/30 bg-danger/10 text-danger"
      >
        <Icon className="size-4" />
      </span>

      <div className="min-w-0 space-y-1">
        <p className="text-sm font-semibold tracking-tight text-text">
          {title}
        </p>
        <p className="text-sm text-text-muted">{body}</p>

        {kind === "interrupted" && onRetry !== undefined && (
          <div className="pt-2">
            <Button type="button" size="sm" variant="outline" onClick={onRetry}>
              <RotateCcw aria-hidden="true" />
              Retry
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
