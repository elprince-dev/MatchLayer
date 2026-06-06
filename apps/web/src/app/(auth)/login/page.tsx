"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { FieldError, FormError } from "@/components/auth/form-error";
import { RetryAfterMessage } from "@/components/auth/retry-after-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiBaseUrl } from "@/lib/api";
import { setAccessToken } from "@/lib/auth";
import { cn } from "@/lib/utils";

/**
 * Client-side validation schema for the login form (Req 8.4, design §8.3).
 *
 * Only the two checks the acceptance criteria name for the login view:
 *   - email: present + RFC-ish format (`z.string().email()`),
 *   - password: minimum length of 12 characters.
 *
 * The server (FastAPI + Pydantic) remains authoritative — this is purely the
 * "early-failure" UX layer (`conventions.md`: client Zod is for UX, not
 * security). Messages are written for a screen reader: each is a complete,
 * plain-language sentence so the `aria-live` field announcement reads cleanly.
 */
const loginSchema = z.object({
  email: z
    .string()
    .min(1, "Email is required.")
    .email("Enter a valid email address."),
  password: z.string().min(12, "Password must be at least 12 characters."),
});

type LoginValues = z.infer<typeof loginSchema>;

/**
 * Inner client component that consumes `useSearchParams()`.
 *
 * Next.js 16 (App Router, Turbopack) requires every component that calls
 * `useSearchParams()` to live inside a `<Suspense>` boundary at the page
 * level — without one, `next build` fails the page's static generation with
 * `missing-suspense-with-csr-bailout`. We isolate the search-param consumer
 * here so the page-level export can wrap exactly the part that needs CSR
 * fallback, while the surrounding chrome can still render in the SSR pass.
 */
function LoginPageInner(): React.JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Banner shown above the form. Holds the non-enumerable invalid-credentials
  // message, the locked-account message, or a generic server/network error —
  // never anything that distinguishes "user not found" from "wrong password"
  // (Req 8.11; security.md "no account enumeration").
  const [bannerError, setBannerError] = React.useState<string | null>(null);
  const [retryAfter, setRetryAfter] = React.useState<number | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const next = searchParams.get("next");
  const justReset = searchParams.get("just-reset");

  // Post-auth destination (Req 8.12): default to the Upload_Page — the first
  // screen of the core authenticated flow — rather than the marketing landing.
  // A `next` param is honoured ONLY when it is a safe, same-origin, in-app
  // path: it must start with a single "/" (rejecting protocol-relative
  // "//evil.com") and contain no "://" (rejecting absolute URLs), so an
  // attacker can't use it as an open-redirect. Anything else falls back to
  // `/upload`. (`/dashboard`/library etc. are out of MVP scope but, being
  // legitimate same-origin paths, are still safe to honour if explicitly
  // requested.)
  const safeNext =
    next &&
    next.startsWith("/") &&
    !next.startsWith("//") &&
    !decodeURIComponent(next).includes("://")
      ? next
      : "/upload";

  async function onValid(values: LoginValues): Promise<void> {
    setBannerError(null);
    setRetryAfter(null);

    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: values.email,
          password: values.password,
        }),
      });

      if (res.status === 429) {
        const ra = res.headers.get("Retry-After");
        setRetryAfter(ra ? Math.ceil(Number(ra)) : 60);
        return;
      }

      if (res.status === 423) {
        setBannerError("Account is temporarily locked. Try again later.");
        return;
      }

      if (res.status === 401) {
        // Identical wording for "user not found" and "wrong password"
        // (Req 8.11). The entered email stays in the field because the
        // uncontrolled RHF inputs are never reset on this path.
        setBannerError("Email or password is incorrect.");
        return;
      }

      if (res.ok) {
        const data = await res.json();
        setAccessToken(data.access_token);
        router.push(safeNext);
        return;
      }

      // Any other non-OK status (including 5xx server errors): a generic,
      // non-enumerable message above the form (Req 8.11).
      setBannerError("Something went wrong. Please try again.");
    } catch {
      setBannerError("Network error. Please try again.");
    }
  }

  // The single banner node: the rate-limit countdown takes precedence (it is
  // the most actionable), otherwise the text error. Kept as one value so the
  // always-mounted `<FormError>` live region announces content changes without
  // remounting (a freshly-mounted live region's first content is often missed).
  const banner: React.ReactNode =
    retryAfter !== null ? (
      <RetryAfterMessage seconds={retryAfter} />
    ) : (
      bannerError
    );
  const hasBanner = retryAfter !== null || bannerError !== null;

  return (
    <div className="space-y-6">
      <header className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-text">
          Sign in
        </h1>
        <p className="mt-1 text-sm text-text-muted">
          Don&apos;t have an account?{" "}
          <a href="/register" className="text-brand hover:underline">
            Create one
          </a>
        </p>
      </header>

      {justReset === "1" && (
        <p className="rounded-xl bg-success/10 px-3 py-2 text-center text-sm text-success">
          Password reset successful. Sign in with your new password.
        </p>
      )}

      {/*
       * Non-enumerable error banner ABOVE the form (Req 8.11, design §8.3).
       * `FormError` is always mounted (empty when there is no error) so its
       * `aria-live="polite"` region announces a later error as a mutation of
       * an existing region. Box chrome is applied only when populated so an
       * empty region is visually absent.
       */}
      <FormError
        className={cn(
          hasBanner
            ? "rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-center"
            : undefined,
        )}
      >
        {banner}
      </FormError>

      <form onSubmit={handleSubmit(onValid)} noValidate className="space-y-4">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            {...register("email")}
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? "email-error" : undefined}
            className="mt-1"
          />
          <FieldError id="email-error">{errors.email?.message}</FieldError>
        </div>

        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            {...register("password")}
            aria-invalid={errors.password ? true : undefined}
            aria-describedby={errors.password ? "password-error" : undefined}
            className="mt-1"
          />
          <FieldError id="password-error">
            {errors.password?.message}
          </FieldError>
        </div>

        <Button
          type="submit"
          disabled={isSubmitting}
          className="h-11 w-full text-base"
        >
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      {/* Trust links below the form (Req 8.2). */}
      <p className="text-center text-xs text-text-muted">
        <a href="/privacy" className="hover:text-text hover:underline">
          Privacy policy
        </a>
        {" · "}
        <a href="/terms" className="hover:text-text hover:underline">
          Terms of service
        </a>
      </p>
    </div>
  );
}

export default function LoginPage(): React.JSX.Element {
  // Suspense boundary required by Next.js 16 around any client component that
  // reads `useSearchParams()`. The fallback mirrors the static heading so the
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
      <header className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-text">
          Sign in
        </h1>
        <p className="mt-1 text-sm text-text-muted">
          Don&apos;t have an account?{" "}
          <a href="/register" className="text-brand hover:underline">
            Create one
          </a>
        </p>
      </header>
    </div>
  );
}
