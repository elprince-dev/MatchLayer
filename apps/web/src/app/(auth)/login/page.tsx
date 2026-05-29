"use client";

import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { FormError } from "@/components/auth/form-error";
import { RetryAfterMessage } from "@/components/auth/retry-after-message";
import { apiBaseUrl } from "@/lib/api";
import { setAccessToken } from "@/lib/auth";

/**
 * Inner client component that consumes `useSearchParams()`.
 *
 * Next.js 16 (App Router, Turbopack) requires every component that calls
 * `useSearchParams()` to live inside a `<Suspense>` boundary at the page
 * level — without one, `next build` fails the page's static generation
 * with `missing-suspense-with-csr-bailout`. We isolate the search-param
 * consumer here so the page-level export can wrap exactly the part that
 * needs CSR fallback, while the surrounding chrome (heading, links) can
 * still be rendered in the SSR pass.
 */
function LoginPageInner(): React.JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = React.useState<string | null>(null);
  const [retryAfter, setRetryAfter] = React.useState<number | null>(null);
  const [loading, setLoading] = React.useState(false);

  const next = searchParams.get("next");
  const justReset = searchParams.get("just-reset");

  // Validate next param: must start with / and not contain ://
  const safeNext =
    next && next.startsWith("/") && !decodeURIComponent(next).includes("://")
      ? next
      : "/";

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setRetryAfter(null);
    setLoading(true);

    const form = new FormData(e.currentTarget);
    const body = {
      email: form.get("email") as string,
      password: form.get("password") as string,
    };

    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(body),
      });

      if (res.status === 429) {
        const ra = res.headers.get("Retry-After");
        setRetryAfter(ra ? Math.ceil(Number(ra)) : 60);
        return;
      }

      if (res.status === 401) {
        setError("Email or password is incorrect.");
        return;
      }

      if (res.status === 423) {
        setError("Account is temporarily locked. Try again later.");
        return;
      }

      if (res.ok) {
        const data = await res.json();
        setAccessToken(data.access_token);
        router.push(safeNext);
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold text-text">Sign in</h1>
        <p className="mt-1 text-sm text-text-muted">
          Don&apos;t have an account?{" "}
          <a href="/register" className="text-brand hover:underline">
            Create one
          </a>
        </p>
      </div>

      {justReset === "1" && (
        <p className="rounded-xl bg-success/10 px-3 py-2 text-center text-sm text-success">
          Password reset successful. Sign in with your new password.
        </p>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="email"
            className="block text-sm font-medium text-text"
          >
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="email"
            className="mt-1 block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        <div>
          <label
            htmlFor="password"
            className="block text-sm font-medium text-text"
          >
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            autoComplete="current-password"
            className="mt-1 block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        <div className="text-right">
          <a
            href="/forgot-password"
            className="text-sm text-brand hover:underline"
          >
            Forgot password?
          </a>
        </div>

        {error && <FormError>{error}</FormError>}
        {retryAfter && <RetryAfterMessage seconds={retryAfter} />}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl bg-brand px-4 py-2 font-medium text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export default function LoginPage(): React.JSX.Element {
  // Suspense boundary required by Next.js 16 around any client component
  // that reads `useSearchParams()`. Fallback is the same form skeleton
  // (without the just-reset banner and `next`-aware redirect) so the
  // initial paint matches the hydrated render closely.
  return (
    <React.Suspense fallback={<LoginPageFallback />}>
      <LoginPageInner />
    </React.Suspense>
  );
}

function LoginPageFallback(): React.JSX.Element {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold text-text">Sign in</h1>
        <p className="mt-1 text-sm text-text-muted">
          Don&apos;t have an account?{" "}
          <a href="/register" className="text-brand hover:underline">
            Create one
          </a>
        </p>
      </div>
    </div>
  );
}
