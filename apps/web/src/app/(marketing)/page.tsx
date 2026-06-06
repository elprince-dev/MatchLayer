import { BarChart3, ScanSearch, Sparkles, Tags } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import type { Metadata } from "next";
import * as React from "react";

import { FeatureCard } from "@/components/landing/feature-card";
import { FinalCTA } from "@/components/landing/final-cta";
import { GlassNav } from "@/components/landing/glass-nav";
import { Hero } from "@/components/landing/hero";
import { HowItWorks } from "@/components/landing/how-it-works";
import { SectionReveal } from "@/components/landing/section-reveal";
import { About, TrustSignals } from "@/components/landing/trust-signals";
import { buildMarketingMetadata } from "@/lib/seo";

/**
 * Landing_Page — the public, indexable marketing homepage at `/`
 * (design Section 6.2, Section 8.2; Req 4.8, 7.1, 7.2, 7.4, 7.5, 7.6, 21.7,
 * 21.9). This module replaces the legacy `app/page.tsx`, which is removed in
 * the same change so `/` resolves to exactly one page (Req 21.9 — see below).
 *
 * ## Server Component (Req 21.7)
 * This page itself is a Server Component (no `'use client'`) — only Server
 * Components may export `metadata`. It composes the section islands: GlassNav,
 * Hero, HowItWorks, and FinalCTA are `'use client'` (scroll/motion/state);
 * FeatureCard, TrustSignals, and About are Server Components. {@link
 * SectionReveal} is the one small client wrapper the page adds so the
 * server-rendered features grid + trust/about bands still fade up on scroll
 * (Req 4.8); the components that own their reveal internally (HowItWorks,
 * FinalCTA) are not double-wrapped.
 *
 * ## Landmark structure (Req 7.2; design Section 10.3)
 * The page owns the full `header → main → footer` landmark trio as **siblings**:
 *
 *   - {@link GlassNav} renders the `<header>` + `<nav>` (`banner` landmark);
 *   - `<main id="main" tabIndex={-1}>` wraps the section content. `id="main"`
 *     is the {@link import("@/components/skip-nav").SkipNav} target the
 *     `(marketing)` layout renders first; `tabIndex={-1}` makes it a
 *     programmatic focus target without joining the tab sequence;
 *   - `<footer>` (`contentinfo` landmark) closes the page.
 *
 * The `<main>` lives **here**, not in `(marketing)/layout.tsx`: a `<header>` or
 * `<footer>` nested inside `<main>` would not expose the `banner`/`contentinfo`
 * landmarks Req 7.2 requires, so the layout renders only `<SkipNav>` +
 * `{children}` and the page composes the three sibling landmarks (see the
 * layout's header comment).
 *
 * ## Single `<h1>` + heading order (Req 7.2)
 * Exactly one `<h1>` exists on the page — it lives in {@link Hero}. Every other
 * section (Features, How it works, Trust signals, About, Final CTA) uses an
 * `<h2>`, and the cards beneath them use `<h3>`, giving a sequential
 * h1 → h2 → h3 outline with no skipped levels.
 *
 * ## Section order (design Section 8.2; Req 4.8)
 * Hero → Features → How it works → Trust signals → About → Final CTA → footer —
 * the logical below-the-hero order the wireframe lays out.
 *
 * ## SEO metadata (Req 7.1, 7.4, 7.5)
 * `metadata` is exported via the Next.js Metadata API using the shared
 * `@/lib/seo` {@link buildMarketingMetadata} helper (no hand-placed `<head>`
 * tags — `seo.md`, `conventions.md`). It emits a unique title (≤60), a
 * description (≤155), a **self-referential canonical** for `/`, Open Graph, and
 * a Twitter `summary_large_image` card. The `(marketing)` layout already
 * exports the same builder for the group; re-exporting it here pins the
 * landing page's own canonical/OG/Twitter and is conflict-free because both
 * resolve the canonical to the identical `/`. `/` stays in the sitemap
 * (`app/sitemap.ts` → `PUBLIC_ROUTES = ["/"]`) and carries no `noindex`, so it
 * remains the one indexable MVP surface (Req 7.5).
 *
 * ## next/image (Req 7.6, 7.4)
 * There are **no raster images on this page** — the hero "gauge" is an inline
 * SVG, every icon is a Lucide SVG, and the brand mark is gradient-clipped text.
 * So there is no `next/image` asset to add here; the requirement is satisfied
 * vacuously, and any informational raster image added later MUST use
 * `next/image` with explicit `width`/`height` (to prevent CLS) and descriptive
 * `alt` (empty `alt=""` for decorative imagery).
 *
 * ## Honesty (Req 5.1)
 * No copy on this page describes the scoring as semantic, embeddings-, AI-, or
 * LLM-powered — Phase 1 scoring is keyword + TF-IDF. The single roadmap feature
 * (semantic analysis) is rendered through {@link FeatureCard}'s `badge` as a
 * clearly-labelled "Coming soon" card with no usable control (Req 5.2, 5.3,
 * 5.5).
 *
 * All styling is token-only — no hex, no inline color, no arbitrary bracket
 * color utilities (Req 21.2).
 */
export const metadata: Metadata = buildMarketingMetadata({ path: "/" });

/**
 * A single feature tile's content for the Features grid (Req 4.1). Author-owned
 * marketing copy — no backend fields. `badge` marks a Roadmap_Feature.
 */
interface Feature {
  readonly icon: LucideIcon;
  readonly title: string;
  readonly description: string;
  readonly badge?: "Coming soon" | "Planned";
}

/**
 * The Features grid content (Req 4.1, 5.1–5.3). Three current capabilities plus
 * one explicitly-labelled roadmap card. Every title ≤40 chars and description
 * ≤120 chars; current scoring is described only as keyword/TF-IDF (Req 5.1),
 * and the roadmap "Semantic analysis" card carries a "Coming soon" badge so it
 * is never presented as available now (Req 5.2, 5.3).
 */
const FEATURES: readonly Feature[] = [
  {
    icon: ScanSearch,
    title: "Transparent ATS score",
    description:
      "See how an ATS reads your resume against a job — keyword and TF-IDF based, never a black box.",
  },
  {
    icon: Tags,
    title: "Matched & missing keywords",
    description:
      "Know which job keywords your resume already hits and which it is missing, ranked by weight.",
  },
  {
    icon: BarChart3,
    title: "Score breakdown",
    description:
      "See how text similarity and keyword coverage combine into your final match score.",
  },
  {
    icon: Sparkles,
    title: "Semantic analysis",
    description:
      "Deeper meaning-based matching beyond keywords is on the roadmap — not part of this release.",
    badge: "Coming soon",
  },
];

export default function LandingPage(): React.JSX.Element {
  return (
    <>
      {/* banner landmark — fixed glass nav (its own <header>/<nav>). */}
      <GlassNav />

      {/* main landmark — the SkipNav target rendered by the (marketing) layout. */}
      <main id="main" tabIndex={-1} className="outline-none">
        {/* Hero owns the page's single <h1> and the #hero anchor GlassNav reads. */}
        <Hero />

        {/* FEATURES — the page owns the responsive grid (Req 4.1): 1 col <640px,
            2 col 640–1024px, 4 col >1024px. Wrapped in SectionReveal for the
            scroll fade-up (Req 4.8); reduced motion renders final immediately. */}
        <section
          id="features"
          aria-labelledby="features-heading"
          className="scroll-mt-20 py-16 md:py-24"
        >
          <div className="mx-auto max-w-7xl px-6">
            <SectionReveal>
              <div className="mx-auto max-w-3xl text-center">
                <h2
                  id="features-heading"
                  className="text-3xl font-semibold tracking-tight text-text md:text-4xl"
                >
                  Everything you need to read your resume like an ATS
                </h2>
                <p className="mt-4 text-base text-text-muted">
                  Transparent, keyword-based scoring — see what matched, what is
                  missing, and how the number is built.
                </p>
              </div>

              <div className="mt-12 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
                {FEATURES.map((feature) => (
                  <FeatureCard
                    key={feature.title}
                    icon={feature.icon}
                    title={feature.title}
                    description={feature.description}
                    badge={feature.badge}
                  />
                ))}
              </div>
            </SectionReveal>
          </div>
        </section>

        {/* HOW IT WORKS — owns its own #how-it-works anchor + scroll reveal. */}
        <HowItWorks />

        {/* TRUST SIGNALS — Server Component; page adds the scroll reveal. */}
        <SectionReveal>
          <TrustSignals />
        </SectionReveal>

        {/* ABOUT — the #about anchor target; Server Component + page reveal. */}
        <SectionReveal>
          <About />
        </SectionReveal>

        {/* FINAL CTA — owns its own scroll reveal. */}
        <FinalCTA />
      </main>

      {/* contentinfo landmark — site footer. */}
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
    </>
  );
}
