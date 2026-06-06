/**
 * Unit tests for `formatBytes` (Task 6.5; Req 9.3, 16.3; design Testing Strategy).
 *
 * `formatBytes` renders the human-readable size shown in the `FilePreviewCard`
 * from a `ResumeResponse.byte_size`. Its threshold + rounding behavior is a
 * documented contract (see the helper's docstring), so these are pure
 * example/edge tests pinning every boundary:
 *
 *   - the unit step boundaries (B → KB at 1024, KB → MB at 1024²);
 *   - the 5 MB upload-cap boundary (Req 9.1);
 *   - the nearest-integer KB rounding and one-decimal MB rounding;
 *   - the carry guard that promotes a value rounding to 1024 KB up to MB so the
 *     UI never emits a nonsensical "1024 KB";
 *   - the non-finite / non-positive guard that yields "0 B" so the DOM never
 *     shows "NaN" or a negative size (the visual-acceptance gate forbids NaN).
 *
 * This is **not** a property-based-testing feature — Vitest example/edge tests
 * only. No DOM is needed, so this file runs in the repo-default `node`
 * environment (no `@vitest-environment jsdom` pragma) and uses no jest-dom
 * matchers.
 */

import { describe, expect, it } from "vitest";

import { formatBytes } from "@/lib/utils";

describe("formatBytes — required edge cases (Task 6.5)", () => {
  it("renders 0 as '0 B'", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("renders 1023 (one below the KB step) as '1023 B'", () => {
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("renders 1024 (the KB step) as '1 KB'", () => {
    expect(formatBytes(1024)).toBe("1 KB");
  });

  it("renders the 5 MB upload cap (5242880) as '5.0 MB'", () => {
    expect(formatBytes(5_242_880)).toBe("5.0 MB");
  });
});

describe("formatBytes — bytes range (n < 1024)", () => {
  it("renders a sub-KB count as an integer with a ' B' suffix", () => {
    expect(formatBytes(1)).toBe("1 B");
    expect(formatBytes(500)).toBe("500 B");
  });

  it("rounds a fractional sub-KB byte count to the nearest integer", () => {
    // Defensive: byte_size is an int in the contract, but the helper still
    // rounds rather than emitting a fractional " B".
    expect(formatBytes(512.4)).toBe("512 B");
  });
});

describe("formatBytes — kilobytes range (1024 ≤ n < 1024²)", () => {
  it("rounds KB to the nearest integer (no decimals at this scale)", () => {
    // 248913 / 1024 = 243.07… → 243 (matches the documented wireframe example).
    expect(formatBytes(248_913)).toBe("243 KB");
  });

  it("rounds a half-KB value up to the nearest integer KB", () => {
    // 1536 / 1024 = 1.5 → Math.round → 2.
    expect(formatBytes(1536)).toBe("2 KB");
  });

  it("renders one below the MB step that does not carry as KB", () => {
    // 1047551 / 1024 = 1023.0 (rounds to 1023) → stays "1023 KB", no carry.
    expect(formatBytes(1_047_551)).toBe("1023 KB");
  });
});

describe("formatBytes — megabytes range (n ≥ 1024²)", () => {
  it("renders the MB step (1048576) as '1.0 MB'", () => {
    expect(formatBytes(1_048_576)).toBe("1.0 MB");
  });

  it("renders MB with one decimal place of resolution", () => {
    // 4733120 / 1048576 = 4.514… → "4.5 MB" (the resumeFailed fixture size).
    expect(formatBytes(4_733_120)).toBe("4.5 MB");
  });
});

describe("formatBytes — carry guard (KB rounding reaching 1024)", () => {
  it("promotes a value that would round to 1024 KB up to MB", () => {
    // 1048575 is one byte below 1 MB; 1048575 / 1024 = 1023.999… → rounds to
    // 1024, which must NOT render as "1024 KB" — it is promoted to "1.0 MB".
    expect(formatBytes(1_048_575)).toBe("1.0 MB");
  });
});

describe("formatBytes — non-finite / non-positive guard", () => {
  it("renders NaN, Infinity, and negatives as '0 B'", () => {
    expect(formatBytes(Number.NaN)).toBe("0 B");
    expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("0 B");
    expect(formatBytes(Number.NEGATIVE_INFINITY)).toBe("0 B");
    expect(formatBytes(-1)).toBe("0 B");
    expect(formatBytes(-5_242_880)).toBe("0 B");
  });
});
