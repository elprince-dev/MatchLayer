import "./globals.css";

import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { ThemeProvider } from "@/components/theme-provider";

/**
 * Root layout for the MatchLayer web app.
 *
 * Wires the three pieces every page below depends on:
 *
 *   1. The global stylesheet — imported first so Tailwind's preflight and the
 *      brand tokens defined in `globals.css` are loaded before any component.
 *   2. The Geist Sans and Geist Mono variable fonts via `next/font/google`,
 *      exposed on `<html>` as `--font-geist-sans` / `--font-geist-mono`. The
 *      `@theme inline` block in `globals.css` reads those CSS variables and
 *      binds them to Tailwind's `font-sans` / `font-mono` utilities.
 *   3. The next-themes `ThemeProvider`, which toggles the `dark` class on
 *      `<html>` to flip the token palette between light and dark. The
 *      `suppressHydrationWarning` prop on `<html>` is required because the
 *      provider mutates that class during hydration to apply the resolved
 *      theme — without the suppression React would warn about the mismatch.
 *
 * The `<body>` uses only design-system tokens (`bg-bg`, `text-text`,
 * `font-sans`, `antialiased`); no hex literals leak into the markup.
 */

const geistSans = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "MatchLayer",
  description: "AI-native ATS, transparent scoring.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable}`}
    >
      <body className="bg-bg text-text font-sans antialiased">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
