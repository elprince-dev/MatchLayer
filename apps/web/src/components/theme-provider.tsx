"use client";

import {
  ThemeProvider as NextThemesProvider,
  type ThemeProviderProps,
} from "next-themes";

/**
 * Application theme provider.
 *
 * Thin wrapper around `next-themes`'s `ThemeProvider` that pins the MatchLayer
 * defaults from `design.md` §7.6:
 *
 *   - `attribute="class"`           — toggles the `dark` class on `<html>`,
 *                                     which `globals.css` reads via
 *                                     `@custom-variant dark (&:where(.dark, .dark *))`.
 *   - `defaultTheme="system"`       — respect the OS preference until the user
 *                                     explicitly picks a theme.
 *   - `enableSystem`                — allow `system` as a selectable value.
 *   - `disableTransitionOnChange`   — suppress CSS transitions during the
 *                                     theme swap so the page does not flash a
 *                                     mismatched intermediate frame.
 *
 * Any prop callers pass in overrides the corresponding default (the spread runs
 * after the literal defaults), so this component stays usable for tests or
 * future surfaces that need a different configuration.
 */
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  );
}
