"use client";

import * as React from "react";

import { Sparkles } from "lucide-react";

import { LlmResultSection } from "@/components/llm/LlmResultSection";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  MAX_BULLET_CHARS,
  MAX_BULLET_COUNT,
  validateBullets,
} from "@/lib/llm/bullet-validation";
import { useLlmFeature } from "@/lib/llm/use-llm-feature";

import {
  BulletRewriteEnvelopeSchema,
  BulletRewriteListResponseSchema,
  type BulletRewrite,
} from "@matchlayer/shared-types";

/**
 * BulletRewritePanel — the Bullet Rewrites tab of the results-page LLM
 * experience (phase-3-llm-layer Task 11.5; Req 6.x surface, 17.1, 17.7,
 * 17.8, 17.9).
 *
 * ## Input & client-side validation (Req 17.7)
 * The user pastes bullet texts — one per line — into a textarea. Blank
 * lines are separators, not bullets. On submit the derived bullet list
 * is validated by `validateBullets` (the generated
 * `BulletRewriteRequestSchema` plus the mirrored server bounds: count
 * 1..{@link MAX_BULLET_COUNT}, each ≤ {@link MAX_BULLET_CHARS} chars,
 * none empty). A violation renders inline errors naming the violated
 * bound and **no request is sent** — the API is only reached by valid
 * submissions.
 *
 * ## Persisted results & regenerate (Req 17.8, 17.9)
 * The newest stored Bullet_Rewrite shows on load without a POST. The
 * meta line's Regenerate action re-submits the displayed result's own
 * `original` bullet texts through the same streaming flow — the exact
 * bullets that produced the result, available even after a fresh page
 * load.
 *
 * All rewrite strings render as plain React text nodes (Req 17.3).
 */
export function BulletRewritePanel({
  matchId,
}: {
  matchId: string;
}): React.JSX.Element {
  const feature = useLlmFeature({
    matchId,
    feature: "bullet-rewrites",
    parseEnvelope: (payload) => BulletRewriteEnvelopeSchema.parse(payload),
    parseList: (payload) => BulletRewriteListResponseSchema.parse(payload),
  });

  const [rawInput, setRawInput] = React.useState("");
  const [inlineErrors, setInlineErrors] = React.useState<string[]>([]);

  const errorListId = React.useId();
  const hintId = React.useId();

  /**
   * Derive the bullet list from the textarea: one bullet per non-blank
   * line, trimmed (the trimmed text is exactly what is submitted, so the
   * server's byte-for-byte `original` alignment check sees the same
   * strings the user sees echoed back).
   */
  const deriveBullets = (): string[] =>
    rawInput
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const outcome = validateBullets(deriveBullets());
    if (!outcome.ok) {
      // Violated bounds render inline; the request is never sent
      // (Req 17.7).
      setInlineErrors(outcome.errors);
      return;
    }
    setInlineErrors([]);
    feature.generate({ bullets: outcome.bullets });
  };

  /** Regenerate = re-run the displayed result's own original bullets. */
  const handleRegenerate = (): void => {
    const displayed = feature.displayed;
    if (displayed === null) {
      return;
    }
    setInlineErrors([]);
    feature.generate({
      bullets: displayed.result.entries.map((entry) => entry.original),
    });
  };

  const busy =
    feature.state.status === "connecting" ||
    feature.state.status === "streaming";
  const hasErrors = inlineErrors.length > 0;

  return (
    <div className="space-y-6">
      <form onSubmit={handleSubmit} noValidate className="space-y-3">
        <Label htmlFor={`bullets-${errorListId}`}>
          Resume bullets to rewrite
        </Label>
        <Textarea
          id={`bullets-${errorListId}`}
          value={rawInput}
          onChange={(event) => setRawInput(event.target.value)}
          placeholder={`Paste up to ${MAX_BULLET_COUNT} resume bullets — one per line.`}
          rows={5}
          aria-invalid={hasErrors || undefined}
          aria-describedby={hasErrors ? errorListId : hintId}
          disabled={busy}
          className="min-h-28"
        />
        <p id={hintId} className="text-xs text-text-subtle">
          One bullet per line · at most {MAX_BULLET_COUNT} bullets ·{" "}
          {MAX_BULLET_CHARS} characters each.
        </p>
        {/* Inline validation errors, announced via aria-live (design.md). */}
        {hasErrors && (
          <ul
            id={errorListId}
            role="alert"
            className="list-disc space-y-1 pl-5 text-sm text-danger"
          >
            {inlineErrors.map((error, index) => (
              <li key={index}>{error}</li>
            ))}
          </ul>
        )}
        <Button type="submit" disabled={busy}>
          <Sparkles aria-hidden="true" />
          Rewrite bullets
        </Button>
      </form>

      <LlmResultSection
        state={feature.state}
        displayed={feature.displayed}
        persistedPending={feature.persistedPending}
        onGenerate={handleRegenerate}
        onRetry={feature.retry}
        streamLabel="Bullet rewrites"
        generateLabel="Rewrite bullets"
        emptyDescription=""
        hideEmptyCta
        renderResult={(envelope) => (
          <BulletRewriteView rewrite={envelope.result} />
        )}
      />
    </div>
  );
}

/** Render one validated BulletRewrite: original → alternatives + rationale. */
function BulletRewriteView({
  rewrite,
}: {
  rewrite: BulletRewrite;
}): React.JSX.Element {
  return (
    <div className="space-y-6">
      {rewrite.entries.map((entry, index) => (
        <section
          key={index}
          className="space-y-3 rounded-card border border-border bg-bg-elevated p-4"
        >
          <p className="text-sm text-text-muted">
            <span className="font-semibold text-text">Original: </span>
            {entry.original}
          </p>
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-text">
            {entry.alternatives.map((alternative, altIndex) => (
              <li key={altIndex}>{alternative}</li>
            ))}
          </ul>
          <p className="text-xs text-text-subtle">{entry.rationale}</p>
        </section>
      ))}
    </div>
  );
}
