"use client";

import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { FormError } from "@/components/auth/form-error";
import { apiBaseUrl } from "@/lib/api";

/**
 * Inner client component that consumes `useSearchParams()`.
 *
 * Next.js 16 (App Router, Turbopack) requires every component that calls
 * `useSearchParams()` to live inside a `<Suspense>` boundary at the page
 * level; without one, `next build` fails the page's static generation
 * with `missing-suspense-with-csr-bailout`. Splitting the body into this
 * inner component lets the page-level export wrap only the part that
 * needs CSR fallback while keeping the surrounding chrome SSR-friendly.
 */
function ResetPasswordPageInner(): React.JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  if (!token) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold text-text">Reset password</h1>
        <p className="text-sm text-text-muted">
          This page is for confirming a password reset. Open the link from your
          reset email.
        </p>
        <a
          href="/forgot-password"
          className="inline-block text-sm text-brand hover:underline"
        >
          Request a reset link
        </a>
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const form = new FormData(e.currentTarget);
    const newPassword = form.get("new_password") as string;
    const confirmPassword = form.get("confirm_password") as string;

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      setLoading(false);
      return;
    }

    try {
      const res = await fetch(
        `${apiBaseUrl}/api/v1/auth/password-reset/confirm`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ token, new_password: newPassword }),
        },
      );

      if (res.status === 204) {
        router.push("/login?just-reset=1");
        return;
      }

      if (res.status === 400) {
        setError(
          "This password-reset link is invalid or expired. Request a new one.",
        );
        return;
      }

      if (res.status === 422) {
        const data = await res.json();
        setError(data.detail || "Validation error.");
        return;
      }

      setError("Something went wrong. Please try again.");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold text-text">Set a new password</h1>
        <p className="mt-1 text-sm text-text-muted">
          Choose a strong password (at least 12 characters).
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="new_password"
            className="block text-sm font-medium text-text"
          >
            New password
          </label>
          <input
            id="new_password"
            name="new_password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            className="mt-1 block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        <div>
          <label
            htmlFor="confirm_password"
            className="block text-sm font-medium text-text"
          >
            Confirm password
          </label>
          <input
            id="confirm_password"
            name="confirm_password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            className="mt-1 block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        {error && <FormError>{error}</FormError>}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl bg-brand px-4 py-2 font-medium text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
        >
          {loading ? "Resetting…" : "Reset password"}
        </button>
      </form>
    </div>
  );
}

export default function ResetPasswordPage(): React.JSX.Element {
  return (
    <React.Suspense fallback={<ResetPasswordPageFallback />}>
      <ResetPasswordPageInner />
    </React.Suspense>
  );
}

function ResetPasswordPageFallback(): React.JSX.Element {
  return (
    <div className="space-y-4 text-center">
      <h1 className="text-xl font-semibold text-text">Reset password</h1>
      <p className="text-sm text-text-muted">Loading…</p>
    </div>
  );
}
