import { FileText, Lock, ShieldCheck, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Landing_Page trust-signals band + the `#about` anchor section
 * (design Section 8.2; Req 4.3, 4.4, 4.7, 5.1).
 *
 * ## Exports (the page assembly in task 8.7 composes these)
 *
 * This file exports **two** sibling Server Components, mirroring the two
 * distinct horizontal bands the design lays out (TRUST SIGNALS, then ABOUT):
 *
 *   - {@link TrustSignals} — the row of truthful capability claims.
 *   - {@link About} — the `<section id="about">` that the GlassNav "About"
 *     in-page link (`#about`) scrolls to.
 *
 * They are kept in one file because they are adjacent, content-only, and share
 * the same honesty contract; the marketing page renders `<TrustSignals />`
 * directly above `<About />`.
 *
 * ## Honesty contract (Req 4.3, 4.4, 5.1) — the load-bearing rule
 *
 * The trust signals communicate **only truthful, supportable claims**. There
 * are deliberately **no fabricated metrics** (no user counts, no
 * "resumes processed", no accuracy percentages), **no testimonials**, and **no
 * company logos** (Req 4.3). Each claim is drawn from the four sanctioned,
 * MVP-real capabilities in Req 4.4 — privacy-first processing, PDF & DOCX
 * support, secure file handling, and fast ATS analysis — and every claim
 * describes something that exists in the current Phase 1 product.
 *
 * The current scoring approach is described **only** as keyword + TF-IDF
 * matching; it is never called semantic, embeddings-based, AI-, or LLM-powered
 * (Req 5.1). Where the {@link About} copy touches the roadmap (richer semantic
 * analysis), it is stated as explicitly **not** part of this release, so a
 * planned capability is never presented as available today.
 *
 * ## Server Components (no `"use client"`)
 *
 * Both sections are static content with no state, effects, or browser APIs, so
 * they render on the server — best for this public, SEO- and Core-Web-Vitals-
 * sensitive page (Req 7.3). Animation is intentionally **not** added here: the
 * task scopes this to the truthful content + the anchor, and a Server Component
 * keeps the band cheap and crawlable. (Scroll-reveal motion for the marketing
 * sections, if layered for visual consistency, would be added at page assembly
 * via the existing `MotionSafe` chokepoint, which honors
 * `prefers-reduced-motion`.)
 *
 * ## Headings
 *
 * Each section owns an `<h2>` (the level under the page `<h1>` in the hero),
 * and the individual trust-signal titles are `<h3>` — giving the sequential
 * h1 → h2 → h3 outline the accessibility checks require (design Section 10.3).
 */

/** A single truthful trust signal: a Lucide icon plus short supporting copy. */
interface TrustSignal {
  /** Lucide icon **component** (passed by reference, e.g. `ShieldCheck`). */
  icon: LucideIcon;
  /** Short claim label. */
  title: string;
  /** One-line supporting description of a real MVP capability (Req 4.4). */
  description: string;
}

/**
 * The four sanctioned trust signals (Req 4.4). Every entry maps to a capability
 * that exists in the Phase 1 MVP; none asserts a metric, testimonial, or logo
 * (Req 4.3). The "fast ATS analysis" copy keeps the scoring description to
 * keyword + TF-IDF — never semantic/AI/LLM (Req 5.1).
 */
const TRUST_SIGNALS: ReadonlyArray<TrustSignal> = [
  {
    icon: ShieldCheck,
    title: "Privacy-first",
    description:
      "Your resume and job description stay private — processed for your match only, never sold or shared.",
  },
  {
    icon: FileText,
    title: "PDF & DOCX",
    description:
      "Upload your resume as a PDF or Word document. Both formats are fully supported.",
  },
  {
    icon: Lock,
    title: "Secure handling",
    description:
      "Files are validated on upload, sent over encrypted connections, and held to a strict size limit.",
  },
  {
    icon: Zap,
    title: "Fast ATS analysis",
    description:
      "Get a transparent keyword and TF-IDF match score in seconds — no black box, no waiting.",
  },
];

/**
 * TrustSignals — the band of truthful capability claims below "How it works"
 * (design Section 8.2; Req 4.3, 4.4, 5.1).
 *
 * Renders the four {@link TRUST_SIGNALS} as an icon + title + description grid:
 * one column on mobile, two from 640px, four from 1024px. The section carries
 * an `<h2>`; each claim title is an `<h3>`.
 */
export function TrustSignals({
  className,
}: {
  className?: string;
}): React.JSX.Element {
  return (
    <section
      aria-labelledby="trust-signals-heading"
      className={cn("py-16 md:py-24", className)}
    >
      <div className="mx-auto max-w-7xl px-6">
        <div className="mx-auto max-w-3xl text-center">
          <h2
            id="trust-signals-heading"
            className="text-3xl font-semibold tracking-tight text-text md:text-4xl"
          >
            Private, secure, and fast by design
          </h2>
          <p className="mt-4 text-base text-text-muted">
            Only what we actually do — no inflated numbers, no fake reviews.
          </p>
        </div>

        <ul
          role="list"
          className="mt-12 grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4"
        >
          {TRUST_SIGNALS.map(({ icon: Icon, title, description }) => (
            <li
              key={title}
              className="flex flex-col items-center gap-3 text-center"
            >
              <span
                aria-hidden="true"
                className="flex size-10 shrink-0 items-center justify-center rounded-card border border-border bg-bg-elevated text-brand"
              >
                <Icon className="size-5" />
              </span>
              <h3 className="text-base font-semibold tracking-tight text-text">
                {title}
              </h3>
              <p className="text-sm text-text-muted">{description}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/**
 * About — the `<section id="about">` anchor (design Section 8.2; Req 4.7, 5.1).
 *
 * This is the scroll target for the GlassNav "About" in-page link (`#about`).
 * It gives a concise, truthful description of what MatchLayer is and what it
 * can do **today** in Phase 1: an ATS simulator and resume-match analysis tool
 * that scores a resume against a job description using transparent keyword +
 * TF-IDF matching (Req 4.7, 5.1). The roadmap mention (richer semantic
 * analysis) is stated as explicitly **not** part of this release, so a planned
 * capability is never implied to be available now.
 *
 * `scroll-mt-20` offsets the anchored scroll position so the heading clears the
 * fixed `h-16` GlassNav instead of hiding beneath it.
 */
export function About({
  className,
}: {
  className?: string;
}): React.JSX.Element {
  return (
    <section
      id="about"
      aria-labelledby="about-heading"
      className={cn("scroll-mt-20 py-16 md:py-24", className)}
    >
      <div className="mx-auto max-w-3xl px-6">
        <h2
          id="about-heading"
          className="text-3xl font-semibold tracking-tight text-text md:text-4xl"
        >
          About MatchLayer
        </h2>
        <div className="mt-6 flex flex-col gap-4 text-base leading-relaxed text-text-muted">
          <p>
            MatchLayer is an ATS (Applicant Tracking System) simulator and
            resume-match analysis tool. Upload your resume and a job
            description, and you get a transparent match score that shows how an
            applicant tracking system would read your application against that
            role.
          </p>
          <p>
            Today, scoring is based on keyword matching and TF-IDF
            term-weighting — not a black box. You see which keywords matched,
            which are missing, and how the final score is composed, so every
            number is explainable. Richer semantic analysis is planned, but it
            is not part of this Phase 1 release.
          </p>
        </div>
      </div>
    </section>
  );
}
