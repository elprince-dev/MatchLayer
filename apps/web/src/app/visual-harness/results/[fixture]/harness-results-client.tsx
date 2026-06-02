"use client";

import * as React from "react";

import { EmptyResultState } from "@/components/results/empty-result-state";
import { ResultsContent } from "@/components/results/results-view";
import {
  matchDegenerate,
  matchPartial,
  matchStrong,
} from "@/components/results/__fixtures__/match-fixtures";

import type { MatchResponse } from "@matchlayer/shared-types";

/**
 * Client renderer for the env-gated visual-harness Results route (task 9.1;
 * design Section 9.1, 9.3). The Server Component page (`./page.tsx`) owns
 * env-gating, `notFound()`, `metadata`, and reading the `[fixture]` segment; it
 * then hands this island **only the small `fixture` string key** — never the
 * resolved `MatchResponse` object.
 *
 * ## Why the fixture is resolved here (client-side), not passed as a prop
 * The Section 9.3 #7/#8 gates assert that a rendered result's markup contains
 * **no raw backend field names** (`similarity_component`,
 * `keyword_coverage_component`) and never `job_description_text`. If the server
 * page passed the resolved `MatchResponse` down as a prop, Next.js would
 * serialize that object into the inline **RSC flight payload** embedded in the
 * HTML — leaking those exact field names into `page.content()` and tripping the
 * gate, even though the visible DOM is clean.
 *
 * Production never does this: the real `ResultsView` fetches the match over the
 * wire (XHR) and holds it in client memory, so the object is never serialized
 * into the document. Resolving the fixture **inside this client component**
 * (importing it directly, keyed by the string) reproduces that exact data path
 * — the object lives only in client memory and never enters the HTML — so the
 * harness measures the true production layout AND the no-raw-fields invariant
 * holds for the same structural reason it holds in production.
 *
 * The chrome here intentionally mirrors the authenticated `(app)` shell (a
 * `border-b` top bar of the same height + a `max-w-7xl px-8 py-8` `<main>`) so
 * the above-the-fold geometry the gates assert (gauge + label `bottom ≤ 720`
 * @1280×720, etc.) matches the real page's vertical offsets.
 */

/**
 * The Section 5 fixtures addressable by the `[fixture]` segment. `a` → strong
 * match (score 85, the flagship success state); `b` → partial match (52);
 * `c` → degenerate (0/0, the Empty_Result_State trigger). The visual gates use
 * `a` and `c` (design Section 9.1). Kept in sync with the server page's
 * validation map.
 */
const FIXTURES: Record<string, MatchResponse> = {
  a: matchStrong,
  b: matchPartial,
  c: matchDegenerate,
};

/** Whether a match is the degenerate 0/0 result (renders EmptyResultState). */
function isDegenerateMatch(match: MatchResponse): boolean {
  const breakdown = match.score_breakdown;
  return (
    breakdown.similarity_component === 0 &&
    breakdown.keyword_coverage_component === 0
  );
}

export interface HarnessResultsClientProps {
  /** The validated fixture key (`a` | `b` | `c`) from the route segment. */
  fixture: string;
}

export function HarnessResultsClient({
  fixture,
}: HarnessResultsClientProps): React.JSX.Element {
  // Resolve the fixture client-side, keyed by the string the server validated.
  // The server already guaranteed this key exists (else it called notFound()).
  const match = FIXTURES[fixture];

  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      {/* Mirrors the authenticated shell top bar (same height/border) so the
          above-the-fold geometry matches the real page. */}
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <span className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-xl font-semibold tracking-tight text-transparent">
            MatchLayer
          </span>
        </div>
      </header>

      <main
        id="main"
        className="mx-auto w-full max-w-7xl flex-1 px-8 py-8 outline-none"
      >
        {match !== undefined && isDegenerateMatch(match) ? (
          // A degenerate 0/0 result renders the EmptyResultState — exactly the
          // branch `ResultsView` takes (design Section 8.1, Req 12.5) — so the
          // gate exercises the same valid-but-empty surface, never the danger
          // ErrorState.
          <div className="py-16">
            <EmptyResultState />
          </div>
        ) : (
          match !== undefined && <ResultsContent match={match} />
        )}
      </main>
    </div>
  );
}
