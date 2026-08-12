/**
 * Unit tests for the SSE transport layer in `sse.ts`
 * (phase-3-llm-layer Task 11.1; Requirements 11.1, 17.11).
 *
 * Covers the wire-format parser (`SseParser`) — including the
 * chunk-boundary cases that motivate its stateful design — and the
 * stream adapter (`readSseStream`)'s end-of-stream semantics: an event
 * left unterminated at connection close is discarded, which is the
 * signal `use-llm-stream` derives the "interrupted" state from.
 */

import { describe, expect, it } from "vitest";

import { readSseStream, SseParser, type SseEvent } from "@/lib/llm/sse";

describe("SseParser", () => {
  it("dispatches an event/data pair on the blank-line terminator", () => {
    const parser = new SseParser();
    const events = parser.feed('event: delta\ndata: {"text": "hi"}\n\n');
    expect(events).toEqual([{ event: "delta", data: '{"text": "hi"}' }]);
  });

  it('defaults the event type to "message" when no event field is set', () => {
    const parser = new SseParser();
    expect(parser.feed("data: x\n\n")).toEqual([
      { event: "message", data: "x" },
    ]);
  });

  it("joins consecutive data lines with a newline", () => {
    const parser = new SseParser();
    expect(parser.feed("data: line1\ndata: line2\n\n")).toEqual([
      { event: "message", data: "line1\nline2" },
    ]);
  });

  it("is chunk-boundary safe: a line split across feeds parses whole", () => {
    const parser = new SseParser();
    expect(parser.feed("event: com")).toEqual([]);
    expect(parser.feed("plete\ndata: {}")).toEqual([]);
    expect(parser.feed("\n\n")).toEqual([{ event: "complete", data: "{}" }]);
  });

  it("handles a CRLF pair split across two chunks as one terminator", () => {
    const parser = new SseParser();
    expect(parser.feed("data: a\r")).toEqual([]);
    // The lone trailing \r was held; the \n arriving next must not
    // produce a phantom blank line (which would dispatch prematurely).
    expect(parser.feed("\ndata: b\r\n\r\n")).toEqual([
      { event: "message", data: "a\nb" },
    ]);
  });

  it("ignores comment lines and id/retry fields", () => {
    const parser = new SseParser();
    expect(parser.feed(": heartbeat\nid: 7\nretry: 100\ndata: x\n\n")).toEqual([
      { event: "message", data: "x" },
    ]);
  });

  it("does not dispatch an event without any data line", () => {
    const parser = new SseParser();
    expect(parser.feed("event: delta\n\n")).toEqual([]);
    // ...and the stray event type must not leak into the next event.
    expect(parser.feed("data: x\n\n")).toEqual([
      { event: "message", data: "x" },
    ]);
  });

  it("never dispatches an event that lacks its blank-line terminator", () => {
    const parser = new SseParser();
    expect(parser.feed('event: complete\ndata: {"v": 1}\n')).toEqual([]);
  });
});

/** Build a ReadableStream<Uint8Array> from pre-encoded text chunks. */
function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

async function collect(
  stream: ReadableStream<Uint8Array>,
): Promise<SseEvent[]> {
  const events: SseEvent[] = [];
  for await (const event of readSseStream(stream)) {
    events.push(event);
  }
  return events;
}

describe("readSseStream", () => {
  it("yields parsed events across arbitrary chunk boundaries", async () => {
    const events = await collect(
      streamOf([
        'event: delta\ndata: {"te',
        'xt": "a"}\n\nevent: comp',
        "lete\ndata: {}\n\n",
      ]),
    );
    expect(events).toEqual([
      { event: "delta", data: '{"text": "a"}' },
      { event: "complete", data: "{}" },
    ]);
  });

  it("decodes a multi-byte UTF-8 sequence split across chunks", async () => {
    const encoded = new TextEncoder().encode("data: é\n\n");
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        // Split inside the 2-byte é sequence.
        controller.enqueue(encoded.slice(0, 7));
        controller.enqueue(encoded.slice(7));
        controller.close();
      },
    });
    expect(await collect(stream)).toEqual([{ event: "message", data: "é" }]);
  });

  it("discards an event left unterminated at stream close (Req 17.11)", async () => {
    // The terminal never got its blank line — connection dropped. The
    // generator must end without yielding it, so the consumer can
    // faithfully derive the interrupted state.
    const events = await collect(
      streamOf([
        'event: delta\ndata: {"text": "a"}\n\nevent: complete\ndata: {"v"',
      ]),
    );
    expect(events).toEqual([{ event: "delta", data: '{"text": "a"}' }]);
  });
});
