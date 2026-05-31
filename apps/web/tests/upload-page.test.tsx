/**
 * Component test for the Upload_Page — `(app)/upload` (Task 14.2).
 *
 * Validates the client-facing slice of Requirement 12 (Upload_Page) the design
 * pins to this surface:
 *
 *   - 12.2 — the file input advertises `accept=".pdf,.docx"` and both controls
 *     (resume file, job description) are reachable by their labels (label
 *     `htmlFor`/`id` association works for screen readers and `getByLabelText`).
 *   - 12.4 — client pre-validation blocks an oversized file and a wrong-type
 *     file *before* any network request is issued (no `apiFetch` call).
 *   - 12.3 / 12.5 — when the two-step upload→match contract returns an
 *     RFC 7807 problem envelope (413 / 415 / 422 / 429), the page renders the
 *     server-supplied `detail` string inside the `aria-live="polite"` region
 *     (the shared `FormError` live region), so assistive tech announces it.
 *
 * The page drives a two-step flow — `POST /api/v1/resumes` (multipart) then
 * `POST /api/v1/matches` (JSON) then `router.push('/matches/{id}')` — both
 * calls going through `apiFetch` from `@/lib/api`. We mock `apiFetch`
 * directly (rather than stubbing global `fetch`) so we can assert the call
 * sequence and craft a per-call `Response`. `next/navigation`'s `useRouter`
 * is mocked because the page navigates imperatively on success.
 *
 * Privacy (`security.md`): all fixtures here are synthetic — generic filenames
 * and a fabricated job-description string. Nothing logs PII.
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
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock next/navigation before importing the page: the page reads
// `useRouter().push` to navigate to the Results_Page on success.
const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

// Mock the API client. The page imports only `apiFetch` from `@/lib/api`;
// replacing it lets us assert the two-step call sequence and return crafted
// Response objects per call without touching the real network stack.
const apiFetchMock = vi.fn();

vi.mock("@/lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import UploadPage from "@/app/(app)/upload/page";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  pushMock.mockClear();
  apiFetchMock.mockReset();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** The client-side resume size ceiling mirrored by the page (5 MiB). */
const RESUME_MAX_BYTES = 5_242_880;

/**
 * A synthetic job description comfortably above the schema's trimmed floor
 * (the generated `CreateMatchRequestSchema.shape.job_description` field only
 * requires a non-empty string, but the design's window is 30..50000 chars).
 * 70+ characters, no PII.
 */
const VALID_JD =
  "We are hiring a backend software engineer to build and maintain " +
  "scalable services and APIs for our platform team.";

/** Build a JSON `Response` with an RFC-7807-friendly content type. */
function jsonResponse(status: number, body: unknown): Response {
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

/** Select a file on the resume input via a change event. */
function selectFile(file: File): void {
  const input = screen.getByLabelText(/resume file/i) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

/** Type text into the job-description textarea. */
function fillJobDescription(text: string): void {
  const textarea = screen.getByLabelText(
    /job description/i,
  ) as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: text } });
}

/** Submit the form by clicking the submit button. */
function submitForm(): void {
  fireEvent.click(screen.getByRole("button", { name: /score match/i }));
}

// ---------------------------------------------------------------------------
// 12.2 — accept attribute + label association
// ---------------------------------------------------------------------------

describe("Upload_Page accessibility wiring (Requirement 12.2)", () => {
  it("advertises accept='.pdf,.docx' on the resume file input", () => {
    render(<UploadPage />);

    const input = screen.getByLabelText(/resume file/i);
    expect(input).toBeInstanceOf(HTMLInputElement);
    expect(input.getAttribute("type")).toBe("file");
    expect(input.getAttribute("accept")).toBe(".pdf,.docx");
  });

  it("associates labels with both the file input and the job-description textarea", () => {
    render(<UploadPage />);

    // getByLabelText only resolves when the htmlFor/id association is correct.
    const fileInput = screen.getByLabelText(/resume file/i);
    const jdTextarea = screen.getByLabelText(/job description/i);

    expect(fileInput.tagName).toBe("INPUT");
    expect(jdTextarea.tagName).toBe("TEXTAREA");
  });
});

// ---------------------------------------------------------------------------
// 12.4 — client pre-validation blocks bad files before any request
// ---------------------------------------------------------------------------

describe("Upload_Page client pre-validation (Requirement 12.4)", () => {
  it("blocks an oversized file with a friendly error and issues no upload request", async () => {
    render(<UploadPage />);

    const file = validResumeFile();
    // Can't allocate megabytes in a test — stub the reported size past the cap.
    Object.defineProperty(file, "size", { value: RESUME_MAX_BYTES + 1 });

    selectFile(file);
    fillJobDescription(VALID_JD);
    submitForm();

    await waitFor(() => {
      expect(screen.getByText(/too large/i)).toBeInstanceOf(HTMLElement);
    });
    // The oversize guard must short-circuit before the two-step API flow.
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("blocks a wrong-type (.txt) file with an unsupported-type error and issues no upload request", async () => {
    render(<UploadPage />);

    const file = new File(["plain text"], "resume.txt", {
      type: "text/plain",
    });

    selectFile(file);
    fillJobDescription(VALID_JD);
    submitForm();

    await waitFor(() => {
      expect(screen.getByText(/isn't supported/i)).toBeInstanceOf(HTMLElement);
    });
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 12.3 / 12.5 — RFC 7807 `detail` rendered in the aria-live region
// ---------------------------------------------------------------------------

describe("Upload_Page error rendering (Requirements 12.3, 12.5)", () => {
  // Synthetic, display-safe `detail` strings — one per status the design
  // enumerates for this surface. No PII.
  const cases: ReadonlyArray<{ status: number; detail: string }> = [
    { status: 413, detail: "That resume is larger than the 5 MB limit." },
    { status: 415, detail: "Only PDF and DOCX resumes are supported." },
    { status: 422, detail: "The job description is too short to score." },
    { status: 429, detail: "Too many uploads. Please try again shortly." },
  ];

  it.each(cases)(
    "renders the RFC 7807 detail inside the aria-live region on a $status response",
    async ({ status, detail }) => {
      // First (and only) call — the resume upload — returns the problem
      // envelope, so the page short-circuits and renders `detail`.
      apiFetchMock.mockResolvedValueOnce(
        jsonResponse(status, {
          type: "validation_error",
          title: "Upload rejected",
          detail,
          status,
        }),
      );

      const { container } = render(<UploadPage />);

      selectFile(validResumeFile());
      fillJobDescription(VALID_JD);
      submitForm();

      // The detail text becomes visible...
      await waitFor(() => {
        expect(screen.getByText(detail)).toBeInstanceOf(HTMLElement);
      });

      // ...and it lives inside the polite live region (the FormError node),
      // so assistive tech announces it (Requirement 12.3).
      const liveRegion = container.querySelector('[aria-live="polite"]');
      expect(liveRegion).not.toBeNull();
      expect(
        within(liveRegion as HTMLElement).getByText(detail),
      ).toBeInstanceOf(HTMLElement);

      // The upload was attempted exactly once; the match step never ran.
      expect(apiFetchMock).toHaveBeenCalledTimes(1);
      expect(apiFetchMock.mock.calls[0]?.[0]).toBe("/api/v1/resumes");
      expect(pushMock).not.toHaveBeenCalled();
    },
  );
});
