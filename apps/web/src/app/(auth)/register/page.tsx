"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { FormError } from "@/components/auth/form-error";
import { RetryAfterMessage } from "@/components/auth/retry-after-message";
import { apiBaseUrl } from "@/lib/api";
import { setAccessToken } from "@/lib/auth";

export default function RegisterPage(): React.JSX.Element {
  const router = useRouter();
  const [error, setError] = React.useState<string | null>(null);
  const [retryAfter, setRetryAfter] = React.useState<number | null>(null);
  const [loading, setLoading] = React.useState(false);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setRetryAfter(null);
    setLoading(true);

    const form = new FormData(e.currentTarget);
    const body = {
      email: form.get("email") as string,
      password: form.get("password") as string,
      display_name: (form.get("display_name") as string) || undefined,
    };

    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/auth/register`, {
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

      if (res.status === 422) {
        const data = await res.json();
        setError(data.detail || "Validation error.");
        return;
      }

      if (res.ok) {
        const data = await res.json();
        if (data.access_token) {
          setAccessToken(data.access_token);
          // Land the new user in the app on the core action (upload + match),
          // so a successful sign-up has an obvious, useful next step rather
          // than dropping back on the marketing landing with no feedback.
          router.push("/upload");
        } else {
          // Enumeration defense path — same UX as success.
          router.push("/upload");
        }
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
        <h1 className="text-xl font-semibold text-text">Create an account</h1>
        <p className="mt-1 text-sm text-text-muted">
          Already have an account?{" "}
          <a href="/login" className="text-brand hover:underline">
            Sign in
          </a>
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
            minLength={12}
            autoComplete="new-password"
            className="mt-1 block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        <div>
          <label
            htmlFor="display_name"
            className="block text-sm font-medium text-text"
          >
            Display name <span className="text-text-subtle">(optional)</span>
          </label>
          <input
            id="display_name"
            name="display_name"
            type="text"
            autoComplete="name"
            className="mt-1 block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        {error && <FormError>{error}</FormError>}
        {retryAfter && <RetryAfterMessage seconds={retryAfter} />}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-xl bg-brand px-4 py-2 font-medium text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
        >
          {loading ? "Creating account…" : "Create account"}
        </button>
      </form>
    </div>
  );
}
