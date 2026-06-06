import type { Metadata } from "next";
import * as React from "react";

import { AuthBackground } from "@/components/auth/auth-background";
import { AuthCard } from "@/components/auth/auth-card";
import { SkipNav } from "@/components/skip-nav";

/**
 * Route-group layout for `/login` and `/register` (design Section 8.3).
 *
 * This is a **Server Component** so it can export `metadata` (Req 8.7, 8.8;
 * seo.md route classification). Next.js forbids a `metadata`/`generateMetadata`
 * export from a `"use client"` module, so the previously client-only layout is
 * split: the animated gradient-mesh/noise background now lives in the
 * `<AuthBackground>` client island (`@/components/auth/auth-background`), while
 * the layout itself stays server-rendered and owns the noindex contract.
 *
 * Renders the centered-card auth shell:
 *
 *   - Background: `<AuthBackground>` — a static token-driven gradient mesh
 *     plus an animated grayscale fractal-noise overlay, both behind the
 *     content at negative z and `pointer-events-none` so they never overlay or
 *     intercept the card (Req 8.6). The noise animation is gated by
 *     `prefers-reduced-motion` inside that component (Req 8.5, 8.7).
 *
 *   - Top: the "MatchLayer" wordmark in the violet → cyan brand gradient,
 *     using the `bg-gradient-to-br from-brand to-brand-2 bg-clip-text
 *     text-transparent` recipe (the same gradient-wordmark recipe used by the
 *     GlassNav brand mark on the landing page). Sized smaller here than on the
 *     landing page because the auth shell is "restrained" per `design.md` — the
 *     wordmark is a brand mark, not a hero. Rendered as a `<span>` (not an
 *     anchor) so it is
 *     not navigation chrome.
 *
 *   - Card slot: `<AuthCard>` wraps the route's `{children}`. The card chrome
 *     (`max-w-md`, `rounded-2xl`, `border-strong`, `bg-bg-elevated`, layered
 *     shadow) is owned by `AuthCard` so each individual auth page is just a
 *     form, not a card-builder.
 *
 * The root `app/layout.tsx` already supplies `<html>`, `<body>`, the Geist
 * fonts, and the next-themes provider; this layout therefore renders only the
 * auth-shell DOM and inherits everything else.
 */

/**
 * Non-indexing control for the auth surface (Req 8.7, 8.8; seo.md; ADR 0006).
 *
 * Per the route classification in `seo.md`, `/login` and `/register` are
 * **publicly reachable but must never be indexed** — they front the
 * authentication flow and the redesign keeps the landing page (`/`) as the
 * only indexable surface among these screens (Req 8.10). Exporting
 * `robots: { index: false, follow: false }` from this route-group layout makes
 * both nested auth routes inherit a `noindex, nofollow` directive via the
 * Next.js Metadata API. This is defense in depth alongside the
 * `X-Robots-Tag: noindex, nofollow` response header that `src/proxy.ts` stamps
 * on every `(auth)` HTML response. No sitemap/canonical/Open Graph metadata is
 * added here (the pages are also excluded from `app/sitemap.ts`, Req 8.9).
 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="relative flex min-h-screen flex-col bg-bg text-text">
      {/*
       * Skip-navigation link (Req 19.8; design Section 10.3): the first
       * focusable element on the auth pages, moving focus past the brand
       * wordmark to the `<main id="main">` content landmark below.
       */}
      <SkipNav />

      {/*
       * Decorative gradient-mesh + animated-noise background. Lives in its own
       * client island so this layout can export `metadata`. Both layers are
       * behind the content (negative z) and `pointer-events-none`, so they
       * never overlay or intercept the card (Req 8.6); the noise animation is
       * disabled under `prefers-reduced-motion` (Req 8.7).
       */}
      <AuthBackground />

      {/*
       * Main content landmark + skip-link target (Req 19.5, 19.8; design
       * Section 10.4). `id="main"` matches the `<SkipNav>` href;
       * `tabIndex={-1}` makes it a programmatic focus target without joining
       * the tab sequence. `justify-center` + `items-center` centers the card
       * on BOTH axes; `min-h-screen` on the wrapper makes the vertical
       * centering hold from 320px up to ≥1920px (Req 8.6). `px-6` keeps a
       * gutter at the 320px floor so the `max-w-md` card never causes
       * horizontal overflow, and the card's own `max-w-md` caps its width on
       * ultrawide viewports so it stays centered rather than stretching.
       */}
      <main
        id="main"
        tabIndex={-1}
        className="flex flex-1 flex-col items-center justify-center px-6 py-16 outline-none"
      >
        <span
          aria-label="MatchLayer"
          className="mb-8 bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-3xl font-semibold tracking-tight text-transparent"
        >
          MatchLayer
        </span>
        <AuthCard>{children}</AuthCard>
      </main>
    </div>
  );
}
