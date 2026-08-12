/**
 * Property 22: Frontend stream state derives only from the terminal event
 * (phase-3-llm-layer Task 11.2; fast-check + Vitest).
 *
 * **Validates: Requirements 17.2, 17.10, 17.11**
 *
 * Subject: the pure stream-state derivation exported by
 * `use-llm-stream.ts` (`reduceSseEvent` / `finalizeStream`) — the core
 * the hook folds every SSE event through. The property statement
 * (design.md): for any generated SSE event sequence — deltas followed by
 * a `complete`, `degraded`, or `error` terminal, or a connection close
 * with no terminal — the final state is the structured result exactly
 * when the terminal is `complete` (17.2), the fallback content when
 * `degraded` (17.10), the error detail when `error`, and interrupted
 * when no terminal arrived (17.11); progressively rendered delta text is
 * never presented as the final result.
 */

import fc from "fast-check";
import { describe, expect, it } from "vitest";

import type { SseEvent } from "@/lib/llm/sse";
import {
  finalizeStream,
  initialStreamReduction,
  reduceSseEvent,
  type LlmStreamOutcome,
} from "@/lib/llm/use-llm-stream";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Reduce an event sequence and resolve its final outcome — exactly what
 * the hook does after the connection closes. */
function outcomeOf(events: readonly SseEvent[]): LlmStreamOutcome {
  return finalizeStream(events.reduce(reduceSseEvent, initialStreamReduction));
}

function isJson(data: string): boolean {
  try {
    JSON.parse(data);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

const TERMINAL_EVENT_NAMES = ["complete", "degraded", "error"] as const;
const KNOWN_EVENT_NAMES = ["delta", ...TERMINAL_EVENT_NAMES];

/** A serialized JSON document — the payload shape terminals carry. */
const jsonDataArb: fc.Arbitrary<string> = fc
  .jsonValue()
  .map((value) => JSON.stringify(value));

/** A `delta` event: usually well-formed (`{"text": ...}`), sometimes a
 * malformed payload (dropped by the reducer). Either way, non-terminal. */
const deltaEventArb: fc.Arbitrary<SseEvent> = fc.record({
  event: fc.constant("delta"),
  data: fc.oneof(
    fc.string().map((text) => JSON.stringify({ text })),
    fc.string(),
  ),
});

/** An event of a type the client does not know (forward compatibility —
 * ignored by the reducer). */
const unknownEventArb: fc.Arbitrary<SseEvent> = fc.record({
  event: fc.string().filter((name) => !KNOWN_EVENT_NAMES.includes(name)),
  data: fc.string(),
});

/** A `complete`/`degraded` frame whose payload is not valid JSON — the
 * reducer drops it, so it is NOT a usable terminal. */
const unparseableTerminalArb: fc.Arbitrary<SseEvent> = fc.record({
  event: fc.constantFrom<string>("complete", "degraded"),
  data: fc.string().filter((data) => !isJson(data)),
});

/** Any event that must not resolve the stream. */
const nonTerminalEventArb: fc.Arbitrary<SseEvent> = fc.oneof(
  { weight: 3, arbitrary: deltaEventArb },
  { weight: 1, arbitrary: unknownEventArb },
  { weight: 1, arbitrary: unparseableTerminalArb },
);

/** A usable terminal: `complete`/`degraded` with a JSON payload, or
 * `error` with any body (RFC 7807 coercion is total). */
const terminalEventArb: fc.Arbitrary<SseEvent> = fc.oneof(
  fc.record({ event: fc.constant("complete"), data: jsonDataArb }),
  fc.record({ event: fc.constant("degraded"), data: jsonDataArb }),
  fc.record({ event: fc.constant("error"), data: fc.string() }),
);

/** Any SSE event at all — used for post-terminal noise. */
const anyEventArb: fc.Arbitrary<SseEvent> = fc.oneof(
  nonTerminalEventArb,
  terminalEventArb,
);

// ---------------------------------------------------------------------------
// Property 22
// ---------------------------------------------------------------------------

describe("Property 22: stream state derives only from the terminal event", () => {
  it("resolves exactly the terminal's outcome, regardless of the deltas before it or any frames after it", () => {
    fc.assert(
      fc.property(
        fc.array(nonTerminalEventArb),
        terminalEventArb,
        fc.array(anyEventArb),
        (before, terminal, after) => {
          const outcome = outcomeOf([...before, terminal, ...after]);

          // The surrounding events contribute nothing: the same terminal
          // alone yields the identical outcome (17.2, 17.10).
          expect(outcome).toEqual(outcomeOf([terminal]));

          // And the outcome is the one the terminal names — never a
          // repackaging of accumulated delta text.
          if (terminal.event === "complete" || terminal.event === "degraded") {
            expect(outcome).toEqual({
              kind: terminal.event,
              payload: JSON.parse(terminal.data),
            });
          } else {
            expect(outcome.kind).toBe("error");
          }
        },
      ),
    );
  });

  it("resolves interrupted when the connection closes without a usable terminal (17.11)", () => {
    fc.assert(
      fc.property(fc.array(nonTerminalEventArb), (events) => {
        expect(outcomeOf(events)).toEqual({ kind: "interrupted" });
      }),
    );
  });

  it("yields the same outcome with every delta removed — progressive text never becomes the result", () => {
    fc.assert(
      fc.property(fc.array(anyEventArb), (events) => {
        const withoutDeltas = events.filter((e) => e.event !== "delta");
        expect(outcomeOf(events)).toEqual(outcomeOf(withoutDeltas));
      }),
    );
  });

  it("coerces any error terminal body into a renderable RFC 7807 problem", () => {
    fc.assert(
      fc.property(
        fc.array(nonTerminalEventArb),
        fc.string(),
        (before, errorBody) => {
          const outcome = outcomeOf([
            ...before,
            { event: "error", data: errorBody },
          ]);
          expect(outcome.kind).toBe("error");
          if (outcome.kind === "error") {
            expect(typeof outcome.problem.title).toBe("string");
            expect(outcome.problem.detail.length).toBeGreaterThan(0);
            expect(typeof outcome.problem.status).toBe("number");
          }
        },
      ),
    );
  });
});
