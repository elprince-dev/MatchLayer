import * as React from "react";

import { ResultsView } from "@/components/results/results-view";

/**
 * ATS Results page shell — `(app)/matches/[id]` (design Section 6.3, 8.1;
 * Req 13.1–13.6, 21.7, 21.10).
 *
 * This is a **Server Component** (no `'use client'`, Req 21.7): its only job is
 * to read the dynamic `[id]` route segment and hand it to the
 * `'use client'` {@link ResultsView}, which owns the data fetch, the score
 * reveal, and the loading / error / empty / success state machine. Keeping the
 * shell server-side means the page itself ships no client JavaScript beyond the
 * view island, matching the design's "Server Component shell → client view"
 * split (Section 6.3 component hierarchy).
 *
 * ## Route param (Next.js 16)
 * In the App Router on Next 16 a dynamic page's `params` is a `Promise`, so it
 * is `await`ed here before reading `id` — the established pattern for this app's
 * server route shells.
 *
 * ## Layout (design Section 8.1)
 * The surrounding `(app)` layout already centers content in a `max-w-7xl`
 * `<main>` landmark. This shell adds the section's horizontal padding (`px-8`,
 * per the Section 8.1 spec) and renders the view; the **two-column desktop
 * composition** — gauge + label on the left, breakdown on the right, collapsing
 * to a single column on mobile — is realized inside `ResultsView`'s success
 * content, which is the only state where both columns exist.
 *
 * ## Non-indexing (Req 13.3, 21.7; seo.md)
 * No `metadata`/`robots` export here: the route inherits
 * `robots: { index: false, follow: false }` from the `(app)` route-group
 * layout, and the security-headers proxy sets `X-Robots-Tag: noindex, nofollow`
 * on the response. Adding any sitemap/canonical/OG metadata to this PII surface
 * is prohibited, so the shell deliberately adds none.
 *
 * ## No non-existent navigation (Req 13.2)
 * The shell renders only the results view. The single forward action is the
 * "Analyze another job" CTA → `/upload` inside the view; there is no dashboard,
 * history, or analytics navigation anywhere on the page.
 */
export default async function MatchResultPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<React.JSX.Element> {
  const { id } = await params;

  return (
    <div className="mx-auto w-full max-w-7xl px-8">
      <ResultsView id={id} />
    </div>
  );
}
