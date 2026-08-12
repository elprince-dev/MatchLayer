"use client";

import * as React from "react";

import { RefreshCw, Sparkles } from "lucide-react";

import { FallbackBadge } from "@/components/llm/FallbackBadge";
import { LlmErrorState } from "@/components/llm/LlmErrorStates";
import { StreamingText } from "@/components/llm/StreamingText";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { LlmEnvelopeLike } from "@/lib/llm/use-llm-feature";
import type { LlmProblem, LlmStreamState } from "@/lib/llm/use-llm-stream";
import { cn } from "@/lib/utils";

/**
 * LlmResultSection — the shared state chrome every LLM feature panel
 * composes (phase-3-llm-layer Task 11.5; Req 17.2, 17.4, 17.5, 17.6,
 * 17.8, 17.9, 17.10, 17.11).
 *
 * The three panels (Coach / Bullet Rewrites / Interview Prep) differ
 * only in their result rendering and (for the rewriter) an input form;
 * everything else — the skeleton before the first delta, the
 * progressive `StreamingText` view, the terminal replacement, the
 * fallback badge, the version/timestamp meta line with the Regenerate
 * action, and the 429/503/interrupted error surfaces — is identical, so
 * it lives here once.
 *
 * ## State precedence (top to bottom)
 * 1. An `error` / `interrupted` stream outcome renders its alert first
 *    (Req 17.5/17.10/17.11) — the previously displayed persisted result,
 *    when one exists, stays visible below it (partial progressive
 *    content was already discarded by the hook; it is never shown).
 * 2. `connecting` / `streaming` render the skeleton / progressive text
 *    (Req 17.2, 17.6) *instead of* the displayed result — the stream is
 *    producing that result's replacement.
 * 3. A displayed envelope renders the meta line (badge + version +
 *    created_at + Regenerate, Req 17.4/17.9) and the feature's result.
 * 4. No envelope: the persisted-load skeleton while the GET is pending,
 *    else the empty state with the explicit generate action (never an
 *    automatic POST, Req 17.8).
 *
 * All model-produced strings are rendered by the panels as plain React
 * text nodes — no `dangerouslySetInnerHTML` anywhere (Req 17.3).
 */
export interface LlmResultSectionProps<TEnvelope extends LlmEnvelopeLike> {
  /** The stream state from `useLlmFeature`. */
  state: LlmStreamState<TEnvelope>;
  /** The envelope to display when no stream is active (or `null`). */
  displayed: TEnvelope | null;
  /** True while the on-load persisted-result GET is pending. */
  persistedPending: boolean;
  /** Explicit generate/regenerate action (Req 17.8/17.9). */
  onGenerate: () => void;
  /** Retry the last request (Req 17.11). */
  onRetry: () => void;
  /** Render the feature's validated result content. */
  renderResult: (envelope: TEnvelope) => React.ReactNode;
  /** Accessible label for the streamed region. */
  streamLabel: string;
  /** Empty-state CTA label, e.g. "Generate coaching report". */
  generateLabel: string;
  /** Empty-state description of what the feature produces. */
  emptyDescription: string;
  /**
   * Hide the empty-state CTA — the Bullet Rewrites panel renders its own
   * submit form instead (generation needs a request body).
   */
  hideEmptyCta?: boolean;
  /** Composition hook. */
  className?: string;
}

/**
 * Map a stream `error` outcome to the right surface (Req 17.5, 17.10):
 * 429 → the quota state, 503 → the unavailable state, anything else →
 * a generic inline alert showing the RFC 7807 user-safe `detail`.
 */
function ProblemAlert({ problem }: { problem: LlmProblem }): React.JSX.Element {
  if (problem.status === 429) {
    return <LlmErrorState kind="quota" detail={problem.detail} />;
  }
  if (problem.status === 503) {
    return <LlmErrorState kind="unavailable" detail={problem.detail} />;
  }
  return (
    <div
      role="alert"
      className="rounded-card border border-border bg-bg-elevated p-4"
    >
      <p className="text-sm font-semibold tracking-tight text-text">
        {problem.title}
      </p>
      <p className="mt-1 text-sm text-text-muted">{problem.detail}</p>
    </div>
  );
}

/** Format an envelope `created_at` for the meta line. */
function formatCreatedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

/**
 * The meta line above every displayed result: the fallback badge
 * (exactly when `is_fallback`, Req 17.4), the prompt version and
 * creation time (Req 17.9 — null for fallbacks, which are not
 * persisted), and the Regenerate action.
 */
function ResultMeta({
  envelope,
  onRegenerate,
}: {
  envelope: LlmEnvelopeLike;
  onRegenerate: () => void;
}): React.JSX.Element {
  const version = envelope.prompt_template_version ?? null;
  const createdAt = envelope.created_at ?? null;

  return (
    <div className="flex flex-wrap items-center gap-3">
      <FallbackBadge isFallback={envelope.is_fallback} />
      {(version !== null || createdAt !== null) && (
        <p className="font-mono text-xs text-text-subtle">
          {version !== null && <>Prompt v{version}</>}
          {version !== null && createdAt !== null && <> · </>}
          {createdAt !== null && (
            <time dateTime={createdAt}>{formatCreatedAt(createdAt)}</time>
          )}
        </p>
      )}
      <div className="ml-auto">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onRegenerate}
        >
          <RefreshCw aria-hidden="true" />
          Regenerate
        </Button>
      </div>
    </div>
  );
}

/** Paragraph-shaped placeholder while the persisted-result GET resolves. */
function PersistedSkeleton(): React.JSX.Element {
  return (
    <div aria-hidden="true" className="space-y-2.5 py-1">
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-11/12" />
      <Skeleton className="h-4 w-2/3" />
    </div>
  );
}

export function LlmResultSection<TEnvelope extends LlmEnvelopeLike>({
  state,
  displayed,
  persistedPending,
  onGenerate,
  onRetry,
  renderResult,
  streamLabel,
  generateLabel,
  emptyDescription,
  hideEmptyCta = false,
  className,
}: LlmResultSectionProps<TEnvelope>): React.JSX.Element {
  const streaming =
    state.status === "connecting" || state.status === "streaming";

  let body: React.ReactNode;
  if (streaming) {
    // Skeleton before the first delta, then progressive text (Req 17.2,
    // 17.6). The stream replaces the displayed result, so nothing else
    // renders alongside it.
    body = (
      <StreamingText
        text={state.status === "streaming" ? state.progressiveText : ""}
        streaming
        label={streamLabel}
      />
    );
  } else if (displayed !== null) {
    body = (
      <div className="space-y-4">
        <ResultMeta envelope={displayed} onRegenerate={onGenerate} />
        {renderResult(displayed)}
      </div>
    );
  } else if (persistedPending) {
    body = <PersistedSkeleton />;
  } else if (!hideEmptyCta) {
    // Nothing stored yet: generation is an explicit user action — a page
    // load never POSTs (Req 17.8).
    body = (
      <div className="space-y-4">
        <p className="text-sm text-text-muted">{emptyDescription}</p>
        <Button type="button" onClick={onGenerate}>
          <Sparkles aria-hidden="true" />
          {generateLabel}
        </Button>
      </div>
    );
  } else {
    body = null;
  }

  return (
    <div className={cn("space-y-4", className)}>
      {state.status === "error" && <ProblemAlert problem={state.problem} />}
      {state.status === "interrupted" && (
        <LlmErrorState kind="interrupted" onRetry={onRetry} />
      )}
      {body}
    </div>
  );
}
