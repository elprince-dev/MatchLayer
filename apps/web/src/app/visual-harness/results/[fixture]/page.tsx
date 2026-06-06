import type { Metadata } from "next";
import { notFound } from "next/navigation";
import * as React from "react";

import { HarnessResultsClient } from "./harness-results-client";

/**
 * Env-gated **visual-harness** route for the ATS Results flagship gates
 * (task 9.1; design Section 9.1, 9.3).
 *
 * ## Why this route exists
 * The real Results page (`(app)/matches/[id]`) sits behind the `(app)` auth
 * shell and fetches its data through `apiFetch` over a live API. That is the
 * wrong surface for a deterministic **visual/layout** acceptance gate: the gate
 * cares about the rendered LAYOUT, not the fetch path or the session lifecycle.
 * This route renders the **exact same** success composition (`ResultsContent`)
 * and degenerate composition (`EmptyResultState`) that `ResultsView` renders —
 * imported directly from the production component — fed a Section 5 fixture,
 * with **no network call and no auth**. Because it reuses the real components,
 * the gate can never drift from production layout.
 *
 * ## Server gate → client renderer split (no RSC payload leak)
 * This Server Component owns only the things that MUST run on the server: the
 * env gate, `notFound()`, the `metadata` export, and reading + validating the
 * `[fixture]` segment. It then hands the `{@link HarnessResultsClient}` island
 * **only the small validated `fixture` string** — never the resolved
 * `MatchResponse`. The client island imports the fixture object itself.
 *
 * This split is deliberate and load-bearing for the Section 9.3 #7/#8 gates: if
 * the server passed the resolved match object down as a prop, Next.js would
 * serialize it into the inline RSC flight payload in the HTML, leaking raw
 * field names (`similarity_component`, …) into `page.content()` and tripping
 * the no-raw-fields assertion — even with a visually clean DOM. Resolving the
 * fixture client-side keeps the object in client memory only, exactly as the
 * production `ResultsView` (which fetches over XHR) does. See the client
 * island's docstring for the full rationale.
 *
 * ## Inert in production (never ships)
 * The route renders **only** when `process.env.PLAYWRIGHT_VISUAL === "1"` — the
 * flag the Playwright `webServer` sets when it boots the app for the gates.
 * With the flag unset (every normal `dev`/`build`/`start`) the route calls
 * `notFound()` and is a 404, so it is inert outside the harness. It is also
 * `noindex, nofollow` (below) and is never added to `sitemap.ts` (the sitemap
 * is a `/`-only allowlist), so it can never be crawled even if it were
 * reachable. The flag is read at request time (not module load) so a single
 * built artifact can be toggled by the harness env.
 *
 * ## Reused by task 9.2
 * The env-flag + test-route mechanism is deliberately general: task 9.2's
 * Upload/Auth/Landing gates reuse the same `PLAYWRIGHT_VISUAL` flag and the
 * same `webServer` wiring (owned here) and may add sibling harness routes under
 * `app/visual-harness/*` for any surface that needs a deterministic, auth-free
 * render.
 */

/** Whether the visual harness is enabled (set by the Playwright `webServer`). */
function harnessEnabled(): boolean {
  return process.env.PLAYWRIGHT_VISUAL === "1";
}

/**
 * The Section 5 fixture keys addressable by the `[fixture]` segment. `a` →
 * strong match (score 85, the flagship success state); `b` → partial match
 * (52); `c` → degenerate (0/0, the Empty_Result_State trigger). The visual
 * gates use `a` and `c` (design Section 9.1). The actual fixture objects are
 * resolved client-side in {@link HarnessResultsClient}; the server only
 * validates that the requested key is one of these.
 */
const VALID_FIXTURES = new Set(["a", "b", "c"]);

/** Defense-in-depth: the harness must never be indexed even if it were
 *  reachable. The route is also outside every robots/sitemap allowlist. */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

/** Read the flag per request rather than statically prerendering the route. */
export const dynamic = "force-dynamic";

export default async function VisualHarnessResultsPage({
  params,
}: {
  params: Promise<{ fixture: string }>;
}): Promise<React.JSX.Element> {
  if (!harnessEnabled()) {
    // Inert outside the Playwright harness: a normal build/start serves a 404.
    notFound();
  }

  const { fixture } = await params;

  if (!VALID_FIXTURES.has(fixture)) {
    notFound();
  }

  // Hand the client island ONLY the validated string key — never the resolved
  // MatchResponse — so the fixture object is never serialized into the HTML
  // (see the route + island docstrings).
  return <HarnessResultsClient fixture={fixture} />;
}
