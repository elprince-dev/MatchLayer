/**
 * Unit tests for the pure stream-state derivation in `use-llm-stream.ts`
 * (phase-3-llm-layer Task 11.1; Requirements 17.2, 17.10, 17.11).
 *
 * These are example-based tests of the pure core (`reduceSseEvent` /
 * `finalizeStream`). The property-based coverage of the same surface
 * (Property 22) is Task 11.2 and lives separately.
 */

import { describe, expect, it } from "vitest";

import type { SseEvent } from "@/lib/llm/sse";
import {
  finalizeStream,
  initialStreamReduction,
  reduceSseEvent,
  type LlmStreamReduction,
} from "@/lib/llm/use-llm-stream";

function run(events: SseEvent[]): LlmStreamReduction {
  return events.reduce(reduceSseEvent, initialStreamReduction);
}

const delta = (text: string): SseEvent => ({
  event: "delta",
  data: JSON.stringify({ text }),
});

describe("reduceSseEvent", () => {
  it("accumulates delta text fragments in order", () => {
    const state = run([delta('{"summary": "You'), delta(" have strong")]);
    expect(state.rawText).toBe('{"summary": "You have strong');
    expect(state.terminal).toBeNull();
  });

  it("records a complete terminal with its parsed payload", () => {
    const envelope = { id: "x", is_fallback: false, result: { summary: "s" } };
    const state = run([
      delta("{"),
      { event: "complete", data: JSON.stringify(envelope) },
    ]);
    expect(state.terminal).toEqual({ kind: "complete", payload: envelope });
  });

  it("records a degraded terminal with its parsed payload", () => {
    const envelope = { id: null, is_fallback: true, result: {} };
    const state = run([{ event: "degraded", data: JSON.stringify(envelope) }]);
    expect(state.terminal).toEqual({ kind: "degraded", payload: envelope });
  });

  it("records an error terminal as a coerced RFC 7807 problem", () => {
    const state = run([
      {
        event: "error",
        data: JSON.stringify({
          type: "llm_timeout",
          title: "Timed out",
          detail: "The model took too long.",
          status: 200,
          request_id: "req-1",
        }),
      },
    ]);
    expect(state.terminal).toEqual({
      kind: "error",
      problem: {
        type: "llm_timeout",
        title: "Timed out",
        detail: "The model took too long.",
        status: 200,
        request_id: "req-1",
      },
    });
  });

  it("coerces a malformed error body into a renderable problem", () => {
    const state = run([{ event: "error", data: "not json" }]);
    expect(state.terminal).not.toBeNull();
    expect(state.terminal?.kind).toBe("error");
    if (state.terminal?.kind === "error") {
      expect(state.terminal.problem.status).toBe(500);
      expect(state.terminal.problem.detail.length).toBeGreaterThan(0);
    }
  });

  it("ignores every event after the terminal (returns same reference)", () => {
    const terminalState = run([
      { event: "complete", data: JSON.stringify({ ok: true }) },
    ]);
    const after = reduceSseEvent(terminalState, delta("late"));
    expect(after).toBe(terminalState);
    const after2 = reduceSseEvent(terminalState, {
      event: "error",
      data: "{}",
    });
    expect(after2).toBe(terminalState);
  });

  it("drops a malformed delta payload without failing", () => {
    const state = run([delta("a"), { event: "delta", data: "not json" }]);
    expect(state.rawText).toBe("a");
  });

  it("drops a complete terminal whose payload is not JSON", () => {
    const state = run([{ event: "complete", data: "{truncated" }]);
    expect(state.terminal).toBeNull();
  });

  it("ignores unknown event types", () => {
    const state = run([{ event: "ping", data: "{}" }, delta("a")]);
    expect(state.rawText).toBe("a");
    expect(state.terminal).toBeNull();
  });
});

describe("finalizeStream", () => {
  it("resolves interrupted when the stream closed without a terminal", () => {
    const state = run([delta("partial "), delta("content")]);
    expect(finalizeStream(state)).toEqual({ kind: "interrupted" });
  });

  it("resolves the recorded terminal when one arrived", () => {
    const state = run([
      delta("x"),
      { event: "degraded", data: JSON.stringify({ is_fallback: true }) },
    ]);
    expect(finalizeStream(state)).toEqual({
      kind: "degraded",
      payload: { is_fallback: true },
    });
  });

  it("derives the outcome from the terminal only, never the delta text", () => {
    const withDeltas = run([
      delta("aaa"),
      { event: "complete", data: JSON.stringify({ v: 1 }) },
    ]);
    const withoutDeltas = run([
      { event: "complete", data: JSON.stringify({ v: 1 }) },
    ]);
    expect(finalizeStream(withDeltas)).toEqual(finalizeStream(withoutDeltas));
  });
});
