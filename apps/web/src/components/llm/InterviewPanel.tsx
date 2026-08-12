"use client";

import * as React from "react";

import { LlmResultSection } from "@/components/llm/LlmResultSection";
import { useLlmFeature } from "@/lib/llm/use-llm-feature";

import {
  InterviewQuestionSetEnvelopeSchema,
  InterviewQuestionSetListResponseSchema,
  type InterviewQuestionCategory,
  type InterviewQuestionSet,
} from "@matchlayer/shared-types";

/**
 * InterviewPanel — the Interview Prep tab of the results-page LLM
 * experience (phase-3-llm-layer Task 11.5; Req 7.x surface, 17.1, 17.8,
 * 17.9).
 *
 * On load a single `GET .../interview-question-sets?limit=1` shows the
 * newest persisted Interview_Question_Set without triggering generation
 * (Req 17.8); generation is the explicit Generate / Regenerate action
 * streaming `POST .../interview-question-sets?stream=true` (Req 17.9).
 *
 * Every question string renders as a plain React text node (Req 17.3).
 */
export function InterviewPanel({
  matchId,
}: {
  matchId: string;
}): React.JSX.Element {
  const feature = useLlmFeature({
    matchId,
    feature: "interview-question-sets",
    parseEnvelope: (payload) =>
      InterviewQuestionSetEnvelopeSchema.parse(payload),
    parseList: (payload) =>
      InterviewQuestionSetListResponseSchema.parse(payload),
  });

  return (
    <LlmResultSection
      state={feature.state}
      displayed={feature.displayed}
      persistedPending={feature.persistedPending}
      onGenerate={() => feature.generate()}
      onRetry={feature.retry}
      streamLabel="Interview questions"
      generateLabel="Generate interview questions"
      emptyDescription="Get likely interview questions for this job — technical, behavioral, and questions probing the gaps between your resume and the role — each with why it may come up."
      renderResult={(envelope) => (
        <InterviewQuestionSetView questionSet={envelope.result} />
      )}
    />
  );
}

/** Human-readable labels for the closed category enum. */
const CATEGORY_LABELS: Record<InterviewQuestionCategory, string> = {
  technical: "Technical",
  behavioral: "Behavioral",
  "experience-gap": "Experience gap",
};

/** Render one validated InterviewQuestionSet as a question list. */
function InterviewQuestionSetView({
  questionSet,
}: {
  questionSet: InterviewQuestionSet;
}): React.JSX.Element {
  return (
    <ul className="space-y-4">
      {questionSet.questions.map((question, index) => (
        <li
          key={index}
          className="space-y-2 rounded-card border border-border bg-bg-elevated p-4"
        >
          <span className="inline-flex items-center rounded-pill border border-border-strong bg-bg px-2.5 py-0.5 text-xs font-medium text-text-muted">
            {CATEGORY_LABELS[question.category]}
          </span>
          <p className="text-sm font-medium leading-relaxed text-text">
            {question.question}
          </p>
          <p className="text-xs text-text-subtle">{question.reason}</p>
        </li>
      ))}
    </ul>
  );
}
