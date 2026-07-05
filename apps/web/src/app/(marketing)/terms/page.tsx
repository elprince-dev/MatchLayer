import type { Metadata } from "next";
import * as React from "react";

import { MarketingShell } from "@/components/marketing/marketing-shell";
import { buildMarketingMetadata } from "@/lib/seo";

/**
 * `/terms` — a Public, indexable marketing page (design → New public pages;
 * Req 1.4, 1.5, 7.1–7.5). Publishing Terms of Service from Phase 1 is required
 * alongside the privacy policy (`security.md` → Privacy & compliance).
 *
 * Server Component; metadata via the shared `buildMarketingMetadata` builder
 * (Metadata API only). Unique title/description, self-referential canonical for
 * `/terms`, branded OG image, no `robots` directive (stays indexable).
 *
 * Minimal-but-real terms reflecting an early-stage, no-warranty product. Not
 * legal advice; replace with a lawyer-reviewed version before a public launch.
 */
export const metadata: Metadata = buildMarketingMetadata({
  path: "/terms",
  title: "Terms of Service — MatchLayer",
  description:
    "The terms for using MatchLayer: acceptable use, ownership of your uploaded content, and the no-warranty basis of this early-stage service.",
});

export default function TermsPage(): React.JSX.Element {
  return (
    <MarketingShell>
      <article>
        <h1 className="text-4xl font-semibold tracking-tight text-text">
          Terms of Service
        </h1>
        <p className="mt-4 text-sm text-text-subtle">Last updated: July 2026</p>

        <p className="mt-6 text-text-muted">
          By using MatchLayer you agree to these terms. They are written for an
          early-stage product and will evolve. This is not legal advice.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          Using the service
        </h2>
        <p className="mt-4 text-text-muted">
          You may use MatchLayer to analyze resumes and job descriptions that
          you own or are authorized to use. Do not upload content that infringes
          others&rsquo; rights, and do not attempt to disrupt, overload, or
          reverse-engineer the service.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          Your content
        </h2>
        <p className="mt-4 text-text-muted">
          You keep ownership of everything you upload. You grant MatchLayer only
          the limited permission needed to store and process your content to
          provide the analysis you requested. We do not sell your content or use
          it to train models. See the{" "}
          <a
            href="/privacy"
            className="rounded-md text-text underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          >
            privacy policy
          </a>{" "}
          for how it is handled.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          No warranty
        </h2>
        <p className="mt-4 text-text-muted">
          MatchLayer is provided &ldquo;as is.&rdquo; Match scores are an
          informational simulation of how an applicant tracking system might
          read your resume; they are not a guarantee of any hiring outcome. To
          the extent permitted by law, we disclaim warranties and are not liable
          for decisions made based on the results.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          Changes
        </h2>
        <p className="mt-4 text-text-muted">
          We may update these terms as the product grows. Continued use after an
          update means you accept the revised terms.
        </p>
      </article>
    </MarketingShell>
  );
}
