"use client";

/**
 * Shared per-panel LLM feature state
 * (phase-3-llm-layer Task 11.5; Requirements 17.8, 17.9).
 *
 * One instance drives one feature panel on the results page. It owns the
 * two data sources a panel composes:
 *
 * 1. **The persisted-result load (Req 17.8).** A TanStack Query `GET
 *    {path}?limit=1` against the feature's newest-first list endpoint.
 *    The newest stored LLM_Result for the *viewed* Match_Result renders
 *    on load — the GET never triggers generation, and no POST is issued
 *    until the user explicitly acts (the coach's explicit regenerate,
 *    the rewriter's submit).
 *
 * 2. **The streaming request (Req 17.2/17.9/17.10/17.11)** via
 *    `useLlmStream`, started only from `generate()` / `retry()` — both
 *    user-initiated.
 *
 * `displayed` merges the two: a terminal stream envelope (`complete` or
 * `degraded`) wins over the persisted result, so a regenerated result
 * replaces the previous one in place (Req 17.9); until then the
 * persisted envelope is shown. Fallback envelopes are never persisted
 * by the backend, so a `degraded` result naturally disappears on the
 * next page load — exactly the Requirement 9.5 retry story.
 */

import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

import { apiFetch } from "@/lib/api";
import { useLlmStream, type LlmStreamState } from "@/lib/llm/use-llm-stream";

/**
 * The envelope fields every LLM feature response shares
 * (`LLMResultEnvelope[T]`, Req 9.2/17.9) — the minimum the shared panel
 * chrome needs: the fallback marker, and the version/timestamp metadata
 * shown next to every displayed result.
 */
export interface LlmEnvelopeLike {
  is_fallback: boolean;
  prompt_template_version?: number | null | undefined;
  created_at?: string | null | undefined;
}

/** Options for {@link useLlmFeature}. */
export interface UseLlmFeatureOptions<TEnvelope extends LlmEnvelopeLike> {
  /** The viewed Match_Result id from the route. */
  matchId: string;
  /**
   * The sub-resource path segment: `"coaching-reports"`,
   * `"bullet-rewrites"`, or `"interview-question-sets"`.
   */
  feature: string;
  /**
   * Validate a terminal SSE payload into the feature envelope — pass the
   * generated Zod schema's `parse` (contract-drift detection per
   * `conventions.md`).
   */
  parseEnvelope: (payload: unknown) => TEnvelope;
  /**
   * Validate the GET-list response body — pass the generated list
   * schema's `parse`. Only `items` is consumed (`limit=1`, newest
   * first).
   */
  parseList: (payload: unknown) => { items: TEnvelope[] };
}

/** What {@link useLlmFeature} hands a panel. */
export interface UseLlmFeatureResult<TEnvelope extends LlmEnvelopeLike> {
  /** The live streaming state (skeleton / progressive / terminal). */
  state: LlmStreamState<TEnvelope>;
  /**
   * The envelope to show when no stream is active: the latest terminal
   * stream envelope if one resolved, else the newest persisted result,
   * else `null`.
   */
  displayed: TEnvelope | null;
  /** True while the on-load persisted-result GET is still in flight. */
  persistedPending: boolean;
  /**
   * Start a generation request (`POST {path}?stream=true`). The body, if
   * given, is remembered for {@link retry}.
   */
  generate: (body?: unknown) => void;
  /** Re-run the last {@link generate} request (Req 17.11 retry action). */
  retry: () => void;
}

/**
 * Fetch the newest persisted LLM_Result envelope for the feature, or
 * `null` when none exists yet. Any load failure also resolves to `null`
 * — the panel then offers generation instead of blocking the whole
 * results page on a secondary surface.
 */
async function fetchNewest<TEnvelope>(
  path: string,
  parseList: (payload: unknown) => { items: TEnvelope[] },
  signal: AbortSignal,
): Promise<TEnvelope | null> {
  let response: Response;
  try {
    response = await apiFetch(`${path}?limit=1`, { signal });
  } catch {
    return null;
  }
  if (!response.ok) {
    return null;
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  try {
    return parseList(body).items[0] ?? null;
  } catch {
    // Contract drift — treat as "no stored result" rather than crashing.
    return null;
  }
}

/** See the module doc. */
export function useLlmFeature<TEnvelope extends LlmEnvelopeLike>(
  options: UseLlmFeatureOptions<TEnvelope>,
): UseLlmFeatureResult<TEnvelope> {
  const { matchId, feature, parseEnvelope, parseList } = options;
  const path = `/api/v1/matches/${encodeURIComponent(matchId)}/${feature}`;

  const stream = useLlmStream<TEnvelope>({ parseEnvelope });

  // Keep the latest parseList without destabilizing the query function
  // identity when callers pass an inline closure. Synced in an effect
  // (not during render) per the React refs rule — the initial useRef
  // value covers the query's first execution.
  const parseListRef = useRef(parseList);
  useEffect(() => {
    parseListRef.current = parseList;
  }, [parseList]);

  const persisted = useQuery<TEnvelope | null>({
    queryKey: ["llm-result", matchId, feature],
    queryFn: ({ signal }) => fetchNewest(path, parseListRef.current, signal),
    enabled: matchId.length > 0,
    retry: false,
  });

  // The last generate() body, replayed by retry() (Req 17.11).
  const lastBodyRef = useRef<unknown>(undefined);

  // `stream.start` is identity-stable (useCallback inside useLlmStream),
  // so depending on it keeps generate/retry stable across renders.
  const { start } = stream;

  const generate = useCallback(
    (body?: unknown): void => {
      lastBodyRef.current = body;
      void start(path, body);
    },
    [start, path],
  );

  const retry = useCallback((): void => {
    void start(path, lastBodyRef.current);
  }, [start, path]);

  const terminalEnvelope =
    stream.state.status === "complete" || stream.state.status === "degraded"
      ? stream.state.envelope
      : null;

  return {
    state: stream.state,
    displayed: terminalEnvelope ?? persisted.data ?? null,
    persistedPending: persisted.isPending,
    generate,
    retry,
  };
}
