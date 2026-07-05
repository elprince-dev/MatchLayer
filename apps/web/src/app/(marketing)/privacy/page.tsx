import type { Metadata } from "next";
import * as React from "react";

import { MarketingShell } from "@/components/marketing/marketing-shell";
import { buildMarketingMetadata } from "@/lib/seo";

/**
 * `/privacy` — a Public, indexable marketing page (design → New public pages;
 * Req 1.4, 1.5, 7.1–7.5). Publishing a privacy policy from Phase 1 is required
 * because MatchLayer collects PII (`security.md` → Privacy & compliance).
 *
 * Server Component; metadata via the shared `buildMarketingMetadata` builder
 * (Metadata API only). Unique title/description, self-referential canonical for
 * `/privacy`, branded OG image, and no `robots` directive (stays indexable).
 *
 * This is a minimal-but-real policy. It intentionally mirrors the data-handling
 * commitments already enforced in the codebase (PII never logged, resumes
 * stored under opaque keys, per-account isolation, deletion on request) rather
 * than boilerplate. It is not legal advice; a lawyer-reviewed version should
 * replace it before a public launch.
 */
export const metadata: Metadata = buildMarketingMetadata({
  path: "/privacy",
  title: "Privacy Policy — MatchLayer",
  description:
    "How MatchLayer collects, stores, and protects your resume and job-description data — kept private, never indexed, never shared across accounts.",
});

export default function PrivacyPage(): React.JSX.Element {
  return (
    <MarketingShell>
      <article>
        <h1 className="text-4xl font-semibold tracking-tight text-text">
          Privacy Policy
        </h1>
        <p className="mt-4 text-sm text-text-subtle">Last updated: July 2026</p>

        <p className="mt-6 text-text-muted">
          This policy explains what MatchLayer collects, why, and how we protect
          it. It is written to match how the product actually behaves. It is not
          legal advice.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          What we collect
        </h2>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-text-muted">
          <li>
            <strong className="text-text">Account data:</strong> your email
            address and a securely hashed password.
          </li>
          <li>
            <strong className="text-text">Resume content:</strong> the files you
            upload and the text extracted from them.
          </li>
          <li>
            <strong className="text-text">Job descriptions:</strong> the text
            you paste in to run a match.
          </li>
          <li>
            <strong className="text-text">Operational data:</strong> match
            scores and minimal request logs that never include your resume text
            or personal details.
          </li>
        </ul>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          How we protect it
        </h2>
        <ul className="mt-4 list-disc space-y-2 pl-6 text-text-muted">
          <li>
            Resume and job-description content is treated as restricted data:
            encrypted at rest, access-controlled, and never written to logs.
          </li>
          <li>
            Uploaded files are stored under opaque identifiers — never under
            your original filename.
          </li>
          <li>
            Your data is isolated to your account and is never shared across
            accounts or used to train any model.
          </li>
          <li>
            Authenticated pages that display your data are never indexed by
            search engines.
          </li>
        </ul>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          Retention and deletion
        </h2>
        <p className="mt-4 text-text-muted">
          You can delete a resume or match at any time. When you delete your
          account, the associated resume files and records are removed. We keep
          security-relevant audit records (such as sign-in events) for a limited
          period to protect the service.
        </p>

        <h2 className="mt-12 text-2xl font-semibold tracking-tight text-text">
          Contact
        </h2>
        <p className="mt-4 text-text-muted">
          Questions about your data can be sent to the address published on our{" "}
          <a
            href="/about"
            className="rounded-md text-text underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
          >
            about page
          </a>
          .
        </p>
      </article>
    </MarketingShell>
  );
}
