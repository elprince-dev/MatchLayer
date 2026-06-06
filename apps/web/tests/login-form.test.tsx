/**
 * Component test for the login page form (Auth Pages Design §14.3; frontend-
 * redesign Task 7.4, Req 8.4, 8.11, 8.12; design Testing Strategy).
 *
 * Validates Requirement 12.7 (literal error strings on 401/423) and
 * Requirement 12.8 (Retry-After surfacing on 429), plus the success path:
 * 200 → setAccessToken + navigation to ?next= (or "/upload" by default), and
 * the redesign's added coverage:
 *   - Req 8.4: client-side validation — an invalid email format and a
 *     <12-char password each surface an inline `FieldError` (announced via
 *     `aria-live="polite"`) AND block submit (no `fetch` is issued).
 *   - Req 8.11: on a 401 the non-enumerable banner reads "Email or password
 *     is incorrect." and the entered email is PRESERVED in the field.
 *   - Req 8.12: a successful sign-in navigates to `/upload` (default), a safe
 *     `?next=` is honored, and an unsafe `?next=` falls back to `/upload`.
 *
 * The login page is a client component that:
 *   - Reads `?next=` from the URL (validated same-origin per §13.7).
 *   - Calls `POST /api/v1/auth/login` directly via global `fetch` (NOT
 *     `apiFetch`), so the global is stubbed via `vi.stubGlobal`.
 *   - Maps response status to UI:
 *       - 401 → "Email or password is incorrect." (email preserved)
 *       - 423 → "Account is temporarily locked. Try again later."
 *       - 429 → <RetryAfterMessage> with the Retry-After seconds.
 *       - 200 → setAccessToken(access_token) + router.push(safeNext).
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

// Mock next/navigation before importing the page.
const pushMock = vi.fn();
const searchParamsMock = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => searchParamsMock,
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

import LoginPage from "@/app/(auth)/login/page";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => {
  pushMock.mockClear();
  setAccessTokenMock.mockClear();
  // Reset search params between tests.
  for (const key of Array.from(searchParamsMock.keys())) {
    searchParamsMock.delete(key);
  }
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
 * the client-validation tests (Req 8.4): an invalid email or a too-short
 * password must block submit, so no network request may be issued.
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

function fillAndSubmit(
  values: { email?: string; password?: string } = {},
): void {
  const email = screen.getByLabelText(/email/i) as HTMLInputElement;
  const password = screen.getByLabelText(/password/i) as HTMLInputElement;
  fireEvent.change(email, { target: { value: values.email ?? VALID_EMAIL } });
  fireEvent.change(password, {
    target: { value: values.password ?? VALID_PASSWORD },
  });
  const submitBtn = screen.getByRole("button", { name: /sign in/i });
  fireEvent.click(submitBtn);
}

describe("Login form (Requirement 12.7, 12.8 — Auth Pages Design §14.3)", () => {
  it("renders the literal 'Email or password is incorrect.' string on 401 and preserves the entered email (Req 8.11)", async () => {
    stubFetch({
      status: 401,
      body: {
        type: "invalid_credentials",
        title: "Invalid Credentials",
        detail: "Email or password is incorrect.",
        status: 401,
      },
    });

    render(<LoginPage />);
    fillAndSubmit({ email: VALID_EMAIL });

    await waitFor(() => {
      expect(
        screen.getByText("Email or password is incorrect."),
      ).toBeInstanceOf(HTMLElement);
    });

    // The entered email survives the failed submit (Req 8.11): the
    // uncontrolled RHF input is never reset on the 401 path.
    const email = screen.getByLabelText(/email/i) as HTMLInputElement;
    expect(email.value).toBe(VALID_EMAIL);
  });

  it("uses identical, non-enumerable wording for the 401 failure regardless of which credential was wrong (Req 8.11)", async () => {
    // The page maps every 401 to one literal string with no branch on
    // "user not found" vs "wrong password", so two different wrong-credential
    // submissions are indistinguishable to the client.
    const message = "Email or password is incorrect.";
    const body = {
      type: "invalid_credentials",
      title: "Invalid Credentials",
      detail: message,
      status: 401,
    };

    stubFetch({ status: 401, body });
    const first = render(<LoginPage />);
    fillAndSubmit({ email: "nobody@example.com" });
    await waitFor(() => {
      expect(screen.getByText(message)).toBeInstanceOf(HTMLElement);
    });
    first.unmount();

    stubFetch({ status: 401, body });
    render(<LoginPage />);
    fillAndSubmit({ email: "alice@example.com" });
    await waitFor(() => {
      expect(screen.getByText(message)).toBeInstanceOf(HTMLElement);
    });
  });

  it("renders the literal 'Account is temporarily locked. Try again later.' string on 423", async () => {
    stubFetch({
      status: 423,
      body: {
        type: "account_locked",
        title: "Account Locked",
        detail: "Account is temporarily locked. Try again later.",
        status: 423,
      },
    });

    render(<LoginPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(
        screen.getByText("Account is temporarily locked. Try again later."),
      ).toBeInstanceOf(HTMLElement);
    });
  });

  it("renders a message that includes '30' on 429 with Retry-After: 30 (Requirement 12.8)", async () => {
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

    render(<LoginPage />);
    fillAndSubmit();

    await waitFor(() => {
      // RetryAfterMessage renders the seconds count somewhere in its output.
      const node = screen.getByText(/30/);
      expect(node).toBeInstanceOf(HTMLElement);
    });
  });

  it("calls setAccessToken and navigates to the safe next path on 200", async () => {
    searchParamsMock.set("next", "/dashboard");
    stubFetch({
      status: 200,
      body: {
        access_token: "test.access.token",
        user: {
          id: "01938f00-0000-7000-8000-000000000001",
          email: "alice@example.com",
          display_name: "alice",
          created_at: "2025-01-01T00:00:00Z",
          updated_at: "2025-01-01T00:00:00Z",
        },
      },
    });

    render(<LoginPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(setAccessTokenMock).toHaveBeenCalledWith("test.access.token");
    });
    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("falls back to '/upload' when ?next= is missing or unsafe (§8.3, Req 8.12)", async () => {
    // Unsafe next (contains ://) should be rejected and replaced with the
    // post-auth landing destination, the Upload_Page (/upload) — not the
    // public landing and not an attacker-controlled absolute URL.
    searchParamsMock.set("next", "https://evil.com/phish");
    stubFetch({
      status: 200,
      body: {
        access_token: "test.access.token",
        user: {
          id: "01938f00-0000-7000-8000-000000000001",
          email: "alice@example.com",
          display_name: "alice",
          created_at: "2025-01-01T00:00:00Z",
          updated_at: "2025-01-01T00:00:00Z",
        },
      },
    });

    render(<LoginPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/upload");
    });
  });

  it("navigates to '/upload' by default when no ?next= is present (Req 8.12)", async () => {
    // No `next` param is set in this test (cleared in beforeEach). A
    // successful sign-in lands on the Upload_Page — the post-auth destination.
    stubFetch({
      status: 200,
      body: {
        access_token: "test.access.token",
        user: {
          id: "01938f00-0000-7000-8000-000000000001",
          email: "alice@example.com",
          display_name: "alice",
          created_at: "2025-01-01T00:00:00Z",
          updated_at: "2025-01-01T00:00:00Z",
        },
      },
    });

    render(<LoginPage />);
    fillAndSubmit();

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/upload");
    });
  });
});

describe("Login form — client-side validation blocks submit (Req 8.4)", () => {
  it("surfaces an inline aria-live email error and does NOT call fetch on an invalid email format", async () => {
    const fetchMock = stubFetchNeverCalled();

    render(<LoginPage />);
    // Invalid email, otherwise-valid password: the email rule must fire and
    // block the submit before any network request.
    fillAndSubmit({ email: "not-an-email", password: VALID_PASSWORD });

    await waitFor(() => {
      expect(screen.getByText("Enter a valid email address.")).toBeInstanceOf(
        HTMLElement,
      );
    });

    // The inline error is announced via an aria-live region adjacent to the
    // field (FieldError → polite live region; Req 8.4, 19.4).
    const fieldError = screen.getByText("Enter a valid email address.");
    expect(fieldError.getAttribute("aria-live")).toBe("polite");

    // Submit was blocked: no fetch issued.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces an inline aria-live password error and does NOT call fetch when the password is shorter than 12 characters", async () => {
    const fetchMock = stubFetchNeverCalled();

    render(<LoginPage />);
    // Valid email, 11-char password (one short of the ≥12 rule).
    fillAndSubmit({ email: VALID_EMAIL, password: "shortpass12" });

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
});
