/**
 * Component test for the Upload_Page — `(app)/upload` (Task 6.5; Req 9.9, 9.10,
 * 9.12; design Section 8.4, Testing Strategy).
 *
 * Task 6.4 rebuilt this page: the resume upload moved OUT of the page and INTO
 * `@/components/upload/upload-widget` (`UploadWidget`). The page no longer owns
 * a resume file input and no longer calls `POST /api/v1/resumes` itself — it
 * composes the widget (via `onResumeReady`/`onResumeCleared`), owns the
 * job-description `Textarea` + live count, gates the **"Analyze Match"** submit
 * button, and on submit posts **only** `POST /api/v1/matches` then navigates to
 * `/matches/{id}`. This file targets that page-level contract; the widget's own
 * validation / `extraction_status` / remove behavior is covered by
 * `src/components/upload/upload-widget.test.tsx`, and `formatBytes` edges by
 * `src/lib/utils.test.ts`.
 *
 * Covered here:
 *   - **Submit gating across resume status × JD bounds (Req 9.9):** "Analyze
 *     Match" is disabled until BOTH a `succeeded` resume is ready AND the
 *     trimmed JD is within 30..50,000 chars. Asserted disabled with no resume,
 *     disabled with a ready resume but a too-short JD, disabled with a valid JD
 *     but a non-ready (pending) resume, and enabled only once both hold.
 *   - **Remove re-disables submit (Req 9.10):** removing the ready resume (via
 *     the widget's `onResumeCleared`) disables the button again even though the
 *     JD is still valid.
 *   - **Success navigation (Req 9.12):** with a ready resume + valid JD,
 *     clicking "Analyze Match" posts `POST /api/v1/matches` and navigates to
 *     `/matches/{returned id}`.
 *
 * The page reaches a `succeeded` resume only through the real `UploadWidget`,
 * which posts `POST /api/v1/resumes` through `apiFetch`; we therefore mock
 * `apiFetch` and branch on the path so the resumes call returns a `succeeded`
 * `ResumeResponse` and the matches call returns a schema-valid `MatchResponse`.
 * `next/navigation` is mocked because the page navigates imperatively on
 * success. The match body is parsed with `MatchResponseSchema` to guard against
 * contract drift (mirroring `results-page.test.tsx`).
 *
 * Conventions mirror the rest of `apps/web/tests`: `@testing-library/react`
 * render/screen/waitFor/fireEvent/cleanup, `vi.mock`, `afterEach(cleanup)`,
 * `toBeInstanceOf`, and **no** jest-dom matchers. Privacy (`security.md`): all
 * fixtures are synthetic — generic filenames and a fabricated JD string.
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

import { MatchResponseSchema } from "@matchlayer/shared-types";

// Mock next/navigation before importing the page: the page reads
// `useRouter().push` to navigate to the Results_Page on success.
const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

// Mock the API client. Both the composed UploadWidget (`POST /api/v1/resumes`)
// and the page (`POST /api/v1/matches`) go through `apiFetch`; branching on the
// path lets us drive a ready resume and a created match independently.
const apiFetchMock = vi.fn();

vi.mock("@/lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import {
  matchStrong,
  resumePending,
  resumeSucceeded,
} from "@/components/results/__fixtures__/match-fixtures";

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

/**
 * A synthetic job description comfortably inside the trimmed 30..50,000 window
 * (70+ characters, no PII).
 */
const VALID_JD =
  "We are hiring a backend software engineer to build and maintain " +
  "scalable services and APIs for our platform team.";

/** A trimmed-length value below the 30-char floor (Req 9.8). */
const TOO_SHORT_JD = "too short";

/**
 * A schema-valid created `MatchResponse`. Parsing the fixture with the
 * generated schema guards against contract drift — if the contract changes,
 * this fails loudly here rather than producing a misleading navigation
 * assertion.
 */
const MATCH = MatchResponseSchema.parse(matchStrong);

/** Build a JSON `Response` with the given status and (optional) body. */
function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body !== undefined ? JSON.stringify(body) : null, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Branch `apiFetch` by path: the widget's `POST /api/v1/resumes` resolves to
 * `resume` (defaults to the `succeeded` fixture) and the page's
 * `POST /api/v1/matches` resolves to a created `MatchResponse`.
 */
function mockApi(options: { resume?: unknown; match?: unknown } = {}): void {
  const resumeBody = options.resume ?? resumeSucceeded;
  const matchBody = options.match ?? MATCH;
  apiFetchMock.mockImplementation(async (path: string): Promise<Response> => {
    if (path === "/api/v1/resumes") {
      return jsonResponse(201, resumeBody);
    }
    if (path === "/api/v1/matches") {
      return jsonResponse(201, matchBody);
    }
    throw new Error(`unexpected apiFetch path: ${path}`);
  });
}

/** A valid, small PDF `File` that passes the widget's client pre-validation. */
function validResumeFile(): File {
  return new File(["%PDF-1.7 synthetic"], "resume.pdf", {
    type: "application/pdf",
  });
}

/** Select a file on the widget's (sr-only) file input via a change event. */
function selectResumeFile(file: File = validResumeFile()): void {
  const input = screen.getByLabelText(
    /upload a pdf or docx resume/i,
  ) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

/** Type text into the job-description textarea. */
function fillJobDescription(text: string): void {
  const textarea = screen.getByLabelText(
    /job description/i,
  ) as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: text } });
}

/** The "Analyze Match" submit button (idle label). */
function submitButton(): HTMLButtonElement {
  return screen.getByRole("button", {
    name: /analyze match/i,
  }) as HTMLButtonElement;
}

/** Select a valid resume and wait until the widget reports it ready. */
async function makeResumeReady(): Promise<void> {
  selectResumeFile();
  await waitFor(() => {
    expect(screen.getByText("Resume ready to analyze.")).toBeInstanceOf(
      HTMLElement,
    );
  });
}

// ---------------------------------------------------------------------------
// 9.9 — submit gating across resume status × JD bounds
// ---------------------------------------------------------------------------

describe("Upload_Page submit gating (Req 9.9)", () => {
  it("disables 'Analyze Match' on first render (no resume, no job description)", () => {
    mockApi();
    render(<UploadPage />);

    expect(submitButton().disabled).toBe(true);
  });

  it("keeps submit disabled with a ready resume but a too-short job description", async () => {
    mockApi();
    render(<UploadPage />);

    await makeResumeReady();
    fillJobDescription(TOO_SHORT_JD);

    // Resume is ready, but the trimmed JD is below the 30-char floor.
    expect(submitButton().disabled).toBe(true);
  });

  it("keeps submit disabled with a valid job description but a non-ready (pending) resume", async () => {
    mockApi({ resume: resumePending });
    const { container } = render(<UploadPage />);

    selectResumeFile();
    // The widget reflects "pending" (processing) and never reports the resume
    // ready, so the page never tracks a resume to gate on.
    await waitFor(() => {
      expect(screen.getByText("Processing your resume…")).toBeInstanceOf(
        HTMLElement,
      );
    });
    fillJobDescription(VALID_JD);

    expect(screen.queryByText("Resume ready to analyze.")).toBeNull();
    expect(submitButton().disabled).toBe(true);
    // Sanity: a pending resume still shows its preview but no ready affirmation.
    expect(
      container.querySelector('[data-slot="file-preview-card"]'),
    ).toBeInstanceOf(HTMLElement);
  });

  it("enables submit only once BOTH a ready resume and a valid job description are present", async () => {
    mockApi();
    render(<UploadPage />);

    // JD alone is not enough.
    fillJobDescription(VALID_JD);
    expect(submitButton().disabled).toBe(true);

    // With the resume also ready, the button enables.
    await makeResumeReady();
    await waitFor(() => {
      expect(submitButton().disabled).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// 9.10 — removing the resume re-disables submit
// ---------------------------------------------------------------------------

describe("Upload_Page remove re-disables submit (Req 9.10)", () => {
  it("disables 'Analyze Match' again after the ready resume is removed, even with a valid JD", async () => {
    mockApi();
    render(<UploadPage />);

    fillJobDescription(VALID_JD);
    await makeResumeReady();
    await waitFor(() => {
      expect(submitButton().disabled).toBe(false);
    });

    // Remove the resume via the FilePreviewCard's remove button.
    fireEvent.click(
      screen.getByRole("button", {
        name: new RegExp(`remove ${resumeSucceeded.original_filename}`, "i"),
      }),
    );

    // The JD is still valid, but with no ready resume the button re-disables.
    await waitFor(() => {
      expect(submitButton().disabled).toBe(true);
    });
    expect(
      (screen.getByLabelText(/job description/i) as HTMLTextAreaElement).value,
    ).toBe(VALID_JD);
  });
});

// ---------------------------------------------------------------------------
// 9.12 — success posts POST /api/v1/matches and navigates to /matches/{id}
// ---------------------------------------------------------------------------

describe("Upload_Page success navigation (Req 9.12)", () => {
  it("posts the match request and navigates to /matches/{returned id}", async () => {
    mockApi();
    render(<UploadPage />);

    fillJobDescription(VALID_JD);
    await makeResumeReady();
    await waitFor(() => {
      expect(submitButton().disabled).toBe(false);
    });

    fireEvent.click(submitButton());

    // Navigation happens with the created match's id (Req 9.12).
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith(`/matches/${MATCH.id}`);
    });

    // The match POST carried the ready resume id + the trimmed job description.
    const matchCall = apiFetchMock.mock.calls.find(
      (call) => call[0] === "/api/v1/matches",
    );
    expect(matchCall).toBeDefined();
    const init = matchCall?.[1] as { method?: string; body?: string };
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body ?? "{}") as {
      resume_id: string;
      job_description: string;
    };
    expect(sent.resume_id).toBe(resumeSucceeded.id);
    expect(sent.job_description).toBe(VALID_JD);

    // While the successful navigation settles, the button shows the loading
    // copy and stays disabled (Req 9.11).
    expect(screen.getByText("Analyzing your resume…")).toBeInstanceOf(
      HTMLElement,
    );
  });

  it("does not navigate and surfaces the RFC 7807 detail when the match POST fails", async () => {
    const detail = "The job description is too short to score.";
    apiFetchMock.mockImplementation(async (path: string): Promise<Response> => {
      if (path === "/api/v1/resumes") {
        return jsonResponse(201, resumeSucceeded);
      }
      if (path === "/api/v1/matches") {
        return jsonResponse(422, {
          type: "validation_error",
          title: "Unprocessable",
          detail,
          status: 422,
        });
      }
      throw new Error(`unexpected apiFetch path: ${path}`);
    });

    const { container } = render(<UploadPage />);

    fillJobDescription(VALID_JD);
    await makeResumeReady();
    await waitFor(() => {
      expect(submitButton().disabled).toBe(false);
    });

    fireEvent.click(submitButton());

    // The server-supplied detail is announced via the page's aria-live region.
    await waitFor(() => {
      expect(screen.getByText(detail)).toBeInstanceOf(HTMLElement);
    });
    const liveRegion = container.querySelector('[aria-live="polite"]');
    expect(liveRegion).not.toBeNull();
    expect(liveRegion?.textContent).toContain(detail);

    // No navigation on failure.
    expect(pushMock).not.toHaveBeenCalled();
  });
});
