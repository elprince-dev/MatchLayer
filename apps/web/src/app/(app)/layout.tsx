import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import * as React from "react";

import { verifySessionFromRefreshCookie } from "@/lib/auth";

import { AppShellClient } from "./shell-client";

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
 * Verifies the session by forwarding the refresh cookie to the API.
 * On failure, redirects to /login?next=<current path>.
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

  if (!session) {
    const hdrs = await headers();
    const url = hdrs.get("x-url") || hdrs.get("x-invoke-path") || "/";
    redirect(`/login?next=${encodeURIComponent(url)}`);
  }

  return (
    <AppShellClient accessToken={session.accessToken} user={session.user}>
      {children}
    </AppShellClient>
  );
}
