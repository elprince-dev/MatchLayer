import Link from "next/link";

import { HeroText } from "@/components/hero-text";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";

/**
 * Placeholder landing page (route: `/`).
 *
 * Server component by design — `conventions.md` says "prefer Server Components;
 * mark `'use client'` only when needed". The two pieces that genuinely need
 * client APIs (Framer Motion in `<HeroText />`, next-themes in
 * `<ThemeToggle />`) are isolated as small client islands; this page itself
 * stays static and renders at build time.
 *
 * Navigation: the top nav and hero CTAs link to the auth surface
 * (`/login`, `/register`). These are the only public entry points into the
 * authenticated app in Phase 1 — without them a first-time visitor on `/`
 * would have no way in. `next/link` keeps client-side navigation fast and the
 * links are plain anchors (good for SEO + accessibility on this public page).
 *
 * Tokens only — no hex literals, and no shadcn defaults like `bg-background` /
 * `text-foreground` (those tokens don't exist in our `globals.css`).
 *
 * WCAG AA verification (per design.md "Accessibility"):
 *   - Tagline `text-text-muted` on `bg-bg`:
 *       light = #52525B on #FFFFFF ≈ 7.2:1  (AA pass for both normal & large)
 *       dark  = #A1A1AA on #0A0A0B ≈ 9.4:1  (AA pass)
 *   - Hero "MatchLayer" wordmark uses the brand gradient. At `text-6xl
 *     font-semibold` it counts as "large text" under WCAG, where the AA
 *     threshold drops to 3:1 — comfortably met by both `--color-brand` and
 *     `--color-brand-2` against `bg-bg` in either theme. The readable body
 *     copy is carried by the tagline, not the gradient wordmark.
 */
export default function Home(): React.JSX.Element {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="flex items-center justify-between p-6">
        <span className="text-sm font-semibold tracking-tight text-text">
          MatchLayer
        </span>
        <nav className="flex items-center gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild size="sm">
            <Link href="/register">Get started</Link>
          </Button>
          <ThemeToggle />
        </nav>
      </header>

      <main className="flex flex-1 flex-col items-center justify-center px-6 pb-24 text-center">
        <HeroText />
        <p className="mt-6 text-lg text-text-muted sm:text-xl">
          AI-native ATS, transparent scoring.
        </p>
        <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
          <Button asChild size="lg">
            <Link href="/register">Get started — it&apos;s free</Link>
          </Button>
          <Button asChild variant="outline" size="lg">
            <Link href="/login">Sign in</Link>
          </Button>
        </div>
      </main>
    </div>
  );
}
