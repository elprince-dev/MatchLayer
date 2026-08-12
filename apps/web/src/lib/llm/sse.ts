/**
 * Fetch + ReadableStream SSE client for the LLM streaming endpoints
 * (phase-3-llm-layer Task 11.1; Requirements 11.1, 17.2).
 *
 * Why not the native `EventSource`: the LLM feature endpoints are
 * `POST /api/v1/matches/{id}/…?stream=true` requests that carry a JSON body
 * and require the `Authorization: Bearer` header (design decision D2).
 * `EventSource` supports neither POST bodies nor custom headers, so this
 * module implements the SSE wire format over `fetch` + `ReadableStream`
 * instead.
 *
 * What this module owns:
 *
 * 1. `SseParser` — an incremental, chunk-boundary-safe parser for the SSE
 *    wire format (WHATWG "Server-sent events" §9.2.6): `event:` /
 *    `data:` fields, comment lines, multi-line data joined with `\n`,
 *    `\r\n` / `\n` / `\r` line terminators, and dispatch on blank line.
 *    Pure and synchronous so it is unit-testable without any network.
 *
 * 2. `readSseStream` — an async generator adapting a `Response.body`
 *    (`ReadableStream<Uint8Array>`) into a sequence of `SseEvent`s via
 *    `TextDecoder` (streaming mode, so multi-byte UTF-8 sequences split
 *    across chunks decode correctly). An event left unterminated when the
 *    connection closes is deliberately **not** dispatched — per the SSE
 *    spec an incomplete event is discarded, which is exactly the signal
 *    `use-llm-stream` needs to derive the "interrupted" state of
 *    Requirement 17.11 (a close without a terminal event).
 *
 * 3. `requestSseStream` — the thin `apiFetch` wrapper that opens the
 *    stream with `Accept: text/event-stream`, inheriting the Bearer
 *    attachment and single silent-refresh-retry behavior of `lib/api.ts`.
 *
 * The event vocabulary this client consumes is defined by the backend in
 * `apps/api/src/matchlayer_api/api/matches/llm/sse.py`: incremental
 * `delta` events (`{"text": ...}`) and exactly one terminal event —
 * `complete` / `degraded` (the LLMResultEnvelope JSON) or `error` (an
 * RFC 7807 body). Interpreting those payloads is `use-llm-stream`'s job;
 * this module is transport only.
 */

import { apiFetch, type ApiFetchInit } from "@/lib/api";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/**
 * One dispatched SSE event: the machine-readable event type (Req 11.2)
 * and the raw data payload (consecutive `data:` lines rejoined with
 * `\n` per the wire format). The payload is *not* parsed here — the
 * backend's payloads are JSON, but decoding them is the consumer's
 * concern so the parser stays a pure transport component.
 */
export interface SseEvent {
  /** Event type from the `event:` field; `"message"` when absent. */
  event: string;
  /** Data payload — `data:` line values joined with `\n`. */
  data: string;
}

// ---------------------------------------------------------------------------
// SseParser — incremental wire-format parser
// ---------------------------------------------------------------------------

/**
 * Incremental SSE wire-format parser. Feed it decoded text chunks in
 * arrival order; it returns the events completed by each chunk.
 *
 * Chunk-boundary safety is the whole reason this is a stateful class:
 * the network layer slices the stream arbitrarily, so a chunk can end
 * mid-line, mid-field, or between the `\r` and `\n` of a CRLF pair. The
 * parser buffers the unterminated tail (including a lone trailing `\r`,
 * which may be the first half of a CRLF) and resumes on the next feed.
 *
 * Spec conformance notes (WHATWG SSE §9.2.6 "Interpreting an event
 * stream"):
 * - Lines starting with `:` are comments and ignored.
 * - A field line without a colon is treated as a field name with an
 *   empty value (so a bare `data` line contributes an empty data line).
 * - Exactly one space after the field colon is stripped from the value.
 * - A blank line dispatches the pending event iff at least one `data:`
 *   line was buffered; an eventless blank line just resets the type.
 * - `id:` and `retry:` fields are accepted and ignored — the LLM
 *   endpoints never emit them and this client does not reconnect.
 */
export class SseParser {
  /** Unterminated tail of the input — a partial line held across feeds. */
  private buffer = "";
  /** `data:` line values buffered for the event under construction. */
  private dataLines: string[] = [];
  /** `event:` field value for the event under construction. */
  private eventType = "";

  /**
   * Consume one decoded text chunk and return every event the chunk
   * completed, in wire order.
   */
  feed(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];

    let start = 0;
    let i = 0;
    while (i < this.buffer.length) {
      const ch = this.buffer.charAt(i);
      if (ch !== "\n" && ch !== "\r") {
        i += 1;
        continue;
      }
      if (ch === "\r" && i === this.buffer.length - 1) {
        // A lone trailing `\r` may be the first half of a CRLF split
        // across chunks — hold the line until more input arrives.
        break;
      }
      const line = this.buffer.slice(start, i);
      // Consume a CRLF pair as a single terminator.
      if (ch === "\r" && this.buffer.charAt(i + 1) === "\n") {
        i += 1;
      }
      i += 1;
      start = i;

      const dispatched = this.processLine(line);
      if (dispatched !== null) {
        events.push(dispatched);
      }
    }

    this.buffer = this.buffer.slice(start);
    return events;
  }

  /**
   * Interpret one complete line. Returns the dispatched event when the
   * line is the blank-line terminator of a pending event, else `null`.
   */
  private processLine(line: string): SseEvent | null {
    if (line === "") {
      return this.dispatch();
    }
    if (line.startsWith(":")) {
      return null; // comment line
    }

    const colon = line.indexOf(":");
    let field: string;
    let value: string;
    if (colon === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
    }

    switch (field) {
      case "event":
        this.eventType = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      default:
        // `id`, `retry`, and unknown fields are ignored (see class doc).
        break;
    }
    return null;
  }

  /**
   * Blank-line dispatch. Per the spec, an event with an empty data
   * buffer (no `data:` lines at all) is not dispatched — the event type
   * buffer is still reset so a stray `event:` line cannot leak into a
   * later event.
   */
  private dispatch(): SseEvent | null {
    if (this.dataLines.length === 0) {
      this.eventType = "";
      return null;
    }
    const event: SseEvent = {
      event: this.eventType === "" ? "message" : this.eventType,
      data: this.dataLines.join("\n"),
    };
    this.dataLines = [];
    this.eventType = "";
    return event;
  }
}

// ---------------------------------------------------------------------------
// readSseStream — Response.body → AsyncGenerator<SseEvent>
// ---------------------------------------------------------------------------

/**
 * Adapt a `ReadableStream<Uint8Array>` (a `Response.body`) into an async
 * sequence of parsed SSE events.
 *
 * Termination semantics (the contract `use-llm-stream` builds on):
 * - The generator ends when the underlying stream ends. An event that
 *   was still unterminated at that point (no closing blank line) is
 *   discarded, never yielded — so "the generator ended without yielding
 *   a terminal event" is a faithful signal of an interrupted stream
 *   (Req 17.11).
 * - If the consumer exits early (`break` after the terminal event) or
 *   the generator is torn down, the reader is cancelled in `finally`,
 *   which closes the underlying HTTP connection so the client stops
 *   consuming the response (the backend aborts its provider call on
 *   disconnect, Req 11.7).
 */
export async function* readSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent, void, undefined> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SseParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      // `stream: true` keeps partial multi-byte UTF-8 sequences buffered
      // inside the decoder across reads.
      for (const event of parser.feed(
        decoder.decode(value, { stream: true }),
      )) {
        yield event;
      }
    }
    // Flush any bytes the decoder was still holding at end-of-stream.
    for (const event of parser.feed(decoder.decode())) {
      yield event;
    }
  } finally {
    // Early consumer exit or error: release the connection. Cancelling
    // an already-finished reader is a harmless no-op.
    try {
      await reader.cancel();
    } catch {
      // The stream may already be errored or the request aborted —
      // there is nothing left to release either way.
    }
  }
}

// ---------------------------------------------------------------------------
// requestSseStream — authenticated POST that negotiates SSE
// ---------------------------------------------------------------------------

/**
 * Open an LLM streaming request through `apiFetch` (Bearer attachment +
 * one silent refresh-and-retry on 401 — see `lib/api.ts`).
 *
 * Returns the raw `Response` rather than a parsed stream on purpose: the
 * backend evaluates every gate *before* opening the stream (Req 11.4),
 * so a quota 429 / breaker 503 / ownership 404 arrives as a plain RFC
 * 7807 JSON response, not as SSE. The caller branches on `response.ok`
 * and only hands `response.body` to `readSseStream` for 2xx responses.
 *
 * Body note: `apiFetch`'s 401 retry re-sends the same `RequestInit`, so
 * callers must pass re-usable bodies (a JSON string — the only body
 * shape the LLM endpoints use — is always re-usable).
 */
export async function requestSseStream(
  path: string,
  init: ApiFetchInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "text/event-stream");
  return apiFetch(path, { ...init, headers });
}
