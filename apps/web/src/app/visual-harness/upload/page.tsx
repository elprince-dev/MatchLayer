import type { Metadata } from "next";
import { notFound } from "next/navigation";
import * as React from "react";

import { HarnessUploadClient } from "./harness-upload-client";

/**
 * Env-gated **visual-harness** route for the Upload screen gates (task 9.2;
 * design Section 9.2, 9.4; Req 9.13, 14.4, 18.1, 18.2).
 *
 * ## Why this route exists
 * The real Upload page (`(app)/upload`) sits behind the `(app)` Authenticated
 * shell, which verifies a session server-side and, when it can't, renders a
 * "Loading…"/redirect state instead of the page. That auth lifecycle is the
 * wrong surface for a deterministic **visual/layout** gate, which cares only
 * about the rendered layout of the page in its initial (idle) state. This
 * route renders the **exact same** `UploadPage` composition — imported
 * directly from the production module — with **no auth gate and no network
 * call** (the page issues no request until the user picks a file / submits),
 * so the gate measures production layout deterministically and can never drift
 * from it.
 *
 * It reuses the same `PLAYWRIGHT_VISUAL` env flag and `webServer` wiring that
 * the flagship results harness route established (see
 * `app/visual-harness/results/[fixture]/page.tsx`). Auth (`/login`,
 * `/register`) and Landing (`/`) are publicly reachable, so their gates visit
 * the real routes directly and need no harness route — only Upload, behind the
 * auth shell, needs this auth-free render surface.
 *
 * ## Server gate → client renderer split
 * This Server Component owns only the server-only concerns: the env gate,
 * `notFound()`, the `metadata` export, and `force-dynamic`. The interactive
 * `UploadPage` (a `"use client"` module) renders inside the
 * {@link HarnessUploadClient} island, wrapped in chrome that mirrors the
 * authenticated `(app)` shell's `header → main → footer` landmark structure so
 * the harness geometry matches the real page.
 *
 * ## Inert in production (never ships)
 * The route renders **only** when `process.env.PLAYWRIGHT_VISUAL === "1"` (the
 * flag the Playwright `webServer` sets). With the flag unset — every normal
 * `dev`/`build`/`start` — it calls `notFound()` and is a 404, so it is inert
 * outside the harness. It is also `noindex, nofollow` (below) and is never in
 * `sitemap.ts` (a `/`-only allowlist), so it can never be crawled.
 */

/** Whether the visual harness is enabled (set by the Playwright `webServer`). */
function harnessEnabled(): boolean {
  return process.env.PLAYWRIGHT_VISUAL === "1";
}

/** Defense-in-depth: the harness must never be indexed even if it were
 *  reachable. The route is also outside every robots/sitemap allowlist. */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

/** Read the flag per request rather than statically prerendering the route. */
export const dynamic = "force-dynamic";

export default function VisualHarnessUploadPage(): React.JSX.Element {
  if (!harnessEnabled()) {
    // Inert outside the Playwright harness: a normal build/start serves a 404.
    notFound();
  }

  return <HarnessUploadClient />;
}
