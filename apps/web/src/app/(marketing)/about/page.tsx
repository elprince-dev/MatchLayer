import type { Metadata } from "next";
import * as React from "react";

import { MarketingShell } from "@/components/marketing/marketing-shell";
import { buildMarketingMetadata } from "@/lib/seo";

/**
 * `/about` — a Public, indexable marketing page (design → New public pages;
 * Req 1.4, 1.5, 7.1–7.5).
 *
 * Server Component so it can export `metadata`. Metadata flows through the
 * shared `buildMarketingMetadata` builder (Metadata API only — `seo.md`,
 * `conventions.md`): unique title/description (Req 1.4, 1.5), self-referential
 * canonical for `/about`, Open Graph + Twitter card, and the branded default OG
 * image. No `robots` directive, so the page stays indexable (Req 7.5).
 *
 * Honesty (Req 5.1 / `product.md`): the copy describes Phase 1 exactly — an ATS
 * simulator using transparent keyword + TF-IDF matching. It never claims
 * semantic, embeddings-, AI-, or LLM-based scoring.
 */
export const metadata: Metadata = buildMarketingMetadata({
  path: "/about",
  title: "About MatchLayer — transparent ATS resume scoring",
  description:
    "MatchLayer is an ATS simulator that scores your resume against a job using transparent keyword and TF-IDF matching — no black box, no hype.",
});

export default function AboutPage(): React.JSX.Element {
  return (
    <MarketingShell>
      <article className="prose-none">
        <h1 className="text-4xl font-semibold tracking-tight text-text">
          About MatchLayer
        </h1>

        <p className="mt-6 text-lg text-text-muted">
          Real applicant tracking systems are opaque. Candidates rewrite their
          resumes blind, guessing at what a machine will reward. MatchLayer
          makes that process visible.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          What it does today
        </h2>
        <p className="mt-4 text-text-muted">
          You upload a resume and paste a job description. MatchLayer returns a
          match score, the keywords your resume already hits and the ones it is
          missing, a breakdown of how the score is built, and rule-based
          suggestions for closing the gap. The scoring is deliberately
          transparent: it combines text similarity (TF-IDF) with keyword
          coverage. There is no hidden model deciding your fate.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          What it is not
        </h2>
        <p className="mt-4 text-text-muted">
          This release does not use semantic embeddings, AI, or large language
          models to score your resume. Deeper meaning-based matching is on the
          roadmap, and we will say so plainly when it ships — not before.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          How we treat your data
        </h2>
        <p className="mt-4 text-text-muted">
          Your resume and the job descriptions you analyze are private. They are
          never indexed, never shared across accounts, and never used to train a
          model. See our{" "}
          <a
            href="/privacy"
            className="rounded-md text-text underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          >
            privacy policy
          </a>{" "}
          for the details.
        </p>
      </article>
    </MarketingShell>
  );
}
