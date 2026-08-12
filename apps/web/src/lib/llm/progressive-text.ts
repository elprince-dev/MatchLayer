/**
 * Display-only tolerant text extraction from partial JSON
 * (phase-3-llm-layer Task 11.1; Requirement 17.2).
 *
 * The LLM endpoints stream the model's raw structured output: the
 * accumulated `delta` fragments form a *partial JSON document* (a
 * CoachingReport / BulletRewrite / InterviewQuestionSet mid-generation),
 * e.g. `{"summary": "Your resume shows strong backe`. Rendering that
 * raw buffer would show the user JSON syntax; waiting for the terminal
 * event would defeat streaming. This module extracts the human-readable
 * parts — the JSON *string values* — from the partial document so the
 * UI can render prose progressively while tokens arrive.
 *
 * Contract:
 * - **Display-only.** The output is never the authoritative result; the
 *   terminal `complete` event's validated envelope always replaces it
 *   (Req 17.2). Nothing here validates, repairs, or interprets the JSON.
 * - **Tolerant.** The input is truncated at an arbitrary byte: an
 *   unterminated string value contributes the characters decoded so far
 *   (that is the progressive point); a truncated escape sequence at the
 *   buffer edge is silently dropped (the next extraction over the grown
 *   buffer will decode it whole).
 * - **Keys are excluded.** Only string *values* are user prose; keys
 *   (`"summary"`, `"strengths"`, …) are schema noise. Object/array
 *   nesting is tracked so key/value positions stay correct at any
 *   depth. Numbers, booleans, and nulls are not text and are skipped.
 * - **Pure and deterministic** — same buffer in, same text out. It is
 *   re-run over the whole accumulated buffer on each delta, not fed
 *   incrementally, so there is no hidden state to drift.
 */

/** JSON container context used to distinguish keys from values. */
type Frame = { type: "object"; expectKey: boolean } | { type: "array" };

/** Result of scanning a JSON string literal starting after its `"`. */
interface StringScan {
  /** Decoded characters (escapes resolved, truncated escape dropped). */
  text: string;
  /** Index just past the closing quote, or `input.length` if unclosed. */
  end: number;
  /** Whether the closing `"` was reached inside the buffer. */
  closed: boolean;
}

/**
 * Decode a JSON string literal beginning at `from` (the index just
 * after the opening `"`). Tolerates truncation: an unclosed string
 * returns everything decoded so far, and an escape cut off by the end
 * of the buffer is dropped rather than emitted half-decoded.
 */
function scanString(input: string, from: number): StringScan {
  let text = "";
  let i = from;
  while (i < input.length) {
    const ch = input.charAt(i);
    if (ch === '"') {
      return { text, end: i + 1, closed: true };
    }
    if (ch === "\\") {
      if (i + 1 >= input.length) {
        // Escape introducer truncated at the buffer edge — drop it.
        return { text, end: input.length, closed: false };
      }
      const esc = input.charAt(i + 1);
      i += 2;
      switch (esc) {
        case "n":
          text += "\n";
          break;
        case "t":
          text += "\t";
          break;
        case "r":
          text += "\r";
          break;
        case "b":
          text += "\b";
          break;
        case "f":
          text += "\f";
          break;
        case '"':
        case "\\":
        case "/":
          text += esc;
          break;
        case "u": {
          const hex = input.slice(i, i + 4);
          if (hex.length < 4) {
            // `\uXX` truncated at the buffer edge — drop it.
            return { text, end: input.length, closed: false };
          }
          if (/^[0-9a-fA-F]{4}$/.test(hex)) {
            text += String.fromCharCode(Number.parseInt(hex, 16));
          }
          i += 4;
          break;
        }
        default:
          // Invalid escape — tolerate by emitting the escaped character
          // verbatim (display-only; strict JSON would reject it).
          text += esc;
          break;
      }
      continue;
    }
    text += ch;
    i += 1;
  }
  return { text, end: input.length, closed: false };
}

/**
 * Extract the user-displayable text from a (possibly partial) JSON
 * document: every JSON string *value*, decoded, in document order,
 * joined with newlines. See the module doc for the full contract.
 *
 * @param partialJson The accumulated raw delta text — a prefix of a
 *   JSON document, cut at an arbitrary character.
 * @returns The decoded string values joined with `"\n"`; `""` when the
 *   buffer contains no (started) string value yet.
 */
export function extractProgressiveText(partialJson: string): string {
  const parts: string[] = [];
  const stack: Frame[] = [];

  let i = 0;
  while (i < partialJson.length) {
    const ch = partialJson.charAt(i);

    if (ch === '"') {
      const top = stack[stack.length - 1];
      // A string is a key iff it sits in an object slot expecting a key;
      // strings in arrays and at top level are values.
      const isValue =
        top === undefined || top.type === "array" || !top.expectKey;
      const scan = scanString(partialJson, i + 1);
      if (isValue && scan.text.length > 0) {
        parts.push(scan.text);
      }
      if (!scan.closed) {
        break; // the buffer ends inside this string
      }
      i = scan.end;
      continue;
    }

    switch (ch) {
      case "{":
        stack.push({ type: "object", expectKey: true });
        break;
      case "[":
        stack.push({ type: "array" });
        break;
      case "}":
      case "]":
        stack.pop();
        break;
      case ":": {
        const top = stack[stack.length - 1];
        if (top !== undefined && top.type === "object") {
          top.expectKey = false; // the next string is this key's value
        }
        break;
      }
      case ",": {
        const top = stack[stack.length - 1];
        if (top !== undefined && top.type === "object") {
          top.expectKey = true; // back to a key position
        }
        break;
      }
      default:
        // Whitespace, digits, `true`/`false`/`null` characters — not
        // displayable text, and structurally irrelevant to key/value
        // tracking.
        break;
    }
    i += 1;
  }

  return parts.join("\n");
}
