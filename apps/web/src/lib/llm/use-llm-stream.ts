"use client";

/**
 * React hook driving one LLM feature streaming request
 * (phase-3-llm-layer Task 11.1; Requirements 11.1, 17.2, 17.10, 17.11).
 *
 * Sits on top of the transport layer in `sse.ts`: it opens the
 * authenticated `POST ...?stream=true` request, accumulates `delta`
 * fragments into a raw buffer (rendered progressively via
 * `progressive-text.ts`), and resolves **exactly one** terminal outcome
 * per stream:
 *
 * - `complete`  — the schema-validated LLM_Result envelope. It *replaces*
 *   the progressive rendering; progressive text is never the final
 *   result (Req 17.2).
 * - `degraded`  — the Fallback_Response envelope (`is_fallback: true`);
 *   progressive content is discarded (Req 17.10).
 * - `error`     — an RFC 7807 problem, either from the `error` terminal
 *   event or from a pre-stream gate rejection (401/404/422/429/503 —
 *   the backend evaluates every gate before the stream opens, Req 11.4,
 *   so those arrive as plain JSON responses, never as SSE).
 * - `interrupted` — the connection closed without delivering a terminal
 *   event (Req 17.11). The UI shows an interrupted state with a retry
 *   action; retry = calling `start` again.
 *
 * Purity split (deliberate, for Task 11.2's property test): the state
 * derivation — "given the SSE events that arrived and whether a terminal
 * was among them, what is the final outcome?" — lives in the exported
 * pure functions `reduceSseEvent` / `finalizeStream`. The hook itself is
 * a thin effectful shell: fetch, iterate, reduce, publish React state.
 * Property 22 (final state derives only from the terminal event) is a
 * statement about the pure core and is testable without React or a
 * network.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { extractProgressiveText } from "@/lib/llm/progressive-text";
import { readSseStream, requestSseStream, type SseEvent } from "@/lib/llm/sse";

// ---------------------------------------------------------------------------
// Problem (RFC 7807) — tolerant client-side shape
// ---------------------------------------------------------------------------

/**
 * The RFC 7807 error body shape used across the API (`conventions.md`).
 * Coerced tolerantly: a malformed body still yields a renderable problem
 * with safe defaults, so the error UI never crashes on a bad payload.
 */
export interface LlmProblem {
  type: string;
  title: string;
  detail: string;
  status: number;
  request_id: string | null;
}

/**
 * Coerce an unknown (already JSON-parsed) value into an `LlmProblem`,
 * defaulting each missing/mistyped field. `fallbackStatus` seeds the
 * status when the body carries none — the HTTP status for gate
 * rejections, 500 for an `error` SSE event.
 */
function coerceProblem(raw: unknown, fallbackStatus: number): LlmProblem {
  const obj =
    raw !== null && typeof raw === "object"
      ? (raw as Record<string, unknown>)
      : {};
  return {
    type: typeof obj.type === "string" ? obj.type : "about:blank",
    title: typeof obj.title === "string" ? obj.title : "Request failed",
    detail:
      typeof obj.detail === "string"
        ? obj.detail
        : "Something went wrong. Please try again.",
    status: typeof obj.status === "number" ? obj.status : fallbackStatus,
    request_id: typeof obj.request_id === "string" ? obj.request_id : null,
  };
}

// ---------------------------------------------------------------------------
// Pure stream-state derivation (Task 11.2's property-test surface)
// ---------------------------------------------------------------------------

/**
 * A terminal recorded while reducing the event sequence. `complete` /
 * `degraded` carry the raw JSON-parsed payload (`unknown` — envelope
 * validation is the hook's concern, via the caller-supplied
 * `parseEnvelope`); `error` carries the coerced RFC 7807 problem.
 */
export type LlmStreamTerminal =
  | { kind: "complete"; payload: unknown }
  | { kind: "degraded"; payload: unknown }
  | { kind: "error"; problem: LlmProblem };

/**
 * Accumulator over one stream's SSE events: the raw delta buffer (a
 * partial JSON document, displayed via `extractProgressiveText`) and the
 * first — and only — terminal event observed.
 */
export interface LlmStreamReduction {
  /** Concatenated `delta` text fragments, in arrival order. */
  rawText: string;
  /** The recorded terminal, or `null` while none has arrived. */
  terminal: LlmStreamTerminal | null;
}

/** The reduction every stream starts from. */
export const initialStreamReduction: LlmStreamReduction = {
  rawText: "",
  terminal: null,
};

/** JSON-parse an SSE data payload; `undefined` when unparseable. */
function parseJson(data: string): unknown {
  try {
    return JSON.parse(data) as unknown;
  } catch {
    return undefined;
  }
}

/**
 * Fold one SSE event into the reduction. Pure — returns the input state
 * unchanged (same reference) for events that don't affect it.
 *
 * Rules (the client half of the SSE contract in design.md):
 * - Anything after a recorded terminal is ignored — the backend emits
 *   nothing after the terminal (Req 11.3), and a client must not let a
 *   stray frame overwrite a resolved outcome.
 * - `delta`: append `{"text": ...}` to the raw buffer. A malformed delta
 *   payload is dropped (display-only content; losing one fragment only
 *   degrades the preview, never correctness).
 * - `complete` / `degraded`: record the terminal with the parsed
 *   payload. A terminal whose payload is not valid JSON is dropped — the
 *   stream then finalizes as `interrupted`, which is truthful: no usable
 *   terminal arrived.
 * - `error`: record the terminal with the coerced RFC 7807 problem
 *   (coercion is total, so a malformed error body still terminates the
 *   stream with a renderable problem).
 * - Unknown event types: ignored (forward compatibility).
 */
export function reduceSseEvent(
  state: LlmStreamReduction,
  event: SseEvent,
): LlmStreamReduction {
  if (state.terminal !== null) {
    return state;
  }
  switch (event.event) {
    case "delta": {
      const payload = parseJson(event.data);
      if (
        payload !== null &&
        typeof payload === "object" &&
        "text" in payload &&
        typeof (payload as { text: unknown }).text === "string"
      ) {
        return {
          ...state,
          rawText: state.rawText + (payload as { text: string }).text,
        };
      }
      return state;
    }
    case "complete":
    case "degraded": {
      const payload = parseJson(event.data);
      if (payload === undefined) {
        return state;
      }
      return { ...state, terminal: { kind: event.event, payload } };
    }
    case "error": {
      return {
        ...state,
        terminal: {
          kind: "error",
          problem: coerceProblem(parseJson(event.data), 500),
        },
      };
    }
    default:
      return state;
  }
}

/**
 * The resolved outcome of a closed stream. Exactly one of the four —
 * there is no fifth outcome, mirroring the backend's "no fourth outcome"
 * principle.
 */
export type LlmStreamOutcome =
  | { kind: "complete"; payload: unknown }
  | { kind: "degraded"; payload: unknown }
  | { kind: "error"; problem: LlmProblem }
  | { kind: "interrupted" };

/**
 * Resolve the final outcome once the connection has closed: the recorded
 * terminal when one arrived, else `interrupted` (Req 17.11). Together
 * with `reduceSseEvent` this is Property 22's subject: the outcome
 * derives only from the terminal event — accumulated delta text never
 * influences it.
 */
export function finalizeStream(state: LlmStreamReduction): LlmStreamOutcome {
  return state.terminal ?? { kind: "interrupted" };
}

// ---------------------------------------------------------------------------
// Hook state
// ---------------------------------------------------------------------------

/**
 * The UI-facing state machine (Req 17.2, 17.6, 17.10, 17.11):
 *
 * - `idle`        — no request started (or reset/cancelled).
 * - `connecting`  — request in flight, no content yet → skeleton (17.6).
 * - `streaming`   — deltas arriving; `progressiveText` is the tolerant
 *                   display extraction over the partial JSON buffer.
 * - `complete`    — validated envelope; replaces the progressive view.
 * - `degraded`    — fallback envelope; progressive content discarded.
 * - `error`       — RFC 7807 problem (terminal event or gate rejection).
 * - `interrupted` — closed without a terminal; offer retry.
 */
export type LlmStreamState<TEnvelope> =
  | { status: "idle" }
  | { status: "connecting" }
  | { status: "streaming"; progressiveText: string }
  | { status: "complete"; envelope: TEnvelope }
  | { status: "degraded"; envelope: TEnvelope }
  | { status: "error"; problem: LlmProblem }
  | { status: "interrupted" };

export interface UseLlmStreamOptions<TEnvelope> {
  /**
   * Parse/validate a terminal payload into the feature's envelope —
   * callers pass the generated Zod schema's `parse` (e.g.
   * `CoachingReportEnvelopeSchema.parse`), per the conventions rule that
   * client-side Zod catches contract drift early. A throw is treated as
   * "no usable terminal arrived" → `interrupted` (retryable), never a
   * crash.
   */
  parseEnvelope: (payload: unknown) => TEnvelope;
}

export interface UseLlmStreamResult<TEnvelope> {
  state: LlmStreamState<TEnvelope>;
  /**
   * Open the streaming request. `path` is the feature endpoint without
   * the negotiation parameter (`/api/v1/matches/{id}/coaching-reports`);
   * the hook appends `stream=true` (design D3). `body`, when given, is
   * JSON-serialized — a string body is re-usable across `apiFetch`'s
   * silent 401 retry. Starting while a stream is active aborts the old
   * stream first (the server aborts its provider call on disconnect).
   */
  start: (path: string, body?: unknown) => Promise<void>;
  /** Abort any in-flight stream and return to `idle`. */
  cancel: () => void;
  /** Return to `idle` without touching an in-flight stream's outcome. */
  reset: () => void;
}

/** Append the `stream=true` negotiation parameter (design D3). */
function withStreamParam(path: string): string {
  return path.includes("?") ? `${path}&stream=true` : `${path}?stream=true`;
}

/** Read a non-2xx response's RFC 7807 body, tolerating a non-JSON body. */
async function readProblem(response: Response): Promise<LlmProblem> {
  let raw: unknown = null;
  try {
    raw = await response.json();
  } catch {
    // Non-JSON error body (proxy page, empty body) — defaults apply.
  }
  return coerceProblem(raw, response.status);
}

// ---------------------------------------------------------------------------
// useLlmStream
// ---------------------------------------------------------------------------

/**
 * Drive one LLM feature streaming request and expose its lifecycle as
 * React state. See the module doc for the full contract.
 *
 * Concurrency: only the latest `start` publishes state. Each run takes a
 * monotonically increasing id; `cancel`, `reset`, unmount, and a newer
 * `start` all invalidate older runs, whose late async completions then
 * publish nothing. The stale run's fetch is also aborted so the server
 * sees a disconnect and stops its provider call (Req 11.7).
 */
export function useLlmStream<TEnvelope>(
  options: UseLlmStreamOptions<TEnvelope>,
): UseLlmStreamResult<TEnvelope> {
  const [state, setState] = useState<LlmStreamState<TEnvelope>>({
    status: "idle",
  });

  const controllerRef = useRef<AbortController | null>(null);
  const runIdRef = useRef(0);
  // Keep the latest parser without making `start` identity-unstable when
  // callers pass an inline function. Synced in an effect (not during
  // render) per the React refs rule; `start` only runs from event
  // handlers, which always execute after the effect has synced.
  const parseEnvelopeRef = useRef(options.parseEnvelope);
  useEffect(() => {
    parseEnvelopeRef.current = options.parseEnvelope;
  }, [options.parseEnvelope]);

  /** Invalidate any in-flight run and abort its connection. */
  const invalidate = useCallback(() => {
    runIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  // Unmount: tear down the connection; publish nothing.
  useEffect(() => invalidate, [invalidate]);

  const start = useCallback(
    async (path: string, body?: unknown): Promise<void> => {
      invalidate();
      const runId = runIdRef.current;
      const controller = new AbortController();
      controllerRef.current = controller;

      const publish = (next: LlmStreamState<TEnvelope>): void => {
        if (runIdRef.current === runId) {
          setState(next);
        }
      };

      publish({ status: "connecting" });

      let response: Response;
      try {
        response = await requestSseStream(withStreamParam(path), {
          method: "POST",
          headers:
            body === undefined
              ? undefined
              : { "Content-Type": "application/json" },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });
      } catch {
        // Network failure or abort before any response. An abort came
        // from invalidate() (newer start / cancel / unmount), in which
        // case publish() is already a no-op — so this only ever renders
        // for a genuine connection failure.
        publish({ status: "interrupted" });
        return;
      }

      // Gate rejections (429 quota, 503 spend, 404 ownership, 422
      // validation, unrecovered 401) arrive as plain RFC 7807 JSON —
      // the backend never opens a stream for them (Req 11.4).
      if (!response.ok) {
        publish({ status: "error", problem: await readProblem(response) });
        return;
      }

      if (response.body === null) {
        publish({ status: "interrupted" });
        return;
      }

      let reduction = initialStreamReduction;
      try {
        for await (const event of readSseStream(response.body)) {
          reduction = reduceSseEvent(reduction, event);
          if (reduction.terminal !== null) {
            // Terminal resolved — stop reading. readSseStream's finally
            // block cancels the reader, releasing the connection.
            break;
          }
          if (event.event === "delta") {
            publish({
              status: "streaming",
              progressiveText: extractProgressiveText(reduction.rawText),
            });
          }
        }
      } catch {
        // The connection dropped (or was aborted) mid-stream. No
        // terminal arrived — the partial content is never presented as
        // final (Req 17.11).
        publish({ status: "interrupted" });
        return;
      }

      const outcome = finalizeStream(reduction);
      switch (outcome.kind) {
        case "complete":
        case "degraded": {
          let envelope: TEnvelope;
          try {
            envelope = parseEnvelopeRef.current(outcome.payload);
          } catch {
            // The terminal payload failed envelope validation (contract
            // drift). No usable terminal → interrupted, retryable.
            publish({ status: "interrupted" });
            return;
          }
          publish({ status: outcome.kind, envelope });
          return;
        }
        case "error":
          publish({ status: "error", problem: outcome.problem });
          return;
        case "interrupted":
          publish({ status: "interrupted" });
          return;
      }
    },
    [invalidate],
  );

  const cancel = useCallback(() => {
    invalidate();
    setState({ status: "idle" });
  }, [invalidate]);

  const reset = useCallback(() => {
    setState({ status: "idle" });
  }, []);

  return { state, start, cancel, reset };
}
