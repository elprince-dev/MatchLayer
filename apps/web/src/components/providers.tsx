"use client";

import {
  QueryClient,
  QueryClientProvider,
  isServer,
} from "@tanstack/react-query";
import * as React from "react";

/**
 * Client-side providers for the MatchLayer web app.
 *
 * Hosts the TanStack Query `QueryClientProvider` that `useAuth()` (and any
 * future server-state hook) depends on — without it, `useQueryClient()` throws
 * "No QueryClient set, use QueryClientProvider to set one". This is mounted
 * once near the root (`app/layout.tsx`) so every route — public and
 * authenticated — has a client available.
 *
 * QueryClient construction follows the Next.js App Router guidance:
 *
 *   - On the server, always create a fresh `QueryClient` per request so state
 *     never leaks between users/requests.
 *   - In the browser, create the client once and reuse it across renders
 *     (module-singleton via `browserQueryClient`) so navigations don't discard
 *     the cache. Creating it lazily (not at module top-level) avoids sharing a
 *     client if React suspends during the initial render.
 */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // A small stale window cuts redundant refetches on quick navigations
        // without masking real updates; `useAuth`'s /me query sets its own.
        staleTime: 30_000,
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  if (isServer) {
    // Server: always a brand-new client per request.
    return makeQueryClient();
  }
  // Browser: reuse the singleton across renders/navigations.
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

export function Providers({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  const queryClient = getQueryClient();
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
