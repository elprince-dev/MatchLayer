/**
 * Unit tests for `UploadWidget` (Task 6.5; Req 9.4, 9.6, 9.10; design Section
 * 7.2 "UploadWidget", Testing Strategy).
 *
 * Task 6.4 moved the resume upload out of the Upload page and into this widget,
 * so the validation / `extraction_status` / remove behaviors now live here (the
 * page-level test focuses on submit gating + the match POST + navigation).
 *
 * Covered:
 *   - **Client pre-validation (Req 9.4):** a non-PDF/DOCX file and a >5MB file
 *     each render the inline `ErrorState` naming the violated constraint, render
 *     **no** `FilePreviewCard`, and issue **no** `POST /api/v1/resumes` (invalid
 *     files never touch the network — the server check is only authoritative for
 *     files that pass the client guard).
 *   - **`extraction_status` reflection (Req 9.6):** a `succeeded` upload renders
 *     the ready affirmation + preview and fires `onResumeReady` exactly once with
 *     the parsed resume; a `pending` upload shows the processing indicator and
 *     never fires `onResumeReady`; a `failed` upload shows the extraction
 *     `ErrorState` and never fires `onResumeReady`.
 *   - **Remove (Req 9.10):** removing a ready resume clears the preview (returns
 *     to the idle drop zone) and fires `onResumeCleared` so the owning page can
 *     re-disable submit.
 *
 * The widget drives `POST /api/v1/resumes` through `apiFetch` from `@/lib/api`;
 * we mock that module directly (every test in this repo mocks `apiFetch` rather
 * than global `fetch`) so we can assert the call and craft the `Response`. The
 * `ResumeResponse` bodies reuse the Section 5 fixtures, which conform exactly to
 * the generated contract.
 *
 * Conventions mirror the co-located component tests (`error-state.test.tsx`,
 * `results/*.test.tsx`): `@testing-library/react` render/screen/waitFor/
 * fireEvent/cleanup, `vi.mock`, `afterEach(cleanup)`, `toBeInstanceOf`, and **no**
 * jest-dom matchers. Privacy (`security.md`): all fixtures are synthetic.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the API client before importing the widget. The widget imports only
// `apiFetch` from `@/lib/api`; replacing it lets us assert the upload call and
// return a crafted Response per test.
const apiFetchMock = vi.fn();

vi.mock("@/lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import {
  resumeFailed,
  resumePending,
  resumeSucceeded,
} from "@/components/results/__fixtures__/match-fixtures";
import { UploadWidget } from "@/components/upload/upload-widget";

import type { ResumeResponse } from "@matchlayer/shared-types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  apiFetchMock.mockReset();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** The client-side resume size ceiling mirrored by the widget (5 MiB). */
const RESUME_MAX_BYTES = 5_242_880;

/** Build a JSON `Response` (defaults to 201, the upload's success status). */
function jsonResponse(body: unknown, status = 201): Response {
  return new Response(body !== undefined ? JSON.stringify(body) : null, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A valid, small PDF `File` that passes client pre-validation. */
function validResumeFile(): File {
  return new File(["%PDF-1.7 synthetic"], "resume.pdf", {
    type: "application/pdf",
  });
}

/** Locate the widget's (sr-only) file input by its accessible label. */
function fileInput(): HTMLInputElement {
  return screen.getByLabelText(
    /upload a pdf or docx resume/i,
  ) as HTMLInputElement;
}

/** Select a file on the widget's file input via a change event. */
function selectFile(file: File): void {
  fireEvent.change(fileInput(), { target: { files: [file] } });
}

/** True when a `FilePreviewCard` is rendered anywhere in the tree. */
function hasPreviewCard(container: HTMLElement): boolean {
  return container.querySelector('[data-slot="file-preview-card"]') !== null;
}

// ---------------------------------------------------------------------------
// 9.4 — client pre-validation blocks bad files with NO preview and NO request
// ---------------------------------------------------------------------------

describe("UploadWidget — client pre-validation (Req 9.4)", () => {
  it("rejects a non-PDF/DOCX file with the unsupported-type ErrorState, no preview, and no upload request", async () => {
    const onResumeReady = vi.fn();
    const { container } = render(
      <UploadWidget onResumeReady={onResumeReady} />,
    );

    selectFile(new File(["plain text"], "resume.txt", { type: "text/plain" }));

    await waitFor(() => {
      expect(screen.getByText("Unsupported file type")).toBeInstanceOf(
        HTMLElement,
      );
    });
    // The constraint is named (PDF or DOCX) per Req 9.4.
    expect(screen.getByText("Use a PDF or DOCX file.")).toBeInstanceOf(
      HTMLElement,
    );
    // No preview card for an invalid file, and the network was never touched.
    expect(hasPreviewCard(container)).toBe(false);
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(onResumeReady).not.toHaveBeenCalled();
  });

  it("rejects a file over 5MB with the too-large ErrorState, no preview, and no upload request", async () => {
    const onResumeReady = vi.fn();
    const { container } = render(
      <UploadWidget onResumeReady={onResumeReady} />,
    );

    const file = validResumeFile();
    // Can't allocate megabytes in a test — stub the reported size past the cap.
    Object.defineProperty(file, "size", { value: RESUME_MAX_BYTES + 1 });

    selectFile(file);

    await waitFor(() => {
      expect(screen.getByText("File is too large")).toBeInstanceOf(HTMLElement);
    });
    // The 5MB constraint is named per Req 9.4.
    expect(screen.getByText("Keep your resume under 5MB.")).toBeInstanceOf(
      HTMLElement,
    );
    expect(hasPreviewCard(container)).toBe(false);
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(onResumeReady).not.toHaveBeenCalled();
  });

  it("accepts a file exactly at the 5MB cap (the boundary is inclusive)", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(resumeSucceeded));
    const onResumeReady = vi.fn();
    render(<UploadWidget onResumeReady={onResumeReady} />);

    const file = validResumeFile();
    Object.defineProperty(file, "size", { value: RESUME_MAX_BYTES });

    selectFile(file);

    // A file at exactly the cap is uploaded (the guard is `size > MAX`).
    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledTimes(1);
    });
    expect(apiFetchMock.mock.calls[0]?.[0]).toBe("/api/v1/resumes");
  });
});

// ---------------------------------------------------------------------------
// 9.6 — extraction_status: succeeded / pending / failed
// ---------------------------------------------------------------------------

describe("UploadWidget — extraction_status reflection (Req 9.6)", () => {
  it("treats a 'succeeded' resume as ready: shows the affirmation + preview and fires onResumeReady once", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(resumeSucceeded));
    const onResumeReady = vi.fn();
    const { container } = render(
      <UploadWidget onResumeReady={onResumeReady} />,
    );

    selectFile(validResumeFile());

    // The upload posts to the resumes endpoint with an Idempotency-Key.
    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledTimes(1);
    });
    expect(apiFetchMock.mock.calls[0]?.[0]).toBe("/api/v1/resumes");
    const init = apiFetchMock.mock.calls[0]?.[1] as
      | { method?: string; headers?: Record<string, string> }
      | undefined;
    expect(init?.method).toBe("POST");
    expect(init?.headers?.["Idempotency-Key"]).toBeTruthy();

    // The ready affirmation + the preview card render once succeeded.
    await waitFor(() => {
      expect(screen.getByText("Resume ready to analyze.")).toBeInstanceOf(
        HTMLElement,
      );
    });
    expect(hasPreviewCard(container)).toBe(true);

    // onResumeReady fired exactly once with the parsed, contract-shaped resume.
    await waitFor(() => {
      expect(onResumeReady).toHaveBeenCalledTimes(1);
    });
    const ready = onResumeReady.mock.calls[0]?.[0] as ResumeResponse;
    expect(ready.id).toBe(resumeSucceeded.id);
    expect(ready.extraction_status).toBe("succeeded");
  });

  it("shows the processing indicator for a 'pending' resume and never fires onResumeReady", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(resumePending));
    const onResumeReady = vi.fn();
    const { container } = render(
      <UploadWidget onResumeReady={onResumeReady} />,
    );

    selectFile(validResumeFile());

    // Pending shows a processing indicator + the preview, never the ready copy.
    await waitFor(() => {
      expect(screen.getByText("Processing your resume…")).toBeInstanceOf(
        HTMLElement,
      );
    });
    expect(hasPreviewCard(container)).toBe(true);
    expect(screen.queryByText("Resume ready to analyze.")).toBeNull();
    // A pending resume is not ready for analysis (Req 9.6).
    expect(onResumeReady).not.toHaveBeenCalled();
  });

  it("shows the extraction-failed ErrorState for a 'failed' resume and never fires onResumeReady", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(resumeFailed));
    const onResumeReady = vi.fn();
    render(<UploadWidget onResumeReady={onResumeReady} />);

    selectFile(validResumeFile());

    await waitFor(() => {
      expect(screen.getByText("We couldn't read that resume")).toBeInstanceOf(
        HTMLElement,
      );
    });
    // The copy prompts trying a different file (Req 9.6) and stays non-ready.
    expect(
      screen.getByRole("button", { name: /try a different file/i }),
    ).toBeInstanceOf(HTMLElement);
    expect(onResumeReady).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 9.10 — remove clears the preview and fires onResumeCleared
// ---------------------------------------------------------------------------

describe("UploadWidget — remove (Req 9.10)", () => {
  it("clears the preview (back to the drop zone) and fires onResumeCleared after a ready resume is removed", async () => {
    apiFetchMock.mockResolvedValue(jsonResponse(resumeSucceeded));
    const onResumeReady = vi.fn();
    const onResumeCleared = vi.fn();
    const { container } = render(
      <UploadWidget
        onResumeReady={onResumeReady}
        onResumeCleared={onResumeCleared}
      />,
    );

    selectFile(validResumeFile());

    // Wait for the ready state (preview + affirmation).
    await waitFor(() => {
      expect(onResumeReady).toHaveBeenCalledTimes(1);
    });
    expect(hasPreviewCard(container)).toBe(true);

    // Remove via the FilePreviewCard's remove button (aria-label "Remove <name>").
    const removeButton = screen.getByRole("button", {
      name: new RegExp(`remove ${resumeSucceeded.original_filename}`, "i"),
    });
    fireEvent.click(removeButton);

    // The preview is cleared and the idle drop zone is shown again.
    await waitFor(() => {
      expect(hasPreviewCard(container)).toBe(false);
    });
    expect(screen.getByText("Drag & drop your resume here")).toBeInstanceOf(
      HTMLElement,
    );

    // The owning page is told the resume is no longer ready (Req 9.10),
    // exactly once, and onResumeReady was not re-fired by the reset.
    expect(onResumeCleared).toHaveBeenCalledTimes(1);
    expect(onResumeReady).toHaveBeenCalledTimes(1);
  });
});
