"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
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
 * Client-side validation schema for the registration form (Req 8.4, design §8.3).
 *
 * The acceptance criteria name three checks for the register view:
 *   - email: present + RFC-ish format (`z.string().email()`),
 *   - password: minimum length of 12 characters,
 *   - confirm-password: must equal `password`.
 *
 * The confirm-match check is expressed with `.superRefine` rather than a
 * `z.object` field rule so the issue can be attached to the `confirmPassword`
 * `path`. React Hook Form then surfaces it through `errors.confirmPassword`,
 * which renders inline adjacent to the confirm field (Req 8.4) rather than as
 * a form-level error — the mismatch is a property of that one field.
 *
 * `confirmPassword` is a purely client-side concern with **no** API
 * representation (the backend `RegisterRequest` is `{ email, password,
 * display_name? }`), so per `conventions.md` ("Handwritten Zod schemas only
 * for purely client-side state that has no API representation") this schema is
 * authored here rather than generated from the OpenAPI contract. The server
 * (FastAPI + Pydantic) remains authoritative for `email`/`password`; this is
 * the "early-failure" UX layer. Messages are complete, plain-language
 * sentences so the `aria-live` field announcement reads cleanly.
 *
 * Note on `display_name`: the backend field is optional and defaults to the
 * email's local part (Req 1.7; `RegisterRequest.display_name?: string | null`
 * in `@matchlayer/shared-types`). The redesign's register view is the
 * three-field shape — email, password, confirm-password (Req 8.1, design
 * §8.3) — so the form omits `display_name` entirely and lets the backend
 * default apply.
 */
const registerSchema = z
  .object({
    email: z
      .string()
      .min(1, "Email is required.")
      .email("Enter a valid email address."),
    password: z.string().min(12, "Password must be at least 12 characters."),
    confirmPassword: z.string().min(1, "Please confirm your password."),
  })
  .superRefine((values, ctx) => {
    if (values.confirmPassword !== values.password) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Passwords do not match.",
        path: ["confirmPassword"],
      });
    }
  });

type RegisterValues = z.infer<typeof registerSchema>;

export default function RegisterPage(): React.JSX.Element {
  const router = useRouter();

  // Banner shown above the form. Holds a generic, non-enumerable server/network
  // error — never anything that distinguishes "email already exists" from any
  // other failure (Req 8.11; security.md "no account enumeration").
  const [bannerError, setBannerError] = React.useState<string | null>(null);
  const [retryAfter, setRetryAfter] = React.useState<number | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: "", password: "", confirmPassword: "" },
  });

  async function onValid(values: RegisterValues): Promise<void> {
    setBannerError(null);
    setRetryAfter(null);

    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/auth/register`, {
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

      if (res.ok) {
        // Enumeration defense (Req 8.11; security.md). A genuine new-account
        // success returns a token pair; the "email already exists" path is
        // designed to return 2xx WITHOUT a token so the response is
        // indistinguishable from success. Both navigate to /upload, so the
        // client cannot tell whether the email was already registered —
        // exactly the success/already-exists indistinguishability the
        // criterion requires. We attach the token only when present.
        let data: unknown = null;
        try {
          data = await res.json();
        } catch {
          data = null;
        }
        const token =
          data !== null &&
          typeof data === "object" &&
          "access_token" in data &&
          typeof (data as { access_token: unknown }).access_token === "string"
            ? (data as { access_token: string }).access_token
            : null;
        if (token !== null) {
          setAccessToken(token);
        }
        // Post-auth destination (Req 8.12): the Upload_Page — the first screen
        // of the core authenticated flow.
        router.push("/upload");
        return;
      }

      // Any other non-OK status (including 5xx server errors): a generic,
      // non-enumerable message above the form (Req 8.11). The entered email
      // stays in the field because the uncontrolled RHF inputs are never reset
      // on this path.
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
          Create an account
        </h1>
        <p className="mt-1 text-sm text-text-muted">
          Already have an account?{" "}
          <a href="/login" className="text-brand hover:underline">
            Sign in
          </a>
        </p>
      </header>

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
            autoComplete="new-password"
            {...register("password")}
            aria-invalid={errors.password ? true : undefined}
            aria-describedby={errors.password ? "password-error" : undefined}
            className="mt-1"
          />
          <FieldError id="password-error">
            {errors.password?.message}
          </FieldError>
        </div>

        <div>
          <Label htmlFor="confirm-password">Confirm password</Label>
          <Input
            id="confirm-password"
            type="password"
            autoComplete="new-password"
            {...register("confirmPassword")}
            aria-invalid={errors.confirmPassword ? true : undefined}
            aria-describedby={
              errors.confirmPassword ? "confirm-password-error" : undefined
            }
            className="mt-1"
          />
          <FieldError id="confirm-password-error">
            {errors.confirmPassword?.message}
          </FieldError>
        </div>

        <Button
          type="submit"
          disabled={isSubmitting}
          className="h-11 w-full text-base"
        >
          {isSubmitting ? "Creating account…" : "Create account"}
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
