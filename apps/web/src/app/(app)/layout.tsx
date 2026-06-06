import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import * as React from "react";

import { SkipNav } from "@/components/skip-nav";
import { verifySessionFromRefreshCookie } from "@/lib/auth-server";

import { AppShellClient } from "./shell-client";

/**
 * Non-indexing control for the authenticated/PII surface (Requirement 15.1,
 * 15.2; `seo.md`; ADR 0006). Exporting `robots: { index: false, follow: false }`
 * from this route-group layout makes every nested authenticated route — the
 * Upload_Page, Results_Page, and Library_View — inherit a `noindex, nofollow`
 * directive via the Next.js Metadata API. This is a privacy control (resume
 * text, job descriptions, and match results must never be crawled or indexed),
 * not just an SEO one, so no sitemap/canonical/Open Graph metadata is added to
 * any `(app)` route.
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

/**
 * The Authenticated_Shell layout reads the request cookies and headers to
 * verify the session every render — it is, by definition, request-scoped.
 * Forcing dynamic rendering tells Next.js (Turbopack on 16.x) to skip the
 * static-generation pass for every route nested under this layout, which
 * would otherwise try to import the `"use client"` `lib/auth.ts` module
 * during prerender and fail with `verifySessionFromRefreshCookie is on the
 * client`. The route is dynamic anyway because we call `cookies()` and
 * `headers()`; this export makes the contract explicit.
 */
export const dynamic = "force-dynamic";

/**
 * Authenticated_Shell layout (Server Component).
 *
 * Verifies the session server-side by forwarding the inbound refresh cookie to
 * the API (design §13.5). This works cleanly when the web app and API are
 * same-origin (production behind one domain): the browser sends the
 * `matchlayer_refresh` cookie to the Next.js server, which forwards it to
 * `/api/v1/auth/refresh` and hands the fresh token to the client tree.
 *
 * Split-origin local dev (web on :3000, API on :8000) is different: the API
 * sets the refresh cookie on the API origin (:8000), so the browser never
 * sends it to the Next.js server (:3000). The server-side check therefore
 * can't see a session that genuinely exists. Hard-redirecting here would trap
 * a just-logged-in user in a /login ↔ /dashboard loop.
 *
 * So the gate degrades by intent:
 *   - server CAN verify (cookie present, same-origin) → render the shell with
 *     the server-acquired token, exactly as designed;
 *   - server canNOT verify → render the shell WITHOUT a server token and let
 *     the client (`AppShellClient` + `useAuth`) verify against the API
 *     directly (the browser can reach :8000 and send its cookie there). If the
 *     client has no session either, it redirects to /login on the client.
 *
 * This keeps the production security posture identical (same-origin still
 * verifies server-side) while making split-origin dev usable.
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}): Promise<React.JSX.Element> {
  const session = await verifySessionFromRefreshCookie({
    headers,
    cookies,
  });

  return (
    <>
      {/*
       * Skip-navigation link (Req 19.8; design Section 10.3): the first
       * focusable element in the authenticated surface, rendered ahead of the
       * `AppShellClient` chrome (the `<header>` brand bar and sign-out
       * control) so keyboard users reach it before any repeated chrome. It
       * targets the `<main id="main">` landmark rendered inside
       * `AppShellClient`. Kept here in the layout (rather than the client
       * shell) so it precedes every interactive element and stays a static,
       * server-rendered anchor — it has no client-side behavior of its own.
       */}
      <SkipNav />
      <AppShellClient
        accessToken={session?.accessToken ?? null}
        user={session?.user ?? null}
      >
        {children}
      </AppShellClient>
    </>
  );
}
