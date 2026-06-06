"use client";

import {
  ThemeProvider as NextThemesProvider,
  type ThemeProviderProps,
} from "next-themes";

/**
 * Application theme provider.
 *
 * Thin wrapper around `next-themes`'s `ThemeProvider` that pins the MatchLayer
 * defaults from `design.md` §6.4 (Req 2.1, 2.2, 2.6, 2.7, 21.5):
 *
 *   - `attribute="class"`           — toggles the `dark` class on `<html>`,
 *                                     which `globals.css` reads via
 *                                     `@custom-variant dark (&:where(.dark, .dark *))`.
 *   - `defaultTheme="dark"`         — render in Dark_Mode as the initial
 *                                     default when no preference is stored,
 *                                     regardless of the OS `prefers-color-scheme`
 *                                     (Req 2.2, 2.6).
 *   - `enableSystem={false}`        — disable the "System" option at the library
 *                                     level so only Dark_Mode / Light_Mode are
 *                                     selectable and the OS preference is never
 *                                     used as the default (Req 2.1, 2.7).
 *   - `disableTransitionOnChange`   — suppress CSS transitions during the
 *                                     theme swap so the page does not flash a
 *                                     mismatched intermediate frame.
 *
 * `next-themes` injects its theme-resolution script before first paint, applying
 * the stored preference (or `dark`) to `<html class="dark">` before any content
 * renders, so no wrong-theme frame is visible (Req 2.4, 2.6). `layout.tsx` sets
 * `<html suppressHydrationWarning>` so React tolerates that pre-paint mutation.
 *
 * Any prop callers pass in overrides the corresponding default (the spread runs
 * after the literal defaults), so this component stays usable for tests or
 * future surfaces that need a different configuration.
 */
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  );
}
