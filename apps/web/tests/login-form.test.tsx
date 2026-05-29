/**
 * Component test for the login page form (Auth Pages Design §14.3).
 *
 * Validates Requirement 12.7 (literal error strings on 401/423) and
 * Requirement 12.8 (Retry-After surfacing on 429), plus the success path:
 * 200 → setAccessToken + navigation to ?next= (or "/" by default).
 *
 * The login page is a client component that:
 *   - Reads `?next=` from the URL (validated same-origin per §13.7).
 *   - Calls `POST /api/v1/auth/login` directly via fetch.
 *   - Maps response status to UI:
 *       - 401 → "Email or password is incorrect."
 *       - 423 → "Account is temporarily locked. Try again later."
 *       - 429 → <RetryAfterMessage> with the Retry-After seconds.
 *       - 200 → setAccessToken(access_token) + router.push(safeNext).
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

function stubFetch(stub: StubResponseInit): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => buildResponse(stub)),
  );
}

function fillAndSubmit(): void {
  const email = screen.getByLabelText(/email/i) as HTMLInputElement;
  const password = screen.getByLabelText(/password/i) as HTMLInputElement;
  fireEvent.change(email, { target: { value: "alice@example.com" } });
  fireEvent.change(password, { target: { value: "Password!12345" } });
  const submitBtn = screen.getByRole("button", { name: /sign in/i });
  fireEvent.click(submitBtn);
}

describe("Login form (Requirement 12.7, 12.8 — Auth Pages Design §14.3)", () => {
  it("renders the literal 'Email or password is incorrect.' string on 401", async () => {
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
    fillAndSubmit();

    await waitFor(() => {
      expect(
        screen.getByText("Email or password is incorrect."),
      ).toBeInstanceOf(HTMLElement);
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

  it("falls back to '/' when ?next= is missing or unsafe (§13.7)", async () => {
    // Unsafe next (contains ://) should be rejected and replaced with /
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
      expect(pushMock).toHaveBeenCalledWith("/");
    });
  });
});
