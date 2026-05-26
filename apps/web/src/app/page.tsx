import { HeroText } from "@/components/hero-text";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Placeholder landing page (route: `/`).
 *
 * Server component by design — `conventions.md` says "prefer Server Components;
 * mark `'use client'` only when needed". The two pieces that genuinely need
 * client APIs (Framer Motion in `<HeroText />`, next-themes in
 * `<ThemeToggle />`) are isolated as small client islands; this page itself
 * stays static and renders at build time.
 *
 * Layout:
 *   - Outer `<div>` is a full-viewport flex column with the design-system
 *     `bg-bg` / `text-text` tokens. The body in `layout.tsx` already paints
 *     these, but setting them here too keeps the page self-contained for any
 *     future parallel-route / template wrappers.
 *   - Top nav row hosts the theme toggle in the right corner (`flex
 *     justify-end p-6`).
 *   - The hero block fills the remaining viewport (`flex-1`) and centers the
 *     animated wordmark over the muted tagline.
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
      <header className="flex justify-end p-6">
        <ThemeToggle />
      </header>

      <main className="flex flex-1 flex-col items-center justify-center px-6 pb-24 text-center">
        <HeroText />
        <p className="mt-6 text-lg text-text-muted sm:text-xl">
          AI-native ATS, transparent scoring.
        </p>
      </main>
    </div>
  );
}
