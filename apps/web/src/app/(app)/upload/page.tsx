"use client";

import { useRouter } from "next/navigation";
import * as React from "react";

import { FormError } from "@/components/auth/form-error";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";

import {
  CreateMatchRequestSchema,
  MatchResponseSchema,
  ResumeResponseSchema,
} from "@matchlayer/shared-types";

/**
 * Upload_Page — `(app)/upload` (Requirement 12.1–12.7; design "Frontend
 * components" and the upload/match data-flow diagrams).
 *
 * The starting point of the Phase 1 flow: a logged-in user picks a PDF/DOCX
 * resume, pastes a job description, and gets routed to the Results_Page once
 * the match is scored. The page drives the two-step contract from the design's
 * sequence diagrams:
 *
 *   1. `POST /api/v1/resumes` as multipart `FormData` (field name `file`) →
 *      201 `ResumeResponse` carrying the new `id` (the resume_id).
 *   2. `POST /api/v1/matches` as JSON `{ resume_id, job_description }` →
 *      201 `MatchResponse` carrying the match `id`.
 *   3. Navigate to `/matches/{match_id}` (the Results_Page).
 *
 * Both calls go through `apiFetch`, which attaches the Bearer access token and
 * performs the silent refresh-and-retry; `FormData` and JSON bodies are both
 * re-usable across that retry path.
 *
 * This must be a Client Component: it owns interactive form state, reads the
 * selected `File` from the input, and navigates imperatively with
 * `useRouter().push`. The surrounding `(app)` Authenticated_Shell gates the
 * session server-side and exports `robots: { index: false, follow: false }`,
 * so this page inherits `noindex, nofollow` (Requirement 15.2) and adds no
 * discoverability metadata of its own.
 *
 * Contract typing (Requirement 12.7, `conventions.md`): request and response
 * shapes come exclusively from the generated `@matchlayer/shared-types` Zod
 * schemas — `CreateMatchRequestSchema` for form validation and
 * `ResumeResponseSchema` / `MatchResponseSchema` for parsing the two API
 * responses at the boundary. No request/response type is hand-written here.
 *
 * Privacy (`security.md`): the uploaded file bytes, the original filename, and
 * the job-description text are Restricted PII. Nothing in this module logs any
 * of them — there are no `console` calls — and every server-derived string is
 * rendered as a plain JSX text node (never `dangerouslySetInnerHTML`).
 */

/** File extensions the upload input accepts (Requirement 12.2, 12.4). */
const ACCEPTED_EXTENSIONS = [".pdf", ".docx"] as const;

/** True media types the client accepts. The backend re-validates by magic
 *  bytes (the authoritative check); some browsers leave `File.type` empty for
 *  DOCX, so an empty type is tolerated and the extension carries the guard. */
const ACCEPTED_MIME_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
] as const;

/**
 * Client-side resume size ceiling for pre-validation (Requirement 12.4),
 * mirroring the backend `MATCHLAYER_RESUME_MAX_BYTES`. Read from the public env
 * var when present, else the documented 5 MiB default. The backend stays the
 * authoritative limit (413 `payload_too_large`); this only blocks an obviously
 * oversized file before the request is issued.
 */
const RESUME_MAX_BYTES: number = (() => {
  const raw = process.env.NEXT_PUBLIC_RESUME_MAX_BYTES;
  const parsed = raw ? Number(raw) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 5_242_880;
})();

export default function UploadPage(): React.JSX.Element {
  const router = useRouter();

  const [file, setFile] = React.useState<File | null>(null);
  const [jobDescription, setJobDescription] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>): void {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
    // Give immediate feedback on an unacceptable file (Requirement 12.4); a
    // valid (or cleared) selection clears the announced error region.
    setError(selected ? validateFile(selected) : null);
  }

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    setError(null);

    // 1. Client pre-validation BEFORE any request (Requirement 12.4): a file
    //    must be present, of an accepted type, and within the size ceiling.
    if (!file) {
      setError("Choose a PDF or DOCX resume file to upload.");
      return;
    }
    const fileProblem = validateFile(file);
    if (fileProblem !== null) {
      setError(fileProblem);
      return;
    }

    // 2. Validate the job description via the generated Zod schema field
    //    (Requirement 12.3, 12.7). The backend enforces the 30..50000 trimmed
    //    window; a violation there surfaces as a 422 we render below.
    const jobDescriptionText = jobDescription.trim();
    const jdField =
      CreateMatchRequestSchema.shape.job_description.safeParse(
        jobDescriptionText,
      );
    if (!jdField.success) {
      setError("Paste the job description text to score against.");
      return;
    }

    setSubmitting(true);
    let navigated = false;
    try {
      // --- Step 1: upload the resume as multipart FormData -----------------
      const uploadForm = new FormData();
      uploadForm.append("file", file);

      const uploadRes = await apiFetch("/api/v1/resumes", {
        method: "POST",
        body: uploadForm,
      });

      if (!uploadRes.ok) {
        // RFC 7807 `detail` for 413/415/422/429 (Requirement 12.5).
        setError(
          await readProblemDetail(
            uploadRes,
            "We couldn't upload your resume. Please try again.",
          ),
        );
        return;
      }

      const uploadBody = await readJson(uploadRes);
      const resume = ResumeResponseSchema.safeParse(uploadBody);
      if (!resume.success) {
        setError("We couldn't read the upload response. Please try again.");
        return;
      }

      // --- Step 2: create the match (validate the full request shape) ------
      const matchRequest = CreateMatchRequestSchema.safeParse({
        resume_id: resume.data.id,
        job_description: jobDescriptionText,
      });
      if (!matchRequest.success) {
        setError("We couldn't start the match. Please try again.");
        return;
      }

      const matchRes = await apiFetch("/api/v1/matches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(matchRequest.data),
      });

      if (!matchRes.ok) {
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

      // --- Step 3: navigate to the Results_Page ----------------------------
      navigated = true;
      router.push(`/matches/${encodeURIComponent(match.data.id)}`);
    } catch {
      // Network error (server down, DNS, CORS) — friendly message, never a
      // thrown error object or stack trace.
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
          Score a resume
        </h1>
        <p className="text-text-muted">
          Upload your resume and paste a job description to see how well they
          match.
        </p>
      </header>

      <form onSubmit={handleSubmit} noValidate className="space-y-6">
        <div className="space-y-2">
          <label
            htmlFor="resume-file"
            className="block text-sm font-medium text-text"
          >
            Resume file
          </label>
          <input
            id="resume-file"
            name="file"
            type="file"
            accept=".pdf,.docx"
            onChange={handleFileChange}
            aria-describedby="resume-file-hint"
            className="block w-full cursor-pointer rounded-xl border border-border-strong bg-bg text-sm text-text file:mr-4 file:cursor-pointer file:border-0 file:bg-brand file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-brand/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          />
          <p id="resume-file-hint" className="text-xs text-text-subtle">
            PDF or DOCX, up to {formatMegabytes(RESUME_MAX_BYTES)}.
          </p>
          {file !== null && (
            <p className="text-sm text-text-muted">Selected: {file.name}</p>
          )}
        </div>

        <div className="space-y-2">
          <label
            htmlFor="job-description"
            className="block text-sm font-medium text-text"
          >
            Job description
          </label>
          <textarea
            id="job-description"
            name="job_description"
            rows={12}
            value={jobDescription}
            onChange={(event) => setJobDescription(event.target.value)}
            placeholder="Paste the full job description here…"
            className="block w-full rounded-xl border border-border-strong bg-bg px-3 py-2 text-sm text-text placeholder:text-text-subtle focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
          />
        </div>

        <FormError>{error}</FormError>

        <Button
          type="submit"
          disabled={submitting}
          className="w-full sm:w-auto"
        >
          {submitting ? "Scoring…" : "Score match"}
        </Button>
      </form>
    </div>
  );
}

/**
 * Pre-submission file check (Requirement 12.4). Returns a friendly message for
 * an unaccepted type or an oversized file, or `null` when the file may be
 * uploaded. The extension is the primary type guard because `File.type` is
 * unreliable for DOCX across browsers; the backend's magic-byte check remains
 * authoritative.
 */
function validateFile(file: File): string | null {
  const name = file.name.toLowerCase();
  const hasAcceptedExtension = ACCEPTED_EXTENSIONS.some((ext) =>
    name.endsWith(ext),
  );
  const hasAcceptedType =
    file.type === "" ||
    (ACCEPTED_MIME_TYPES as readonly string[]).includes(file.type);

  if (!hasAcceptedExtension || !hasAcceptedType) {
    return "That file type isn't supported. Upload a PDF or DOCX resume.";
  }
  if (file.size > RESUME_MAX_BYTES) {
    return `That file is too large. The maximum size is ${formatMegabytes(
      RESUME_MAX_BYTES,
    )}.`;
  }
  return null;
}

/**
 * Read an RFC 7807 envelope's `detail` as a display-safe string. Falls back to
 * a friendly message when the body is missing, unparseable, or carries no
 * string `detail` — so the UI never renders a raw error object or stack trace
 * (Requirement 12.5). `@matchlayer/shared-types` ships no generated problem
 * schema, so this is the minimal safe field read the design calls for.
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

/** Format a byte count as a whole-number MiB label (e.g. 5242880 → "5 MB"). */
function formatMegabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}
