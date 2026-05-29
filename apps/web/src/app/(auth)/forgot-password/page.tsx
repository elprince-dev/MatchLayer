"use client";

import * as React from "react";

import { RetryAfterMessage } from "@/components/auth/retry-after-message";
import { apiBaseUrl } from "@/lib/api";

export default function ForgotPasswordPage(): React.JSX.Element {
  const [submitted, setSubmitted] = React.useState(false);
  const [retryAfter, setRetryAfter] = React.useState<number | null>(null);
  const [loading, setLoading] = React.useState(false);

  const isLocalDev =
    apiBaseUrl.includes("localhost") || apiBaseUrl.includes("127.0.0.1");

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setRetryAfter(null);
    setLoading(true);

    const form = new FormData(e.currentTarget);
    const body = { email: form.get("email") as string };

    try {
      const res = await fetch(
        `${apiBaseUrl}/api/v1/auth/password-reset/request`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(body),
        },
      );

      if (res.status === 429) {
        const ra = res.headers.get("Retry-After");
        setRetryAfter(ra ? Math.ceil(Number(ra)) : 60);
        return;
      }

      // Always show success regardless of whether email matched (Req 5.2).
      setSubmitted(true);
    } catch {
      setSubmitted(true);
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold text-text">Check your email</h1>
        <p className="text-sm text-text-muted">
          If that email is registered, we&apos;ve sent password-reset
          instructions.
        </p>
        {isLocalDev && (
          <p className="mt-4 rounded-xl border border-border bg-bg-elevated px-3 py-2 text-xs text-text-subtle">
            <strong>Dev tip:</strong> No email is sent locally. Retrieve the
            link via{" "}
            <code className="font-mono text-brand">
              GET {apiBaseUrl}/api/v1/dev/last-reset-link
            </code>
          </p>
        )}
        <a
          href="/login"
          className="inline-block text-sm text-brand hover:underline"
        >
          Back to sign in
        </a>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h1 className="text-xl font-semibold text-text">Reset your password</h1>
        <p className="mt-1 text-sm text-text-muted">
          Enter your email and we&apos;ll send you a reset link.
        </p>
      </div>

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

        {retryAfter && <RetryAfterMessage seconds={retryAfter} />}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl bg-brand px-4 py-2 font-medium text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
        >
          {loading ? "Sending…" : "Send reset link"}
        </button>
      </form>

      <p className="text-center text-sm text-text-muted">
        <a href="/login" className="text-brand hover:underline">
          Back to sign in
        </a>
      </p>
    </div>
  );
}
