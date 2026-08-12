"use client";

import * as React from "react";

import { LlmResultSection } from "@/components/llm/LlmResultSection";
import { useLlmFeature } from "@/lib/llm/use-llm-feature";

import {
  CoachingReportEnvelopeSchema,
  CoachingReportListResponseSchema,
  type CoachingReport,
} from "@matchlayer/shared-types";

/**
 * CoachPanel — the Resume_Coach tab of the results-page LLM experience
 * (phase-3-llm-layer Task 11.5; Req 5.x surface, 17.1, 17.8, 17.9).
 *
 * On load a single `GET .../coaching-reports?limit=1` shows the newest
 * persisted Coaching_Report without triggering generation (Req 17.8);
 * generation is always the **explicit** Generate / Regenerate action,
 * which streams `POST .../coaching-reports?stream=true` through the
 * shared `useLlmFeature` flow (Req 17.9). The backend's persisted-result
 * reuse (Req 5.4) means a regenerate under an unchanged prompt version
 * and model returns the stored report without a provider call — the UI
 * simply displays whatever envelope the terminal event carries.
 *
 * Every report string renders as a plain React text node (Req 17.3).
 */
export function CoachPanel({
  matchId,
}: {
  matchId: string;
}): React.JSX.Element {
  const feature = useLlmFeature({
    matchId,
    feature: "coaching-reports",
    parseEnvelope: (payload) => CoachingReportEnvelopeSchema.parse(payload),
    parseList: (payload) => CoachingReportListResponseSchema.parse(payload),
  });

  return (
    <LlmResultSection
      state={feature.state}
      displayed={feature.displayed}
      persistedPending={feature.persistedPending}
      onGenerate={() => feature.generate()}
      onRetry={feature.retry}
      streamLabel="Coaching report"
      generateLabel="Generate coaching report"
      emptyDescription="Get an AI coaching report for this match: a summary of how your resume lines up, your strengths, the gaps, and a prioritized list of improvements."
      renderResult={(envelope) => (
        <CoachingReportView report={envelope.result} />
      )}
    />
  );
}

/** Render one validated CoachingReport (summary, strengths, gaps, improvements). */
function CoachingReportView({
  report,
}: {
  report: CoachingReport;
}): React.JSX.Element {
  return (
    <div className="space-y-6">
      <p className="text-sm leading-relaxed text-text">{report.summary}</p>

      {report.strengths.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold tracking-tight text-text">
            Strengths
          </h4>
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-text-muted">
            {report.strengths.map((strength, index) => (
              <li key={index}>{strength}</li>
            ))}
          </ul>
        </section>
      )}

      {report.gaps.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-sm font-semibold tracking-tight text-text">
            Gaps
          </h4>
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-text-muted">
            {report.gaps.map((gap, index) => (
              <li key={index}>{gap}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-2">
        <h4 className="text-sm font-semibold tracking-tight text-text">
          Improvements
        </h4>
        {/* Improvements arrive in descending priority order (schema-enforced);
            an ordered list preserves and communicates that ranking. */}
        <ol className="list-decimal space-y-1.5 pl-5 text-sm text-text-muted">
          {report.improvements.map((improvement, index) => (
            <li key={index}>{improvement.action}</li>
          ))}
        </ol>
      </section>
    </div>
  );
}
