"use client";

import * as React from "react";

import UploadPage from "@/app/(app)/upload/page";

/**
 * Client renderer for the env-gated visual-harness Upload route (task 9.2;
 * design Section 9.2). The Server Component page (`./page.tsx`) owns
 * env-gating, `notFound()`, `metadata`, and `force-dynamic`; this island
 * renders the **production** `UploadPage` inside chrome that mirrors the
 * authenticated `(app)` shell.
 *
 * ## Why render the real `UploadPage`
 * The gate must measure the same layout users get. `UploadPage` is a
 * `"use client"` component that owns its own form state and only issues a
 * network request once the user picks a file or submits — so in its initial
 * idle state (the state the visual gate captures) it makes no API call and
 * needs no session. Importing and rendering it directly here reproduces the
 * real page's layout exactly, without the `(app)` auth shell's server-side
 * session gate (which would otherwise render a "Loading…"/redirect state).
 *
 * ## Chrome mirrors the authenticated shell
 * The real page renders inside `(app)/shell-client.tsx`'s
 * `header → main(max-w-7xl px-6 py-8) → footer` structure. This island
 * reproduces that same landmark structure and spacing so the harness page's
 * vertical offsets and content width match production — the no-horizontal-
 * scroll and screenshot gates then reflect the real composition. The header is
 * link-free chrome (no sign-out button) because there is no session here.
 */
export function HarnessUploadClient(): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      {/* Mirrors the authenticated shell top bar (same height/border). */}
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <span className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-xl font-semibold tracking-tight text-transparent">
            MatchLayer
          </span>
        </div>
      </header>

      {/* Same landmark + width/padding the (app) shell wraps page content in. */}
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-7xl flex-1 px-6 py-8 outline-none"
      >
        <UploadPage />
      </main>

      <footer className="border-t border-border px-6 py-6">
        <p className="mx-auto max-w-7xl text-sm text-text-subtle">
          MatchLayer — ATS simulation and resume-match analysis.
        </p>
      </footer>
    </div>
  );
}
