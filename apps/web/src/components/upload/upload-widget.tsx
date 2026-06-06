"use client";

import { CircleCheck, UploadCloud } from "lucide-react";
import * as React from "react";

import type { ResumeResponse } from "@matchlayer/shared-types";
import { ResumeResponseSchema } from "@matchlayer/shared-types";

import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

import { FilePreviewCard } from "./file-preview-card";
import { ProgressBar } from "./progress-bar";

/**
 * UploadWidget — the Upload_Page's resume drop zone (design Section 7.2
 * "UploadWidget"; Req 9.1–9.7, 9.10, 16.3).
 *
 * Owns the full client lifecycle of getting **one** PDF/DOCX resume to the API
 * and reflecting the result. It is the data island for the upload half of the
 * authenticated flow; the surrounding page (task 6.4) supplies `onResumeReady`
 * and pairs the widget with the job-description field + submit gating.
 *
 * ## State machine (the eight design states)
 *   - **idle** — the drop zone (drag-drop + click-to-browse).
 *   - **drag-over** — `isDragOver` overlays the idle/invalid zone with a brand
 *     border + bg tint + updated instructional text (Req 9.2).
 *   - **invalid file** — client type/size pre-validation failed: an inline
 *     `ErrorState` naming the violated constraint (PDF/DOCX or ≤5MB), and
 *     **no** preview card (Req 9.4).
 *   - **uploading** — the multipart `POST /api/v1/resumes` is in flight; the
 *     selected file's preview + a `ProgressBar` are shown (Req 9.5).
 *   - **pending** — the upload returned `extraction_status: "pending"`; the UI
 *     indicates processing (Req 9.6).
 *   - **failed** — `extraction_status: "failed"`; an inline `ErrorState`
 *     explains the text couldn't be read and prompts a different file (Req 9.6).
 *   - **succeeded** — `extraction_status: "succeeded"`; the preview is shown,
 *     a ready affirmation rendered, and `onResumeReady(resume)` fired — the
 *     **only** path that calls the callback (Req 9.6).
 *   - **transmission error** — a network error / non-OK response / contract
 *     drift; an inline `ErrorState` offering retry + remove (Req 9.7).
 *
 * ## Why client validation is UX-only (security.md "File uploads")
 * Type and size are checked here purely so the user gets instant feedback and
 * we don't ship an obviously-bad file over the wire. The server re-validates by
 * **magic bytes** and enforces the hard size ceiling — it is authoritative.
 * `File.type` is unreliable for DOCX across browsers (often empty), so the
 * extension is the primary client guard and an empty MIME is tolerated; the
 * server still rejects a spoofed/renamed file. The original filename is shown
 * for **display only** and is never used to build a path.
 *
 * ## Upload progress with `apiFetch` (the documented tradeoff)
 * The transmission goes through the shared {@link apiFetch} wrapper, which
 * attaches the Bearer access token and performs the single-flight
 * 401→refresh→retry. The WHATWG `fetch` API that `apiFetch` builds on **cannot
 * report upload-progress events** — only `XMLHttpRequest` exposes
 * `upload.onprogress`. Switching to raw `XHR` to get byte-accurate progress
 * would forfeit `apiFetch`'s Bearer attachment and the silent token refresh,
 * re-implementing auth by hand for one screen — a security/consistency
 * regression we deliberately avoid. So, per the task's sanctioned alternative,
 * the `ProgressBar` is driven as an **honest activity indicator**: a bounded
 * animation that eases toward (but never reaches) completion while the request
 * is genuinely in flight, and is replaced the instant the response resolves.
 * It conveys "working", not measured bytes — it never shows 100%/"done" before
 * the server has actually responded.
 *
 * ## Idempotency (security.md, Req 16.3 idempotent uploads)
 * Each selected file gets one `Idempotency-Key` (a UUID) generated at selection
 * time and **reused across retries** of that same file, so a retried
 * transmission replays idempotently rather than creating a duplicate resume.
 * Selecting a new file mints a fresh key.
 *
 * ## Privacy (security.md)
 * No file contents are ever logged — there are no `console` calls. Every
 * server-derived string is rendered as a plain JSX text node.
 */

/** PDF media type accepted by the upload flow (Req 9.1). */
const PDF_CONTENT_TYPE = "application/pdf";
/** DOCX media type accepted by the upload flow (Req 9.1). */
const DOCX_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * Client-side size ceiling for pre-validation (Req 9.1, 9.4), mirroring the
 * backend `MATCHLAYER_RESUME_MAX_BYTES`. Read from the public env var when
 * present, else the documented 5 MiB default. The backend stays authoritative
 * (413 `payload_too_large`); this only blocks an obviously oversized file
 * before the request is issued.
 */
const RESUME_MAX_BYTES: number = (() => {
  const raw = process.env.NEXT_PUBLIC_RESUME_MAX_BYTES;
  const parsed = raw ? Number(raw) : Number.NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 5_242_880;
})();

/**
 * Mapped, display-safe error copy (Req 17.3–17.5): titles ≤60 chars,
 * explanations ≤200 chars, plain language, each constraint named. The
 * `ErrorState` indicator carries the only `danger` color; the copy here never
 * leaks codes, stack traces, or RFC 7807 envelope fields.
 */
const TYPE_ERROR = {
  title: "Unsupported file type",
  message: "Use a PDF or DOCX file.",
} as const;
const SIZE_ERROR = {
  title: "File is too large",
  message: "Keep your resume under 5MB.",
} as const;
const TRANSMISSION_ERROR = {
  title: "Upload failed",
  message:
    "We couldn't upload your resume. Check your connection and try again.",
} as const;
const EXTRACTION_FAILED = {
  title: "We couldn't read that resume",
  message:
    "We couldn't pull any text from this file. Try uploading a different PDF or DOCX.",
} as const;

/**
 * The widget's internal state, one variant per design state. `drag-over` is not
 * a variant here — it is a transient `isDragOver` overlay on the idle/invalid
 * drop zone (it never changes the underlying selection).
 */
type Phase =
  | { kind: "idle" }
  | { kind: "invalid"; title: string; message: string }
  | { kind: "uploading"; file: File }
  | { kind: "pending"; resume: ResumeResponse }
  | { kind: "failed"; resume: ResumeResponse }
  | { kind: "succeeded"; resume: ResumeResponse }
  | { kind: "error"; file: File };

/** Props for {@link UploadWidget} (design Section 7.2). */
export interface UploadWidgetProps {
  /**
   * Fired **only** when a resume's `extraction_status` is `"succeeded"`
   * (Req 9.6) — i.e. when the resume is genuinely ready to analyze. Receives
   * the parsed, contract-validated `ResumeResponse`.
   */
  onResumeReady: (resume: ResumeResponse) => void;
  /**
   * Optional complement to {@link onResumeReady}: fired when a
   * previously-ready resume stops being ready — the user removed it, selected
   * a different file, or a re-upload moved the status back to
   * pending/failed/error. The widget itself owns no submit button, so this lets
   * the owning Upload page (task 6.4) re-disable "Analyze Match" the instant the
   * resume is no longer `succeeded` (Req 9.9 "disabled … on remove", Req 9.10).
   *
   * Backward compatible: omitting it preserves the original single-callback
   * contract. Never fires before the first `onResumeReady`, and fires at most
   * once per readiness loss.
   */
  onResumeCleared?: () => void;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/**
 * Generate a per-upload `Idempotency-Key`. Prefers the Web Crypto
 * `randomUUID()`; falls back to a timestamp+random string in environments that
 * lack it (older browsers / non-secure contexts) so an upload is never blocked.
 */
function generateIdempotencyKey(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Client pre-validation (Req 9.4). Returns mapped `{ title, message }` copy
 * naming the violated constraint, or `null` when the file may be uploaded.
 * Extension is the primary type guard (DOCX `File.type` is often empty); an
 * empty MIME is tolerated and the server's magic-byte check remains
 * authoritative.
 */
function validateFile(file: File): { title: string; message: string } | null {
  const name = file.name.toLowerCase();
  const hasAcceptedExtension = name.endsWith(".pdf") || name.endsWith(".docx");
  const hasAcceptedType =
    file.type === "" ||
    file.type === PDF_CONTENT_TYPE ||
    file.type === DOCX_CONTENT_TYPE;

  if (!hasAcceptedExtension || !hasAcceptedType) {
    return TYPE_ERROR;
  }
  if (file.size > RESUME_MAX_BYTES) {
    return SIZE_ERROR;
  }
  return null;
}

/**
 * Best-effort media type for the pre-upload preview card. Uses the browser's
 * `File.type` when present, else derives one from the extension so the
 * `FilePreviewCard` shows the right icon while the bytes are still in flight.
 */
function localContentType(file: File): string {
  if (file.type !== "") {
    return file.type;
  }
  const name = file.name.toLowerCase();
  if (name.endsWith(".pdf")) {
    return PDF_CONTENT_TYPE;
  }
  if (name.endsWith(".docx")) {
    return DOCX_CONTENT_TYPE;
  }
  return "";
}

/**
 * Parse a response body against the generated `ResumeResponseSchema` so the
 * contract is validated at the boundary (Req 16.3, 20.7) — drift surfaces as a
 * friendly transmission error rather than a render crash. Returns `null` on any
 * parse failure.
 */
function parseResume(body: unknown): ResumeResponse | null {
  const parsed = ResumeResponseSchema.safeParse(body);
  return parsed.success ? parsed.data : null;
}

/**
 * Drive the `ProgressBar` as an honest activity indicator while a request is in
 * flight (see the component docstring's "Upload progress" note). The value eases
 * asymptotically toward — but never reaches — a cap below 100% while `active`,
 * and resets to 0 when inactive. It represents elapsed activity, not measured
 * bytes, and is a loading indicator, so it is exempt from `prefers-reduced-motion`
 * suppression (Req 15.4).
 */
function useActiveProgress(active: boolean): number {
  const [value, setValue] = React.useState(0);

  React.useEffect(() => {
    // All state updates run inside async timer callbacks (never synchronously
    // in the effect body) so a new activation starts from a clean 0 without
    // triggering cascading renders.
    if (!active) {
      const resetId = setTimeout(() => setValue(0), 0);
      return () => {
        clearTimeout(resetId);
      };
    }
    // Show immediate activity, then ease asymptotically toward (never reaching)
    // a 90% cap while the request is genuinely in flight.
    const startId = setTimeout(() => setValue(8), 0);
    const id = setInterval(() => {
      setValue((current) =>
        current >= 90
          ? current
          : current + Math.max(1, Math.round((90 - current) * 0.12)),
      );
    }, 200);
    return () => {
      clearTimeout(startId);
      clearInterval(id);
    };
  }, [active]);

  return value;
}

export function UploadWidget({
  onResumeReady,
  onResumeCleared,
  className,
}: UploadWidgetProps): React.JSX.Element {
  const [phase, setPhase] = React.useState<Phase>({ kind: "idle" });
  const [isDragOver, setIsDragOver] = React.useState(false);

  const inputRef = React.useRef<HTMLInputElement>(null);
  // Monotonic token used to ignore the outcome of a superseded/aborted upload
  // (e.g. the user removed the file or selected a new one mid-request).
  const requestSeqRef = React.useRef(0);
  // The current file + its reusable idempotency key, so a retry replays
  // idempotently rather than creating a duplicate resume.
  const pendingUploadRef = React.useRef<{ file: File; key: string } | null>(
    null,
  );
  // Depth counter so nested dragenter/dragleave events don't flicker the
  // highlight as the cursor moves over child nodes.
  const dragDepthRef = React.useRef(0);
  // Ensures `onResumeReady` fires once per succeeded resume id.
  const readyIdRef = React.useRef<string | null>(null);
  // Tracks whether the parent has been told a resume is ready, so the
  // complementary `onResumeCleared` fires exactly once on the ready→not-ready
  // edge. Kept separate from `readyIdRef` (which `reset()`/new-selection clear
  // synchronously to allow a same-id re-upload to re-fire ready) so the edge is
  // detected even when the selection ref has already been wiped.
  const wasReadyRef = React.useRef(false);

  // Fire the ready callback exactly once when a resume succeeds (never during
  // render). Reset on `reset()` so a re-upload of the same id can re-fire. When
  // the phase leaves "succeeded" after having been ready (remove, new file, or
  // a re-upload that lands pending/failed/error), fire `onResumeCleared` once so
  // the owning page can re-disable its submit button (Req 9.9, 9.10).
  React.useEffect(() => {
    if (phase.kind === "succeeded") {
      if (readyIdRef.current !== phase.resume.id) {
        readyIdRef.current = phase.resume.id;
        wasReadyRef.current = true;
        onResumeReady(phase.resume);
      }
      return;
    }
    if (wasReadyRef.current) {
      wasReadyRef.current = false;
      onResumeCleared?.();
    }
  }, [phase, onResumeReady, onResumeCleared]);

  const openFileDialog = React.useCallback((): void => {
    inputRef.current?.click();
  }, []);

  const reset = React.useCallback((): void => {
    // Invalidate any in-flight upload, clear selection + key, and return to idle.
    requestSeqRef.current += 1;
    pendingUploadRef.current = null;
    readyIdRef.current = null;
    if (inputRef.current !== null) {
      inputRef.current.value = "";
    }
    setIsDragOver(false);
    dragDepthRef.current = 0;
    setPhase({ kind: "idle" });
  }, []);

  const startUpload = React.useCallback(
    async (file: File, key: string): Promise<void> => {
      const seq = (requestSeqRef.current += 1);
      setPhase({ kind: "uploading", file });

      let res: Response;
      try {
        const form = new FormData();
        form.append("file", file);
        res = await apiFetch("/api/v1/resumes", {
          method: "POST",
          body: form,
          headers: { "Idempotency-Key": key },
        });
      } catch {
        // Network error (server down, DNS, CORS). Never surface the raw error.
        if (requestSeqRef.current === seq) {
          setPhase({ kind: "error", file });
        }
        return;
      }

      if (requestSeqRef.current !== seq) {
        return; // Superseded by a remove / new selection.
      }

      if (!res.ok) {
        setPhase({ kind: "error", file });
        return;
      }

      let body: unknown;
      try {
        body = await res.json();
      } catch {
        body = null;
      }

      if (requestSeqRef.current !== seq) {
        return;
      }

      const resume = parseResume(body);
      if (resume === null) {
        setPhase({ kind: "error", file });
        return;
      }

      switch (resume.extraction_status) {
        case "succeeded":
          setPhase({ kind: "succeeded", resume });
          break;
        case "pending":
          setPhase({ kind: "pending", resume });
          break;
        case "failed":
          setPhase({ kind: "failed", resume });
          break;
      }
    },
    [],
  );

  const handleFiles = React.useCallback(
    (files: FileList | null): void => {
      const file = files?.[0] ?? null;
      if (file === null) {
        return;
      }
      // A new selection invalidates any in-flight upload.
      requestSeqRef.current += 1;
      readyIdRef.current = null;

      const problem = validateFile(file);
      if (problem !== null) {
        pendingUploadRef.current = null;
        setPhase({ kind: "invalid", ...problem });
        return;
      }

      const key = generateIdempotencyKey();
      pendingUploadRef.current = { file, key };
      void startUpload(file, key);
    },
    [startUpload],
  );

  const retry = React.useCallback((): void => {
    const pending = pendingUploadRef.current;
    if (pending === null) {
      reset();
      return;
    }
    // Reuse the same idempotency key so the retry replays idempotently.
    void startUpload(pending.file, pending.key);
  }, [reset, startUpload]);

  // --- Drag-and-drop handlers (Req 9.2) ------------------------------------
  const onDragEnter = React.useCallback((event: React.DragEvent): void => {
    event.preventDefault();
    dragDepthRef.current += 1;
    setIsDragOver(true);
  }, []);

  const onDragOver = React.useCallback((event: React.DragEvent): void => {
    // Required so the element is a valid drop target.
    event.preventDefault();
  }, []);

  const onDragLeave = React.useCallback((event: React.DragEvent): void => {
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setIsDragOver(false);
    }
  }, []);

  const onDrop = React.useCallback(
    (event: React.DragEvent): void => {
      event.preventDefault();
      dragDepthRef.current = 0;
      setIsDragOver(false);
      handleFiles(event.dataTransfer.files);
    },
    [handleFiles],
  );

  const onInputChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>): void => {
      handleFiles(event.target.files);
    },
    [handleFiles],
  );

  const progressActive = phase.kind === "uploading" || phase.kind === "pending";
  const progress = useActiveProgress(progressActive);

  // The drag-drop zone, shown for the idle + invalid states (where re-selection
  // is the next action). Drag-over highlight is applied only here (Req 9.2).
  const dropZone = (
    <div
      role="group"
      aria-label="Resume upload drop zone"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-hero border-2 border-dashed p-8 text-center transition-colors",
        isDragOver
          ? "border-brand bg-brand/5"
          : "border-border-strong bg-bg-elevated",
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-12 items-center justify-center rounded-full border border-border bg-bg text-text-muted"
      >
        <UploadCloud className="size-6" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-medium text-text">
          {isDragOver
            ? "Drop your file to upload"
            : "Drag & drop your resume here"}
        </p>
        <p className="text-xs text-text-subtle">PDF or DOCX · up to 5MB</p>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={openFileDialog}
      >
        Browse files
      </Button>
    </div>
  );

  return (
    <div className={cn("w-full space-y-4", className)}>
      {/* Hidden but accessible file input — triggered by the branded "Browse
          files" button (and the recovery actions), so the upload control is
          keyboard-reachable (Req 19.3). */}
      <input
        ref={inputRef}
        id="upload-widget-file-input"
        type="file"
        accept=".pdf,.docx"
        aria-label="Upload a PDF or DOCX resume"
        className="sr-only"
        onChange={onInputChange}
      />

      {phase.kind === "idle" && dropZone}

      {phase.kind === "invalid" && (
        <div className="space-y-4">
          <ErrorState
            title={phase.title}
            message={phase.message}
            action={{
              label: "Choose a different file",
              onClick: openFileDialog,
            }}
          />
          {dropZone}
        </div>
      )}

      {phase.kind === "uploading" && (
        <div className="space-y-4">
          <FilePreviewCard
            resume={{
              filename: phase.file.name,
              byteSize: phase.file.size,
              contentType: localContentType(phase.file),
            }}
            onRemove={reset}
          />
          <ProgressBar value={progress} label="Uploading your resume…" />
        </div>
      )}

      {phase.kind === "pending" && (
        <div className="space-y-4">
          <FilePreviewCard resume={phase.resume} onRemove={reset} />
          <ProgressBar value={progress} label="Processing your resume…" />
        </div>
      )}

      {phase.kind === "succeeded" && (
        <div className="space-y-3">
          <FilePreviewCard resume={phase.resume} onRemove={reset} />
          <p className="flex items-center gap-2 text-sm text-text-muted">
            <CircleCheck aria-hidden="true" className="size-4 text-success" />
            Resume ready to analyze.
          </p>
        </div>
      )}

      {phase.kind === "failed" && (
        <ErrorState
          title={EXTRACTION_FAILED.title}
          message={EXTRACTION_FAILED.message}
          action={{ label: "Try a different file", onClick: openFileDialog }}
        />
      )}

      {phase.kind === "error" && (
        <div className="space-y-4">
          <ErrorState
            title={TRANSMISSION_ERROR.title}
            message={TRANSMISSION_ERROR.message}
            action={{ label: "Retry", onClick: retry }}
          />
          <div className="flex justify-center">
            <Button type="button" variant="ghost" onClick={reset}>
              Remove
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
