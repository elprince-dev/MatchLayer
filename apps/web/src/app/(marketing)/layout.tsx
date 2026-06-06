import type { Metadata } from "next";
import * as React from "react";

import { SkipNav } from "@/components/skip-nav";
import { buildMarketingMetadata } from "@/lib/seo";

/**
 * Route-group layout for the **Public**, indexable marketing surface
 * (design Section 6.2; `seo.md`; ADR 0006; Req 7.1, 7.5).
 *
 * This is the counterpart to the `(app)` layout's privacy posture: where
 * `(app)` exports `robots: { index: false, follow: false }` to keep
 * PII-bearing pages out of every index, `(marketing)` is the one route group
 * that carries full SEO chrome and is allowed to be crawled. The landing page
 * (`/`) is the only indexable surface among the four MVP screens.
 *
 * Metadata is wired exclusively through the Next.js **Metadata API** via the
 * shared `@/lib/seo` builders — there are no hand-placed `<head>`/`<meta>`
 * tags anywhere in the tree (`conventions.md`, `seo.md`). The values exported
 * here act as the group-wide defaults (title, description, canonical for `/`,
 * Open Graph, Twitter card); individual public pages built later (task 8.7)
 * override `title`/`description`/`canonical` through their own `metadata`
 * export, inheriting everything else.
 *
 * Server Component by default (Req 21.7, `conventions.md`): this layout needs
 * no state, effects, or browser APIs, so it carries no `'use client'`
 * directive. The root `app/layout.tsx` already supplies `<html>`, `<body>`,
 * the Geist fonts, and the theme/query providers.
 *
 * Accessibility wiring (Req 19.5, 19.8, 7.2; design Section 10.3, 10.4): the
 * layout renders the `<SkipNav>` as the **first focusable element**. The
 * `header`/`nav`/`main`/`footer` landmark trio is owned by the **page**
 * (task 8.7), not this layout, and here is why:
 *
 * Task 1.6 originally staged a `<main id="main">` wrapper here. But the
 * marketing page (Req 7.2) must expose four sibling landmarks — `banner`
 * (the GlassNav `<header>`), `<nav>`, `main`, and `contentinfo` (the page
 * `<footer>`). A `<header>` or `<footer>` nested *inside* `<main>` does **not**
 * map to the `banner`/`contentinfo` landmarks — a `<footer>` inside `<main>`
 * is only the main's content footer, and a `<header>` inside `<main>` is a
 * generic section header. Keeping the `<main>` in the layout would therefore
 * force the GlassNav and the site footer to live inside `<main>`, collapsing
 * the required landmark structure.
 *
 * So the layout renders only `<SkipNav>` + `{children}`, and the page composes
 * `<header>` (GlassNav) → `<main id="main" tabIndex={-1}>` → `<footer>` as true
 * siblings — the cleanest valid landmark structure. The `<main>`'s `id="main"`
 * matches the {@link SkipNav} default target, and `tabIndex={-1}` makes it a
 * programmatic focus target without inserting it into the tab sequence. Each
 * future public page that carries its own GlassNav + footer provides its own
 * `<main id="main">` the same way.
 */
export const metadata: Metadata = buildMarketingMetadata({ path: "/" });

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <>
      <SkipNav />
      {children}
    </>
  );
}
