"use client";

import { animate, useReducedMotion } from "framer-motion";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

import { MatchResponseSchema } from "@matchlayer/shared-types";

/**
 * Results_Page — `(app)/matches/[id]` (Requirement 13.1–13.7; design "results
 * page: the demo moment").
 *
 * This is the Phase 1 payoff screen. It fetches a Match_Result via
 * `GET /api/v1/matches/{id}` (the dynamic segment is named `[id]`; the OpenAPI
 * path parameter is `match_id`) using `apiFetch` for Bearer attachment + the
 * silent refresh-and-retry path, then validates the body with the generated
 * `MatchResponseSchema` from `@matchlayer/shared-types` (no hand-written API
 * types, per `conventions.md`).
 *
 * It must be a Client Component: the score reveal is an animated count-up and
 * the page reads `prefers-reduced-motion` at runtime — both browser-only. The
 * surrounding `(app)` Authenticated_Shell still gates the session server-side
 * and exports `robots: { index: false, follow: false }`, so this page inherits
 * `noindex, nofollow` (Requirement 15.2) and adds no discoverability metadata.
 *
 * Security (`security.md` LLM/match-output rule, Requirement 13.7): every piece
 * of Match_Result-derived content — keyword terms and suggestion text — is
 * rendered as a plain JSX text node. `dangerouslySetInnerHTML` is never used
 * anywhere in this file.
 */

/** Parsed Match_Result shape, inferred from the generated Zod schema so the
 *  component never hand-writes the API response type. */
type Match = ReturnType<typeof MatchResponseSchema.parse>;

/** Hero-reveal easing from `design.md` ("Motion"): smooth ease-out. Declared as
 *  a const tuple so framer-motion's `Easing` type narrows correctly. */
const SCORE_EASE = [0.16, 1, 0.3, 1] as const;

/** The four request lifecycle states the page renders distinct UI for. */
type LoadState = "loading" | "ready" | "not-found" | "error";

export default function ResultsPage(): React.JSX.Element {
  // The dynamic segment is `[id]`; Next.js exposes it under that key.
  const params = useParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";

  const [state, setState] = React.useState<LoadState>("loading");
  const [match, setMatch] = React.useState<Match | null>(null);

  React.useEffect(() => {
    // A missing route param is handled by a render guard below, not by setting
    // state from the effect body.
    if (!id) {
      return;
    }

    let cancelled = false;

    async function load(): Promise<void> {
      setState("loading");
      setMatch(null);

      let res: Response;
      try {
        res = await apiFetch(`/api/v1/matches/${encodeURIComponent(id)}`);
      } catch {
        // Network error (server down, DNS, CORS). Friendly error, not a throw.
        if (!cancelled) setState("error");
        return;
      }

      if (cancelled) return;

      // Friendly not-found state for a missing / deleted / other-owner match
      // (Requirement 13.6) — the API returns 404 `not_found` for all three.
      if (res.status === 404) {
        setState("not-found");
        return;
      }

      if (!res.ok) {
        setState("error");
        return;
      }

      let body: unknown;
      try {
        body = await res.json();
      } catch {
        if (!cancelled) setState("error");
        return;
      }

      // Validate the contract at the boundary. A drift between the live API and
      // the committed schema surfaces here as a friendly error rather than a
      // render crash on a missing field.
      const parsed = MatchResponseSchema.safeParse(body);
      if (cancelled) return;
      if (!parsed.success) {
        setState("error");
        return;
      }

      setMatch(parsed.data);
      setState("ready");
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [id]);

  if (state === "loading") {
    return <ResultsSkeleton />;
  }

  if (state === "not-found" || !id) {
    return (
      <EmptyState
        title="Match not found"
        body="We couldn't find this match. It may have been deleted, or it never belonged to your account."
      />
    );
  }

  if (state === "error" || match === null) {
    return (
      <EmptyState
        title="Something went wrong"
        body="We couldn't load this match right now. Please try again in a moment."
      />
    );
  }

  return <MatchResult match={match} />;
}

/**
 * The resolved result view. Receives a parsed, validated Match_Result and lays
 * out the score reveal, the matched/missing keyword groups, the suggestions,
 * and the explainable score breakdown.
 */
function MatchResult({ match }: { match: Match }): React.JSX.Element {
  return (
    <article className="mx-auto max-w-3xl space-y-12">
      <header className="space-y-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-text">
          Your match score
        </h1>
        <ScoreReveal score={match.score} />
        <p className="font-mono text-xs text-text-subtle">
          {match.scorer_version}
        </p>
      </header>

      <ScoreBreakdown breakdown={match.score_breakdown} />

      <section aria-labelledby="keywords-heading" className="space-y-6">
        <h2 id="keywords-heading" className="text-lg font-semibold text-text">
          Keyword overlap
        </h2>
        <div className="grid gap-6 sm:grid-cols-2">
          <KeywordGroup
            tone="success"
            title="Matched"
            emptyLabel="No keywords from the job description were found in your resume."
            keywords={match.matched_keywords}
          />
          <KeywordGroup
            tone="warning"
            title="Missing"
            emptyLabel="Nothing missing — your resume covers every analyzed keyword."
            keywords={match.missing_keywords}
          />
        </div>
      </section>

      <Suggestions suggestions={match.suggestions} />

      <footer className="flex flex-wrap gap-3">
        <Button asChild variant="outline">
          <Link href="/upload">Run another match</Link>
        </Button>
        <Button asChild variant="ghost">
          <Link href="/library">Back to library</Link>
        </Button>
      </footer>
    </article>
  );
}

/**
 * Animated count-up to the final score with the signature violet→cyan gradient
 * on the number (Requirement 13.2). Honors `prefers-reduced-motion`: when set,
 * the resolved score is shown immediately and no animation runs (Requirement
 * 13.5).
 *
 * The animated glyphs are `aria-hidden` and a single `sr-only` sentence carries
 * the resolved value, so assistive tech announces "92 out of 100" once instead
 * of every intermediate frame.
 */
function ScoreReveal({ score }: { score: number }): React.JSX.Element {
  const reduced = useReducedMotion();
  const [display, setDisplay] = React.useState(0);

  React.useEffect(() => {
    // Reduced motion (or the SSR/unknown case resolving to reduced): no
    // animation runs; the resolved value is shown directly in render below.
    if (reduced) {
      return;
    }

    const controls = animate(0, score, {
      duration: 0.6,
      ease: SCORE_EASE,
      onUpdate: (value) => setDisplay(Math.round(value)),
    });

    return () => controls.stop();
  }, [score, reduced]);

  // When reduced motion is set, present the resolved score immediately
  // (Requirement 13.5); otherwise show the animating value.
  const shown = reduced ? score : display;

  return (
    <div className="flex items-baseline justify-center gap-1">
      <span
        aria-hidden="true"
        className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-mono text-7xl font-semibold tabular-nums tracking-tight text-transparent sm:text-8xl"
      >
        {shown}
      </span>
      <span
        aria-hidden="true"
        className="font-mono text-2xl font-medium tabular-nums text-text-muted"
      >
        /100
      </span>
      <span className="sr-only">{`Match score: ${score} out of 100.`}</span>
    </div>
  );
}

/**
 * The explainable score breakdown (Requirement 13.4): the similarity component,
 * the keyword-coverage component, the two applied weights, and the final score.
 * Component values and weights are fractions in 0..1; the components are shown
 * as percentages and the weights as their literal decimal contribution.
 */
function ScoreBreakdown({
  breakdown,
}: {
  breakdown: Match["score_breakdown"];
}): React.JSX.Element {
  const rows: { label: string; value: string }[] = [
    {
      label: "Similarity (TF-IDF cosine)",
      value: formatPercent(breakdown.similarity_component),
    },
    {
      label: "Keyword coverage",
      value: formatPercent(breakdown.keyword_coverage_component),
    },
    {
      label: "Similarity weight",
      value: formatWeight(breakdown.weight_similarity),
    },
    {
      label: "Keyword weight",
      value: formatWeight(breakdown.weight_keyword),
    },
  ];

  return (
    <section
      aria-labelledby="breakdown-heading"
      className="rounded-2xl border border-border bg-bg-elevated p-6 shadow-sm sm:p-8"
    >
      <h2 id="breakdown-heading" className="text-lg font-semibold text-text">
        How this score was calculated
      </h2>
      <dl className="mt-4 divide-y divide-border">
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex items-center justify-between py-2.5"
          >
            <dt className="text-sm text-text-muted">{row.label}</dt>
            <dd className="font-mono text-sm tabular-nums text-text">
              {row.value}
            </dd>
          </div>
        ))}
        <div className="flex items-center justify-between py-2.5">
          <dt className="text-sm font-medium text-text">Final score</dt>
          <dd className="font-mono text-sm font-semibold tabular-nums text-text">
            {breakdown.final_score}
          </dd>
        </div>
      </dl>
    </section>
  );
}

/**
 * One keyword group rendered in either the `success` (matched) or `warning`
 * (missing) token family (Requirement 13.3). Each term is a plain text node
 * inside a pill — never interpreted as HTML.
 */
function KeywordGroup({
  tone,
  title,
  keywords,
  emptyLabel,
}: {
  tone: "success" | "warning";
  title: string;
  keywords: Match["matched_keywords"];
  emptyLabel: string;
}): React.JSX.Element {
  const pillClass =
    tone === "success"
      ? "border-success/30 bg-success/10 text-success"
      : "border-warning/30 bg-warning/10 text-warning";
  const countClass = tone === "success" ? "text-success" : "text-warning";

  return (
    <div className="space-y-3">
      <h3 className="flex items-center gap-2 text-sm font-medium text-text">
        {title}
        <span className={cn("font-mono text-xs tabular-nums", countClass)}>
          {keywords.length}
        </span>
      </h3>
      {keywords.length === 0 ? (
        <p className="text-sm text-text-muted">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {keywords.map((keyword, index) => (
            <li
              key={`${keyword.term}-${index}`}
              className={cn("rounded-full border px-3 py-1 text-sm", pillClass)}
            >
              {keyword.term}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Rule-based improvement suggestions as a readable list (Requirement 13.3).
 * Suggestion text is plain text. The empty case is defensive only — the
 * Suggestion_Generator always returns at least one affirmative suggestion.
 */
function Suggestions({
  suggestions,
}: {
  suggestions: Match["suggestions"];
}): React.JSX.Element {
  return (
    <section aria-labelledby="suggestions-heading" className="space-y-4">
      <h2 id="suggestions-heading" className="text-lg font-semibold text-text">
        Suggestions
      </h2>
      {suggestions.length === 0 ? (
        <p className="text-sm text-text-muted">
          No suggestions for this match.
        </p>
      ) : (
        <ul className="space-y-3">
          {suggestions.map((suggestion, index) => (
            <li
              key={`${suggestion.keyword}-${index}`}
              className="rounded-xl border border-border bg-bg-elevated p-4 shadow-sm"
            >
              <p className="text-sm text-text">{suggestion.text}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Friendly empty/error surface used for the 404 not-found state and any
 * unexpected load failure (Requirement 13.6) — never a raw error object or a
 * stack trace.
 */
function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}): React.JSX.Element {
  return (
    <div className="mx-auto max-w-md space-y-4 py-16 text-center">
      <h1 className="text-2xl font-semibold tracking-tight text-text">
        {title}
      </h1>
      <p className="text-text-muted">{body}</p>
      <div className="flex justify-center gap-3 pt-2">
        <Button asChild variant="outline">
          <Link href="/library">Back to library</Link>
        </Button>
        <Button asChild>
          <Link href="/upload">Start a new match</Link>
        </Button>
      </div>
    </div>
  );
}

/**
 * Loading skeleton that mirrors the resolved layout's shape (a score block, a
 * breakdown card, and keyword columns) per `design.md` — skeletons over a bare
 * spinner. Purely presentational and `aria-hidden`; the page announces nothing
 * while the request is in flight.
 */
function ResultsSkeleton(): React.JSX.Element {
  return (
    <div
      aria-hidden="true"
      className="mx-auto max-w-3xl animate-pulse space-y-12"
    >
      <div className="space-y-6 text-center">
        <div className="mx-auto h-6 w-40 rounded-md bg-bg-elevated" />
        <div className="mx-auto h-24 w-40 rounded-2xl bg-bg-elevated" />
      </div>
      <div className="h-48 rounded-2xl border border-border bg-bg-elevated" />
      <div className="grid gap-6 sm:grid-cols-2">
        <div className="h-32 rounded-xl bg-bg-elevated" />
        <div className="h-32 rounded-xl bg-bg-elevated" />
      </div>
    </div>
  );
}

/** Format a 0..1 component value as a whole-number percentage (e.g. 0.42 →
 *  "42%"). Non-finite inputs collapse to "0%" so a malformed breakdown never
 *  renders "NaN%". */
function formatPercent(value: number): string {
  if (!Number.isFinite(value)) {
    return "0%";
  }
  return `${Math.round(value * 100)}%`;
}

/** Format a weight as a 2-decimal contribution (e.g. 0.6 → "0.60"). */
function formatWeight(value: number): string {
  if (!Number.isFinite(value)) {
    return "0.00";
  }
  return value.toFixed(2);
}
