/**
 * Unit tests for the display-only progressive text extraction
 * (phase-3-llm-layer Task 11.1; Requirement 17.2).
 *
 * The subject is `extractProgressiveText`: pull the human-readable JSON
 * string *values* out of a partial JSON document so the UI can render
 * prose while the model is still generating.
 */

import { describe, expect, it } from "vitest";

import { extractProgressiveText } from "@/lib/llm/progressive-text";

describe("extractProgressiveText", () => {
  it("returns the empty string for an empty or value-less buffer", () => {
    expect(extractProgressiveText("")).toBe("");
    expect(extractProgressiveText('{"count": 3, "ok": true')).toBe("");
  });

  it("extracts string values but never keys", () => {
    const text = extractProgressiveText(
      '{"summary": "Strong backend profile", "score": 87}',
    );
    expect(text).toBe("Strong backend profile");
    expect(text).not.toContain("summary");
    expect(text).not.toContain("score");
  });

  it("includes an unterminated trailing string value (the progressive case)", () => {
    expect(
      extractProgressiveText('{"summary": "Your resume shows strong backe'),
    ).toBe("Your resume shows strong backe");
  });

  it("extracts array element strings as values at any depth", () => {
    const text = extractProgressiveText(
      '{"strengths": ["Python", "FastAPI"], "gaps": {"top": "Kubernetes"',
    );
    expect(text).toBe("Python\nFastAPI\nKubernetes");
  });

  it("decodes escape sequences in values", () => {
    expect(extractProgressiveText('{"a": "line1\\nline2 \\"quoted\\""}')).toBe(
      'line1\nline2 "quoted"',
    );
    expect(extractProgressiveText('{"a": "caf\\u00e9"}')).toBe("café");
  });

  it("drops an escape sequence truncated at the buffer edge", () => {
    expect(extractProgressiveText('{"a": "abc\\')).toBe("abc");
    expect(extractProgressiveText('{"a": "abc\\u00')).toBe("abc");
  });

  it("is deterministic: growing the buffer only extends the extraction", () => {
    const full = '{"summary": "Great fit", "strengths": ["SQL"]}';
    const atPrefix = extractProgressiveText(full.slice(0, 22));
    const atFull = extractProgressiveText(full);
    expect(atFull.startsWith(atPrefix)).toBe(true);
  });
});
