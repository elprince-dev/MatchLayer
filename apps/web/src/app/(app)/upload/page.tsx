"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { FormError } from "@/components/auth/form-error";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { UploadWidget } from "@/components/upload/upload-widget";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { ResumeResponse } from "@matchlayer/shared-types";
import {
  CreateMatchRequestSchema,
  MatchResponseSchema,
} from "@matchlayer/shared-types";

/**
 * Upload_Page — `(app)/upload` (Req 9.8–9.13, 21.7, 21.11; design Section 8.4).
 *
 * The entry point of the Phase 1 authenticated flow: a logged-in user gets a
 * resume ready and pastes a job description, then is routed to the Results_Page
 * once the match is scored. The screen is split into two cooperating halves:
 *
 *   - **{@link UploadWidget}** owns the resume entirely (task 6.3): it posts
 *     `POST /api/v1/resumes` with an `Idempotency-Key`, validates type/size,
 *     reflects `extraction_status`, and fires `onResumeReady(resume)` **only**
 *     when `extraction_status === "succeeded"`. Its complementary
 *     `onResumeCleared` fires when a previously-ready resume stops being ready
 *     (removed, replaced, or re-uploaded to a non-succeeded state).
 *   - **this page** owns the job-description `Textarea` + live character count,
 *     the submit gating, and the `POST /api/v1/matches` → navigate step.
 *
 * ## Submit gating (Req 9.9, 9.10, 9.11)
 * "Analyze Match" is enabled **only** when a resume is `succeeded` (tracked via
 * the widget's ready/cleared callbacks) **and** the trimmed job description is
 * within the backend's 30..50,000 window. It is disabled otherwise, disabled
 * again the instant the resume is removed, and disabled while a submission is
 * in flight (where it also shows "Analyzing your resume…").
 *
 * ## Navigation (Req 9.12)
 * On a created `MatchResponse` the page navigates imperatively to
 * `/matches/{id}` using the returned `id`.
 *
 * This must be a Client Component: it owns interactive form state and navigates
 * with `useRouter().push`. The surrounding `(app)` Authenticated_Shell gates the
 * session server-side and exports `robots: { index: false, follow: false }`, so
 * this page inherits `noindex, nofollow` (Req 9.13, 21.7) and adds no
 * discoverability metadata of its own.
 *
 * Contract typing (Req 21.11, `conventions.md`): request/response shapes come
 * exclusively from the generated `@matchlayer/shared-types` Zod schemas —
 * `CreateMatchRequestSchema` for request validation and `MatchResponseSchema`
 * for parsing the response at the boundary. No request/response type is
 * hand-written here.
 *
 * Privacy (`security.md`): the job-description text is Restricted PII. Nothing
 * here logs it — there are no `console` calls — and every server-derived string
 * is rendered as a plain JSX text node (never `dangerouslySetInnerHTML`). Error
 * copy is RFC 7807-`detail`-only; raw error objects, stack traces, and envelope
 * fields (`type`/`status`/`request_id`) are never surfaced.
 */

/**
 * Trimmed job-description bounds (Req 9.8), mirroring the backend
 * `MATCHLAYER_JD_MIN_CHARS` / `MATCHLAYER_JD_MAX_CHARS` (defaults 30 / 50,000).
 *
 * These are intentionally client constants, not a `NEXT_PUBLIC_*` env var: the
 * backend stays the authoritative validator (an out-of-window value returns 422
 * `validation_error`), and introducing a new public env var solely for the
 * display bounds would add `.env.example` drift for no security gain. They drive
 * the live count + submit gating so the user gets in-bounds feedback before the
 * request is issued; the generated `CreateMatchRequestSchema` only encodes the
 * `min_length=1` request-parser floor, so the 30..50,000 window is enforced here
 * and re-enforced server-side.
 */
const JD_MIN_CHARS = 30;
const JD_MAX_CHARS = 50_000;

export default function UploadPage(): React.JSX.Element {
  const router = useRouter();

  // The latest *ready* resume (set only via the widget's `onResumeReady`, which
  // fires solely on `extraction_status === "succeeded"`), or `null` when no
  // resume is currently ready — including after a remove/replace (Req 9.10).
  const [resume, setResume] = React.useState<ResumeResponse | null>(null);
  const [jobDescription, setJobDescription] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  // Bounds are measured on the TRIMMED value (Req 9.8) so leading/trailing
  // whitespace neither counts toward the minimum nor blocks at the maximum.
  const trimmedLength = jobDescription.trim().length;
  const jdInBounds =
    trimmedLength >= JD_MIN_CHARS && trimmedLength <= JD_MAX_CHARS;
  const jdOverMax = trimmedLength > JD_MAX_CHARS;

  // "Analyze Match" is enabled only when a resume is ready AND the JD is in
  // bounds, and never while a submission is in flight (Req 9.9, 9.11).
  const canSubmit = resume !== null && jdInBounds && !submitting;

  const handleResumeReady = React.useCallback((ready: ResumeResponse): void => {
    setResume(ready);
    // A freshly-ready resume clears any stale match-creation error.
    setError(null);
  }, []);

  const handleResumeCleared = React.useCallback((): void => {
    // The resume is no longer ready (removed/replaced) — re-disable submit
    // (Req 9.10) by dropping the tracked resume.
    setResume(null);
  }, []);

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    setError(null);

    // Defensive guards: the submit button is disabled unless these already
    // hold, but a form can still be submitted programmatically, so re-check.
    if (resume === null) {
      setError("Upload a resume that's ready to analyze first.");
      return;
    }

    const jobDescriptionText = jobDescription.trim();
    // Validate against the generated schema field (Req 21.11) AND the
    // documented 30..50,000 window the schema itself doesn't encode.
    const jdField =
      CreateMatchRequestSchema.shape.job_description.safeParse(
        jobDescriptionText,
      );
    if (
      !jdField.success ||
      jobDescriptionText.length < JD_MIN_CHARS ||
      jobDescriptionText.length > JD_MAX_CHARS
    ) {
      setError(
        `Paste a job description between ${JD_MIN_CHARS.toLocaleString()} and ${JD_MAX_CHARS.toLocaleString()} characters.`,
      );
      return;
    }

    // Validate the full request shape from the generated schema before sending.
    const matchRequest = CreateMatchRequestSchema.safeParse({
      resume_id: resume.id,
      job_description: jobDescriptionText,
    });
    if (!matchRequest.success) {
      setError("We couldn't start the match. Please try again.");
      return;
    }

    setSubmitting(true);
    let navigated = false;
    try {
      const matchRes = await apiFetch("/api/v1/matches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(matchRequest.data),
      });

      if (!matchRes.ok) {
        // RFC 7807 `detail` for 404/422/429 etc. (security.md) — never a raw
        // error object or stack trace.
        setError(
          await readProblemDetail(
            matchRes,
            "We couldn't score this match. Please try again.",
          ),
        );
        return;
      }

      const matchBody = await readJson(matchRes);
      const match = MatchResponseSchema.safeParse(matchBody);
      if (!match.success) {
        setError("We couldn't read the match response. Please try again.");
        return;
      }

      // Navigate to the Results_Page using the returned id (Req 9.12).
      navigated = true;
      router.push(`/matches/${encodeURIComponent(match.data.id)}`);
    } catch {
      // Network error (server down, DNS, CORS) — friendly message only.
      setError("Network error. Please try again.");
    } finally {
      // Keep the button disabled while the navigation settles on success.
      if (!navigated) {
        setSubmitting(false);
      }
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-text">
          Analyze your resume
        </h1>
        <p className="text-text-muted">
          Upload a resume and paste the job description to see your ATS match.
        </p>
      </header>

      {/*
       * Resume half (Req 9.1–9.7, 9.10): the widget owns the upload lifecycle
       * and signals readiness via `onResumeReady` / loss of readiness via
       * `onResumeCleared`. The page tracks the ready resume to gate submit.
       */}
      <UploadWidget
        onResumeReady={handleResumeReady}
        onResumeCleared={handleResumeCleared}
      />

      <form onSubmit={handleSubmit} noValidate className="space-y-6">
        <div className="space-y-2">
          <Label htmlFor="job-description">Job description</Label>
          <Textarea
            id="job-description"
            name="job_description"
            rows={12}
            value={jobDescription}
            onChange={(event) => setJobDescription(event.target.value)}
            placeholder="Paste the full job description here…"
            aria-describedby="job-description-help"
            aria-invalid={jdOverMax ? true : undefined}
          />
          {/*
           * Guidance + live character count (Req 9.8). The count reflects the
           * trimmed length (the value the bounds are measured on) so it lines
           * up with submit enablement; `tabular-nums` keeps the digits from
           * shifting as the count changes (design Section 4 typography).
           */}
          <p
            id="job-description-help"
            className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-text-subtle"
          >
            <span>Paste the full posting for the most accurate match.</span>
            <span
              className={cn(
                "tabular-nums",
                jdOverMax ? "text-danger" : "text-text-subtle",
              )}
            >
              {trimmedLength.toLocaleString()} / {JD_MAX_CHARS.toLocaleString()}{" "}
              (min {JD_MIN_CHARS.toLocaleString()})
            </span>
          </p>
        </div>

        {/* Match-creation error stays on the page (announced via FormError's
            aria-live region); the resume-upload errors live inside the widget. */}
        <FormError>{error}</FormError>

        <Button
          type="submit"
          disabled={!canSubmit}
          className="h-11 w-full text-base sm:w-auto"
        >
          {submitting ? "Analyzing your resume…" : "Analyze Match"}
        </Button>
      </form>
    </div>
  );
}

/**
 * Read an RFC 7807 envelope's `detail` as a display-safe string. Falls back to
 * a friendly message when the body is missing, unparseable, or carries no
 * string `detail` — so the UI never renders a raw error object or stack trace
 * (security.md). `@matchlayer/shared-types` ships no generated problem schema,
 * so this is the minimal safe field read.
 */
async function readProblemDetail(
  res: Response,
  fallback: string,
): Promise<string> {
  const body = await readJson(res);
  if (body !== null && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.length > 0) {
      return detail;
    }
  }
  return fallback;
}

/** Parse a JSON response body, returning `null` instead of throwing on an
 *  empty or malformed body so callers can branch on a friendly error state. */
async function readJson(res: Response): Promise<unknown> {
  try {
    return (await res.json()) as unknown;
  } catch {
    return null;
  }
}
