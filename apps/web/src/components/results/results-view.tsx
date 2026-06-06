"use client";

import { useReducedMotion } from "framer-motion";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { ErrorState } from "@/components/error-state";
import { MotionSafe } from "@/components/motion-safe";
import { SkeletonLoader } from "@/components/skeleton-loader";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

import { MatchResponseSchema } from "@matchlayer/shared-types";
import type { MatchResponse } from "@matchlayer/shared-types";

import { EmptyResultState } from "./empty-result-state";
import { KeywordSection } from "./keyword-section";
import { ScoreBreakdownCard } from "./score-breakdown-card";
import { ScoreGauge } from "./score-gauge";
import { SuggestionCard } from "./suggestion-card";

/**
 * ResultsView — the client data view for the ATS Results page (design Section
 * 6.3, 6.6, 8.1, Error Handling; Req 11.7, 11.8, 12.5, 12.6, 13.1–13.6, 17.6,
 * 17.7, 19.4, 21.11).
 *
 * The Server Component shell at `(app)/matches/[id]/page.tsx` reads the route
 * `id` and renders this island. Everything that needs the browser lives here:
 * the TanStack Query fetch, the reduced-motion-aware staggered entrance, and
 * the score reveal (owned by `ScoreGauge`).
 *
 * ## Data (Req 13.4–13.6, 17.6, 17.7; design 6.6)
 * A single `useQuery({ queryKey: ["match", id] })` drives the screen. The
 * `queryFn` calls the existing `apiFetch` wrapper (Bearer attach + the silent
 * 401→refresh→retry) and validates the body with the generated
 * `MatchResponseSchema` from `@matchlayer/shared-types` — no hand-written API
 * type (conventions.md, Req 21.11). Outcomes map to UI states:
 *
 *   - **pending** → `SkeletonLoader variant="results"` (Req 13.4, 17.1, 17.2).
 *   - **5xx / network / no response ≤10s** (and the 30s general ceiling) →
 *     retryable `ErrorState` whose **[Retry]** re-runs the query in place via
 *     `refetch()` — no navigation away (Req 13.5, 17.6, 17.7) — plus a
 *     `/upload` recovery link.
 *   - **404** → a **non-enumerable** `ErrorState` ("We couldn't find that
 *     result") that never reveals whether the match exists for another user
 *     (Req 13.6); offers a `/upload` link, no retry.
 *   - **degenerate 0/0** → `EmptyResultState` (valid result, not an error;
 *     Req 12.5, 12.6).
 *   - **success** → the composed results content (gauge, breakdown, keyword
 *     sections, staggered suggestions, scorer footnote, CTA).
 *
 * The `QueryClientProvider` is already mounted at the app root
 * (`app/layout.tsx` → `Providers`), and that provider sets `retry: false`, so
 * the only retry is the user-initiated one. A 10s `AbortController` deadline
 * (Req 13.5) is layered under the 30s general ceiling (Req 17.7) — 10s is the
 * binding cutoff for this screen.
 *
 * ## Security (Req 11.8, security.md)
 * The match contract never returns `job_description_text` (Restricted PII), so
 * it is neither referenced nor rendered anywhere here. Every match-derived
 * string (keyword terms, suggestion text, `scorer_version`) is rendered as a
 * plain JSX text node — `dangerouslySetInnerHTML` is never used.
 *
 * ## Accessibility (Req 19.4)
 * A polite `aria-live` region announces result completion once the query
 * resolves, so assistive-tech users learn the analysis finished without the
 * focus moving.
 */

/** The screen's request deadline (Req 13.5: no response within 10s → error).
 *  Sits under the 30s general ceiling (Req 17.7); 10s is the binding cutoff. */
const REQUEST_TIMEOUT_MS = 10_000;

/** Why a fetch attempt failed, so the view can pick non-enumerable (404) vs.
 *  retryable (5xx / network / timeout / contract drift) error copy. */
type MatchFetchErrorKind = "not-found" | "retryable";

/**
 * The single error type thrown by {@link fetchMatch}. It carries **only** a
 * coarse `kind` discriminant — never a status code, RFC 7807 envelope, or any
 * backend detail — so there is structurally nothing for the UI to leak
 * (Req 17.4, security.md).
 */
class MatchFetchError extends Error {
  readonly kind: MatchFetchErrorKind;

  constructor(kind: MatchFetchErrorKind) {
    super(kind);
    this.name = "MatchFetchError";
    this.kind = kind;
  }
}

/**
 * Fetch and validate one Match_Result.
 *
 * Wraps `apiFetch` with a 10s abort deadline (Req 13.5) wired to both the
 * query's own cancellation `signal` (so unmount/refetch aborts the request)
 * and a timer. Any network error, abort/timeout, non-OK status, unparseable
 * body, or schema-drift parse failure becomes a `retryable` error; a 404
 * becomes a `not-found` error. Success returns the parsed, contract-validated
 * `MatchResponse`.
 */
async function fetchMatch(
  id: string,
  querySignal: AbortSignal,
): Promise<MatchResponse> {
  const controller = new AbortController();
  const abort = (): void => controller.abort();

  // Tie the request to the query's lifecycle signal so a refetch/unmount
  // cancels it; TanStack Query discards results from an aborted query.
  if (querySignal.aborted) {
    controller.abort();
  } else {
    querySignal.addEventListener("abort", abort);
  }

  const timer = setTimeout(abort, REQUEST_TIMEOUT_MS);

  let res: Response;
  try {
    res = await apiFetch(`/api/v1/matches/${encodeURIComponent(id)}`, {
      signal: controller.signal,
    });
  } catch {
    // Network error, or the 10s deadline / cancellation aborted the request.
    throw new MatchFetchError("retryable");
  } finally {
    clearTimeout(timer);
    querySignal.removeEventListener("abort", abort);
  }

  // A missing / deleted / other-owner match all return 404 `not_found`; the
  // view renders identical, non-enumerable copy for every case (Req 13.6).
  if (res.status === 404) {
    throw new MatchFetchError("not-found");
  }

  // 5xx and any other non-OK status are retryable load failures (Req 13.5).
  if (!res.ok) {
    throw new MatchFetchError("retryable");
  }

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new MatchFetchError("retryable");
  }

  // Validate the contract at the boundary: drift between the live API and the
  // committed schema surfaces as a friendly retryable error, not a render
  // crash on a missing field (Req 20.7).
  const parsed = MatchResponseSchema.safeParse(body);
  if (!parsed.success) {
    throw new MatchFetchError("retryable");
  }

  return parsed.data;
}

/** Props for {@link ResultsView}. */
export interface ResultsViewProps {
  /** The Match_Result id from the `/matches/[id]` route segment. */
  id: string;
}

export function ResultsView({ id }: ResultsViewProps): React.JSX.Element {
  const hasId = id.length > 0;

  const query = useQuery<MatchResponse, MatchFetchError>({
    queryKey: ["match", id],
    queryFn: ({ signal }) => fetchMatch(id, signal),
    enabled: hasId,
    retry: false,
  });

  // Decide the body and the polite announcement together so the live region is
  // always present in the DOM and only its text content changes on resolve.
  let body: React.JSX.Element;
  let liveMessage = "";

  if (!hasId) {
    // No id in the route — treat as the same non-enumerable not-found surface
    // (never disclose anything about another user's data).
    body = <NotFoundErrorState />;
  } else if (query.status === "pending") {
    body = (
      <div className="py-12">
        <SkeletonLoader variant="results" />
      </div>
    );
  } else if (query.status === "error") {
    if (query.error.kind === "not-found") {
      body = <NotFoundErrorState />;
    } else {
      body = (
        <div className="py-16">
          <ErrorState
            title="We couldn't load your results"
            message="Something went wrong loading this match. Check your connection and try again in a moment."
            action={{ label: "Retry", onClick: () => void query.refetch() }}
            secondaryHref="/upload"
          />
        </div>
      );
    }
  } else {
    // Success. A degenerate 0/0 breakdown is a valid-but-empty result, not an
    // error (Req 12.5, 12.6) — render the EmptyResultState, never the danger
    // ErrorState.
    const breakdown = query.data.score_breakdown;
    const isDegenerate =
      breakdown.similarity_component === 0 &&
      breakdown.keyword_coverage_component === 0;

    if (isDegenerate) {
      liveMessage =
        "Analysis complete. Not enough readable content was found to produce a match.";
      body = (
        <div className="py-16">
          <EmptyResultState />
        </div>
      );
    } else {
      liveMessage = "Your match results are ready.";
      body = <ResultsContent match={query.data} />;
    }
  }

  return (
    <>
      {/* The page's single <h1> (Req 19.5; design Section 10.4). The score
          gauge is the visual hero, so the page needs no large visible title;
          a visually-hidden <h1> gives the screen exactly one top-level heading
          and a valid, sequential outline (h1 → h2 "Score breakdown" → h3
          keyword/suggestion sections) in every state — loading, error, empty,
          and success — without competing with the gauge. */}
      <h1 className="sr-only">Your resume match results</h1>
      {/* Polite result-completion announcement (Req 19.4). Always mounted so a
          content change after the query resolves is announced. */}
      <p role="status" aria-live="polite" className="sr-only">
        {liveMessage}
      </p>
      {body}
    </>
  );
}

/**
 * The non-enumerable not-found surface (Req 13.6). The copy is identical for a
 * match that never existed, was deleted, or belongs to another user, so it
 * reveals nothing about another account's data. Offers a `/upload` recovery
 * link and no retry (a 404 will not change on retry).
 */
function NotFoundErrorState(): React.JSX.Element {
  return (
    <div className="py-16">
      <ErrorState
        title="We couldn't find that result"
        message="This result isn't available. It may have been removed, or the link may be incorrect. Try analyzing a job from the upload page."
        secondaryHref="/upload"
      />
    </div>
  );
}

/**
 * The successful (non-degenerate) results composition (design Section 8.1).
 *
 * Two-column "hero result" on desktop — the score moment (gauge + label) on the
 * left, the explainable breakdown on the right — collapsing to a single column
 * below `lg`. Below the fold: matched keywords, missing keywords, the staggered
 * suggestion cards, the `scorer_version` footnote, and the single
 * "Analyze another job" primary CTA → `/upload` (the only navigation on the
 * page — no dashboard/history/analytics, Req 13.2).
 *
 * Exported (in addition to being the success branch of {@link ResultsView}) so
 * the env-gated visual-harness route (`app/visual-harness/results/[fixture]`)
 * can render the **exact same** composition from a Section 5 fixture, with no
 * network fetch, for the Playwright visual/layout acceptance gates (task 9.1;
 * design Section 9.3). Rendering the real component — rather than a hand-rolled
 * copy — guarantees the gate measures the production layout and can never drift
 * from it.
 */
export function ResultsContent({
  match,
}: {
  match: MatchResponse;
}): React.JSX.Element {
  const {
    score,
    score_breakdown,
    matched_keywords,
    missing_keywords,
    suggestions,
    scorer_version,
  } = match;

  return (
    <article className="space-y-12 py-12">
      {/* Hero row: gauge+label (left) · breakdown (right). */}
      <div className="grid gap-8 lg:grid-cols-2">
        <div className="flex items-center justify-center rounded-hero border border-border bg-bg-elevated p-8 shadow-resting">
          <ScoreGauge score={score} />
        </div>
        <Entrance>
          <ScoreBreakdownCard breakdown={score_breakdown} />
        </Entrance>
      </div>

      <KeywordSection
        title="Matched keywords"
        keywords={matched_keywords}
        variant="success"
        emptyMessage="No keywords from the job description were found in your resume yet."
      />

      <KeywordSection
        title="Missing keywords"
        keywords={missing_keywords}
        variant="warning"
        emptyMessage="Your resume already covers every keyword we analyzed from this job description."
      />

      {suggestions.length > 0 && (
        <section className="space-y-4">
          <h3 className="text-lg font-semibold tracking-tight text-text">
            Suggestions
          </h3>
          <div className="grid gap-4 md:grid-cols-2">
            {suggestions.map((suggestion, index) => (
              <Entrance key={`${suggestion.keyword}-${index}`} index={index}>
                <SuggestionCard suggestion={suggestion} />
              </Entrance>
            ))}
          </div>
        </section>
      )}

      <footer className="space-y-6 pb-4">
        {/* Attribution footnote (Req 11.8). `job_description_text` is never
            referenced or rendered anywhere on this page (Req 11.8, security.md). */}
        <p className="font-mono text-xs text-text-subtle">
          Scored with {scorer_version}
        </p>
        <Button asChild>
          <Link href="/upload">Analyze another job</Link>
        </Button>
      </footer>
    </article>
  );
}

/**
 * Staggered fade-up entrance for the breakdown and suggestion cards (Req 11.7,
 * 15.2, 19.6; design Section 6.5, 8.1).
 *
 * The whole stagger sequence stays within the 400ms layout-transition ceiling
 * (Req 15.2): each successive card starts 100ms later, and its duration is
 * trimmed so it still finishes by ~400ms regardless of how many cards render.
 *
 * Under `prefers-reduced-motion` the children render in their final state
 * immediately with no transform — handled by branching on `useReducedMotion()`
 * here (so content is never left at `opacity: 0`), then delegating the animated
 * case to the shared `MotionSafe` chokepoint (Req 11.7, 19.6; design Section
 * 6.5).
 */
function Entrance({
  index = 0,
  className,
  children,
}: {
  index?: number;
  className?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  const reduced = useReducedMotion();

  // Reduced motion (or the SSR/unknown `null` case is treated as no-preference
  // and animates normally): render the final state with no entrance transform.
  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  // 100ms between successive starts, capped so the sequence total stays within
  // the 400ms layout ceiling; the per-card duration is trimmed to match.
  const delay = Math.min(index * 0.1, 0.3);
  const duration = Math.max(0.4 - delay, 0.1);

  return (
    <MotionSafe
      className={className}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </MotionSafe>
  );
}
