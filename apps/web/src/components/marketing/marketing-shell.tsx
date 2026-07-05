import Link from "next/link";
import * as React from "react";

import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Shared page chrome for the secondary **Public** marketing pages — `/about`,
 * `/privacy`, `/terms` (design → New public pages; Req 7.1–7.5).
 *
 * The landing page (`/`) composes its own richer chrome (GlassNav + hero +
 * section islands); these content-first pages instead share this lean shell so
 * every one of them exposes the same landmark structure the SEO + a11y rules
 * require (`seo.md`, `design.md` Section 10.3):
 *
 *   - `<header>` (`banner`) with a `<nav>` linking back to `/` and across the
 *     public pages, plus the theme toggle — so no public page is an orphan
 *     (Req 7.5, internal-links crawlability);
 *   - `<main id="main" tabIndex={-1}>` — the `<SkipNav>` target the
 *     `(marketing)` layout renders first (Req 19.5/19.8 parity with the
 *     landing page); the page passes its content (including its single `<h1>`)
 *     as `children`;
 *   - `<footer>` (`contentinfo`) with the brand mark and legal links.
 *
 * Server Component (no `'use client'`): it renders only static markup plus the
 * `ThemeToggle` client island. Token-only styling — no hex, no inline color
 * (`design.md`).
 *
 * The page owns its single `<h1>` inside `children`; this shell renders no
 * heading of its own, keeping the one-`<h1>`-per-page rule intact (Req 7.1).
 */
const NAV_LINKS = [
  { href: "/about", label: "About" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
] as const;

export function MarketingShell({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="border-b border-border">
        <nav
          aria-label="Primary"
          className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4"
        >
          <Link
            href="/"
            className="rounded-md bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-lg font-semibold tracking-tight text-transparent outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          >
            MatchLayer
          </Link>

          <div className="flex items-center gap-6 text-sm text-text-muted">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="rounded-md outline-none transition-colors hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
              >
                {link.label}
              </Link>
            ))}
            <ThemeToggle />
          </div>
        </nav>
      </header>

      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-3xl flex-1 px-6 py-16 outline-none"
      >
        {children}
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-6 py-10 text-sm text-text-muted sm:flex-row">
          <span className="bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-base font-semibold tracking-tight text-transparent">
            MatchLayer
          </span>
          <nav aria-label="Footer" className="flex items-center gap-6">
            <Link
              href="/privacy"
              className="rounded-md outline-none transition-colors hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            >
              Privacy
            </Link>
            <Link
              href="/terms"
              className="rounded-md outline-none transition-colors hover:text-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            >
              Terms
            </Link>
          </nav>
          <span className="text-text-subtle">
            © {new Date().getFullYear()} MatchLayer
          </span>
        </div>
      </footer>
    </div>
  );
}
