"use client";

import { usePathname, useRouter } from "next/navigation";
import * as React from "react";

import { getAccessToken, setAccessToken, useAuth } from "@/lib/auth";

interface AppShellUser {
  id: string;
  email: string;
  display_name: string;
}

interface AppShellClientProps {
  /**
   * Access token acquired by the server-side session check, or `null` when the
   * server could not verify a session. The server CAN verify when the browser
   * and API share an origin (production); it canNOT in split-origin local dev
   * (web :3000, API :8000), where the refresh cookie lives on the API origin
   * and never reaches the Next.js server. See `(app)/layout.tsx`.
   */
  accessToken: string | null;
  /** User from the server-side check, or `null` when it couldn't verify. */
  user: AppShellUser | null;
  children: React.ReactNode;
}

/**
 * Client wrapper that finalizes auth gating and renders the app chrome.
 *
 * Two paths:
 *
 *   1. Server already verified (same-origin): `accessToken`/`user` are
 *      non-null. We inject the token into the closure store and render
 *      immediately, exactly as before.
 *
 *   2. Server could not verify (split-origin dev): both props are `null`. The
 *      browser, however, still holds the `matchlayer_refresh` cookie on the
 *      API origin, so the client can recover the session by calling the API
 *      directly. We:
 *        - reuse an access token already in the in-memory closure (set by the
 *          login form moments earlier — the common case right after sign-in),
 *          or
 *        - run a silent `refresh()` against the API (the browser sends its
 *          cookie to :8000 with `credentials: "include"`).
 *      While that check is in flight we render a lightweight loading state. If
 *      it resolves to a signed-in user, we render the shell; if not, we
 *      redirect to `/login?next=<path>` on the client.
 *
 * This preserves the production security model (same-origin still verifies
 * server-side and never renders protected chrome unauthenticated) while making
 * the split-origin dev flow work.
 */
export function AppShellClient({
  accessToken,
  user,
  children,
}: AppShellClientProps): React.JSX.Element {
  const router = useRouter();
  const pathname = usePathname();
  const { user: clientUser, isAuthenticated, isLoading, refresh } = useAuth();

  // When the server verified the session, seed the in-memory token so leaf
  // components and `apiFetch` can attach the Bearer header on first paint.
  React.useEffect(() => {
    if (accessToken !== null) {
      setAccessToken(accessToken);
    }
  }, [accessToken]);

  // Split-origin recovery: if the server couldn't verify AND there's no token
  // already in memory, attempt one silent refresh against the API directly.
  const serverVerified = accessToken !== null;

  // Lazily seed `recoveryTried` from the initial token presence: if a token is
  // already in the closure at mount (the login form set it just before this
  // navigation), recovery is effectively already done and we skip the refresh
  // round-trip — without a synchronous setState inside an effect.
  const [recoveryTried, setRecoveryTried] = React.useState<boolean>(
    () => serverVerified || getAccessToken() !== null,
  );

  // Ensures the silent-refresh recovery fires at most once for this mount,
  // independent of `refresh`'s changing function identity across renders.
  const recoveryStartedRef = React.useRef(false);

  React.useEffect(() => {
    // Nothing to do when the server verified, or when recovery is already
    // resolved (token present at mount → `recoveryTried` initialized true).
    if (serverVerified || recoveryTried || recoveryStartedRef.current) {
      return;
    }
    // Guard so recovery runs EXACTLY once: `refresh` from `useAuth()` is a new
    // function identity every render, so without this ref the effect would
    // re-fire on each re-render and hammer `/refresh` (the 403 flood).
    recoveryStartedRef.current = true;
    let cancelled = false;
    void (async () => {
      try {
        await refresh();
      } finally {
        if (!cancelled) {
          // Asynchronous (post-await) state update — not a synchronous
          // cascading render.
          setRecoveryTried(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // `refresh` is included for exhaustive-deps correctness; the
    // `recoveryStartedRef` guard above makes the effect body run only once
    // regardless of `refresh` identity, so re-runs are harmless no-ops.
  }, [serverVerified, recoveryTried, refresh]);

  // Decide whether the client considers the user authenticated. When the
  // server verified, that's authoritative. Otherwise we trust the in-memory
  // token / `useAuth` once recovery has been attempted.
  const clientAuthed = isAuthenticated || getAccessToken() !== null;
  const resolved = serverVerified || recoveryTried;

  // If recovery finished and the client still has no session, redirect to
  // /login (client-side) preserving where the user was headed.
  React.useEffect(() => {
    if (serverVerified) {
      return;
    }
    if (resolved && !clientAuthed && !isLoading) {
      const next = encodeURIComponent(pathname || "/");
      router.replace(`/login?next=${next}`);
    }
  }, [serverVerified, resolved, clientAuthed, isLoading, pathname, router]);

  // While the client is still verifying (split-origin path), show a minimal
  // loading state rather than flashing protected chrome or the login redirect.
  if (!serverVerified && (!resolved || isLoading)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg text-text-muted">
        <span className="text-sm">Loading…</span>
      </div>
    );
  }

  // If unauthenticated after recovery, render nothing (the effect above is
  // redirecting to /login).
  if (!serverVerified && !clientAuthed) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg text-text-muted">
        <span className="text-sm">Redirecting…</span>
      </div>
    );
  }

  // Prefer the server-provided user; fall back to the client `useAuth` user
  // (populated by the recovery refresh + the `/me` query) in the dev path.
  const displayUser = user ?? clientUser;

  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <span className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-xl font-semibold tracking-tight text-transparent">
            MatchLayer
          </span>
          <div className="flex items-center gap-4">
            {displayUser ? (
              <span className="text-sm text-text-muted">
                {displayUser.display_name}
              </span>
            ) : null}
            <SignOutButton />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-6 py-8">
        {children}
      </main>
    </div>
  );
}

function SignOutButton(): React.JSX.Element {
  const router = useRouter();
  const { signOut } = useAuth();
  return (
    <button
      onClick={async () => {
        await signOut();
        router.replace("/login");
      }}
      className="rounded-xl border border-border-strong px-3 py-1.5 text-sm text-text-muted transition-colors hover:border-danger hover:text-danger"
    >
      Sign out
    </button>
  );
}
