"use client";

import * as React from "react";

import { setAccessToken, useAuth } from "@/lib/auth";

interface AppShellClientProps {
  accessToken: string;
  user: { id: string; email: string; display_name: string };
  children: React.ReactNode;
}

/**
 * Client wrapper that injects the server-acquired access token into the
 * closure store and renders the app chrome.
 */
export function AppShellClient({
  accessToken,
  user,
  children,
}: AppShellClientProps): React.JSX.Element {
  // Inject the access token on mount (from the server-side refresh).
  React.useEffect(() => {
    setAccessToken(accessToken);
  }, [accessToken]);

  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="border-b border-border px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <span className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-xl font-semibold tracking-tight text-transparent">
            MatchLayer
          </span>
          <div className="flex items-center gap-4">
            <span className="text-sm text-text-muted">{user.display_name}</span>
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
  const { signOut } = useAuth();
  return (
    <button
      onClick={() => signOut()}
      className="rounded-xl border border-border-strong px-3 py-1.5 text-sm text-text-muted transition-colors hover:border-danger hover:text-danger"
    >
      Sign out
    </button>
  );
}
