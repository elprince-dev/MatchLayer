/**
 * Component test for the register page form — `(auth)/register` (frontend-
 * redesign Task 7.4; Req 8.4, 8.11, 8.12; design §8.3, Testing Strategy).
 *
 * Sibling of `login-form.test.tsx`, mirroring its conventions. The register
 * page is a client component that:
 *   - Validates client-side via React Hook Form + Zod: email format,
 *     password minimum length of 12, and confirm-password match.
 *   - Calls `POST /api/v1/auth/register` directly via global `fetch` (NOT
 *     `apiFetch`), so the global is stubbed via `vi.stubGlobal`.
 *   - Maps the response to UI:
 *       - 2xx WITH an `access_token` → setAccessToken + router.push("/upload")
 *         (a genuine new-account success).
 *       - 2xx WITHOUT a token → router.push("/upload") with NO setAccessToken
 *         (the enumeration-defense "email already exists" path, designed to be
 *         indistinguishable from real success — Req 8.11).
 *       - any non-OK status (incl. 5xx) → a single generic, non-enumerable
 *         banner "Something went wrong. Please try again." with the entered
 *         email preserved (Req 8.11).
 *       - 429 → <RetryAfterMessage> with the Retry-After seconds.
 *
 * Coverage in this file:
 *   - Req 8.4: invalid email, a <12-char password, and a confirm-password
 *     MISMATCH each surface an inline `FieldError` (aria-live="polite") AND
 *     block submit (no `fetch` issued).
 *   - Req 8.11: the non-enumerable failure wording is IDENTICAL across
 *     failure modes (a 400 "email exists"-shaped body and a 500 both render
 *     the same generic string), and the entered email is preserved.
 *   - Req 8.11/8.12: the enumeration-defense path (2xx without a token)
 *     navigates to `/upload` indistinguishably from a real token success.
 *   - Req 8.12: a genuine success (2xx with token) sets the token and
 *     navigates to `/upload`.
 *
 * The register form has THREE fields — "Email", "Password", and "Confirm
 * password". Because `getByLabelText(/password/i)` would match both password
 * fields, this file queries by EXACT label text ("Password" vs "Confirm
 * password") to disambiguate.
 *
 * Conventions mirror the rest of `apps/web/tests`: render/screen/waitFor/
 * fireEvent/cleanup, `vi.mock`, `toBeInstanceOf`, and NO jest-dom matchers.
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

// Mock next/navigation before importing the page. The register page reads
// `useRouter().push` to navigate on success; it does NOT read useSearchParams.
const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

// Mock the auth lib so we can spy on setAccessToken without touching the
// closure used by other tests.
const setAccessTokenMock = vi.fn();

vi.mock("@/lib/auth", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("@/lib/auth");
  return {
    ...actual,
    setAccessToken: (...args: unknown[]) => setAccessTokenMock(...args),
  };
});

import RegisterPage from "@/app/(auth)/register/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => {
  pushMock.mockClear();
  setAccessTokenMock.mockClear();
});

interface StubResponseInit {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}

function buildResponse(stub: StubResponseInit): Response {
  return new Response(
    stub.body !== undefined ? JSON.stringify(stub.body) : null,
    {
      status: stub.status,
      headers: {
        "Content-Type": "application/json",
        ...(stub.headers ?? {}),
      },
    },
  );
}

function stubFetch(stub: StubResponseInit): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => buildResponse(stub));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/**
 * Stub `fetch` with a mock that fails the test if it is ever called. Used by
 * the client-validation tests (Req 8.4): an invalid field must block submit,
 * so no network request may be issued.
 */
function stubFetchNeverCalled(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => {
    throw new Error("fetch should not be called when validation blocks submit");
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const VALID_EMAIL = "alice@example.com";
const VALID_PASSWORD = "Password!12345"; // 14 chars — satisfies the ≥12 rule.

function getEmail(): HTMLInputElement {
  return screen.getByLabelText("Email") as HTMLInputElement;
}

function getPassword(): HTMLInputElement {
  // Exact label text avoids matching "Confirm password".
  return screen.getByLabelText("Password") as HTMLInputElement;
}

function getConfirm(): HTMLInputElement {
  return screen.getByLabelText("Confirm password") as HTMLInputElement;
}

/**
 * Fill the three fields and click submit. Defaults produce a fully valid form
 * (matching passwords ≥12 chars); callers override individual fields to
 * exercise a specific validation branch. When `confirm` is omitted it mirrors
 * the `password` value so the confirm-match rule passes by default.
 */
function fillAndSubmit(
  values: { email?: string; password?: string; confirm?: string } = {},
): void {
  const password = values.password ?? VALID_PASSWORD;
  fireEvent.change(getEmail(), {
    target: { value: values.email ?? VALID_EMAIL },
  });
  fireEvent.change(getPassword(), { target: { value: password } });
  fireEvent.change(getConfirm(), {
    target: { value: values.confirm ?? password },
  });
  fireEvent.click(screen.getByRole("button", { name: /create account/i }));
}

describe("Register form — client-side validation blocks submit (Req 8.4)", () => {
  it("surfaces an inline aria-live email error and does NOT call fetch on an invalid email format", async () => {
    const fetchMock = stubFetchNeverCalled();

    render(<RegisterPage />);
    fillAndSubmit({ email: "not-an-email" });

    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address.")).toBeInstanceOf(
        HTMLElement,
      );
    });

    const fieldError = screen.getByText("Enter a valid email address.");
    expect(fieldError.getAttribute("aria-live")).toBe("polite");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces an inline aria-live password error and does NOT call fetch when the password is shorter than 12 characters", async () => {
    const fetchMock = stubFetchNeverCalled();

    render(<RegisterPage />);
    // 11-char password in BOTH fields (so confirm-match passes and the
    // length rule is the only failure surfaced).
    fillAndSubmit({ password: "shortpass12", confirm: "shortpass12" });

    await waitFor(() => {
      expect(
        screen.getByText("Password must be at least 12 characters."),
      ).toBeInstanceOf(HTMLElement);
    });

    const fieldError = screen.getByText(
      "Password must be at least 12 characters.",
    );
    expect(fieldError.getAttribute("aria-live")).toBe("polite");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces an inline aria-live confirm-password error and does NOT call fetch when the passwords do not match", async () => {
    const fetchMock = stubFetchNeverCalled();

    render(<RegisterPage />);
    // Both passwords are ≥12 chars and the email is valid, so the ONLY
    // failure is the mismatch — proving the confirm-match rule is what blocks.
    fillAndSubmit({
      password: VALID_PASSWORD,
      confirm: "DifferentPass!9876",
    });

    await waitFor(() => {
      expect(screen.getByText("Passwords do not match.")).toBeInstanceOf(
        HTMLElement,
      );
    });

    const fieldError = screen.getByText("Passwords do not match.");
    expect(fieldError.getAttribute("aria-live")).toBe("polite");

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("Register form — non-enumerable failure handling (Req 8.11)", () => {
  it("renders the generic 'Something went wrong. Please try again.' banner and preserves the email on a 5xx", async () => {
    stubFetch({
      status: 500,
      body: {
        type: "internal_error",
        title: "Internal Server Error",
        detail: "boom",
        status: 500,
      },
    });

    render(<RegisterPage />);
    fillAndSubmit({ email: VALID_EMAIL });

    await waitFor(() => {
      expect(
        screen.getByText("Something went wrong. Please try again."),
      ).toBeInstanceOf(HTMLElement);
    });

    // Entered email is preserved across the failed submit (Req 8.11).
    expect(getEmail().value).toBe(VALID_EMAIL);
  });

  it("renders IDENTICAL wording for an 'email already exists'-shaped 400 and a generic 500 (no enumeration)", async () => {
    const message = "Something went wrong. Please try again.";

    // A 400 whose body hints the email is taken...
    stubFetch({
      status: 400,
      body: {
        type: "email_already_registered",
        title: "Email already registered",
        detail: "An account with this email already exists.",
        status: 400,
      },
    });
    const first = render(<RegisterPage />);
    fillAndSubmit({ email: VALID_EMAIL });
    await waitFor(() => {
      expect(screen.getByText(message)).toBeInstanceOf(HTMLElement);
    });
    // The page never surfaces the server's enumerating `detail` — only the
    // generic copy.
    expect(
      screen.queryByText("An account with this email already exists."),
    ).toBeNull();
    first.unmount();

    // ...and a plain 500 produces byte-for-byte the same banner.
    stubFetch({
      status: 500,
      body: { type: "internal_error", title: "Internal", status: 500 },
    });
    render(<RegisterPage />);
    fillAndSubmit({ email: VALID_EMAIL });
    await waitFor(() => {
      expect(screen.getByText(message)).toBeInstanceOf(HTMLElement);
    });
  });

  it("renders a message that includes '30' on 429 with Retry-After: 30", async () => {
    stubFetch({
      status: 429,
      headers: { "Retry-After": "30" },
      body: {
        type: "rate_limited",
        title: "Rate Limited",
        detail: "Too many requests.",
        status: 429,
      },
    });

    render(<RegisterPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(screen.getByText(/30/)).toBeInstanceOf(HTMLElement);
    });
  });
});

describe("Register form — success & enumeration-defense navigation (Req 8.11, 8.12)", () => {
  it("sets the access token and navigates to '/upload' on a genuine 2xx success (token present)", async () => {
    stubFetch({
      status: 201,
      body: {
        access_token: "test.access.token",
        user: {
          id: "01938f00-0000-7000-8000-000000000001",
          email: VALID_EMAIL,
          display_name: "alice",
          created_at: "2025-01-01T00:00:00Z",
          updated_at: "2025-01-01T00:00:00Z",
        },
      },
    });

    render(<RegisterPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(setAccessTokenMock).toHaveBeenCalledWith("test.access.token");
    });
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/upload");
    });
  });

  it("navigates to '/upload' on a 2xx WITHOUT a token (email-exists defense) indistinguishably from real success, without setting a token", async () => {
    // The enumeration-defense path: the "email already exists" case returns
    // 2xx with no token. The client must navigate to /upload exactly as it
    // would on a genuine new-account success, and must NOT call setAccessToken
    // (there is no token to set).
    stubFetch({ status: 200, body: {} });

    render(<RegisterPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/upload");
    });
    expect(setAccessTokenMock).not.toHaveBeenCalled();
  });
});
