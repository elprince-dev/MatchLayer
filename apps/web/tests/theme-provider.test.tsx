/**
 * Unit test for `<ThemeProvider>` — foundation wiring (Task 1.7).
 *
 * `apps/web/src/components/theme-provider.tsx` is a thin wrapper around
 * `next-themes`'s `ThemeProvider` that pins the MatchLayer theme defaults from
 * design.md §6.4. The acceptance criteria this test guards:
 *
 *   - Requirement 2.2 / 2.6 — `defaultTheme="dark"`: with no stored preference
 *     the app renders Dark_Mode as the initial default.
 *   - Requirement 2.1 / 2.7 — `enableSystem={false}`: the "System" option is
 *     disabled at the library level so only Dark/Light are selectable and the
 *     OS `prefers-color-scheme` is never used as the default.
 *   - design.md §6.4 — `attribute="class"` (toggles `dark` on `<html>`, read by
 *     `globals.css`) and `disableTransitionOnChange` (no mismatched frame on
 *     swap).
 *
 * Strategy: mock `next-themes` so the inner `ThemeProvider` is a spy. Rendering
 * `<ThemeProvider>` then lets us assert the exact props our wrapper forwards —
 * the config IS the contract here, not any rendered DOM. The wrapper spreads
 * caller `...props` AFTER its literal defaults, so a final test confirms a
 * caller can override a default (the documented escape hatch for tests/future
 * surfaces).
 *
 * The mock factory references only the `vi.hoisted` spy, so it is safe under
 * Vitest's mock hoisting.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// `vi.hoisted` makes the spy available to the hoisted `vi.mock` factory below
// without tripping the "factory may not reference out-of-scope variables" rule.
const { nextThemesSpy } = vi.hoisted(() => ({ nextThemesSpy: vi.fn() }));

// Replace `next-themes`' `ThemeProvider` with a spy component that records the
// props our wrapper forwards and renders its children so the tree mounts. The
// `type ThemeProviderProps` import in the component under test is type-only and
// erased at runtime, so the mock need not provide it.
vi.mock("next-themes", () => ({
  ThemeProvider: (props: { children?: React.ReactNode }) => {
    nextThemesSpy(props);
    return props.children ?? null;
  },
}));

import { ThemeProvider } from "@/components/theme-provider";

afterEach(() => {
  cleanup();
  nextThemesSpy.mockClear();
});

describe("<ThemeProvider> theme defaults (Requirements 2.1, 2.2, 2.6, 2.7; design §6.4)", () => {
  it("forwards dark as the default theme and disables the System option", () => {
    render(
      <ThemeProvider>
        <span>child</span>
      </ThemeProvider>,
    );

    // The two acceptance-critical props: dark default + System disabled.
    expect(nextThemesSpy).toHaveBeenCalledTimes(1);
    expect(nextThemesSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        defaultTheme: "dark",
        enableSystem: false,
      }),
    );
  });

  it("forwards the class attribute strategy and disableTransitionOnChange (design §6.4)", () => {
    render(
      <ThemeProvider>
        <span>child</span>
      </ThemeProvider>,
    );

    expect(nextThemesSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        attribute: "class",
        disableTransitionOnChange: true,
      }),
    );
  });

  it("renders its children inside the provider tree", () => {
    const { getByText } = render(
      <ThemeProvider>
        <span>themed-child</span>
      </ThemeProvider>,
    );

    expect(getByText("themed-child")).toBeInstanceOf(HTMLSpanElement);
  });

  it("lets a caller override a default (props spread after the defaults)", () => {
    // The wrapper spreads `...props` after its literal defaults, so an explicit
    // prop wins. This keeps the component usable for tests/future surfaces that
    // need a different configuration, without weakening the production default.
    render(
      <ThemeProvider defaultTheme="light">
        <span>child</span>
      </ThemeProvider>,
    );

    expect(nextThemesSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ defaultTheme: "light" }),
    );
  });
});
