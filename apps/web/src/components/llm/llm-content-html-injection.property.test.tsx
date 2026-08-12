/**
 * Property 23: LLM content rendering injects no HTML
 * (phase-3-llm-layer Task 11.4; fast-check + Vitest).
 *
 * **Validates: Requirements 17.3**
 *
 * Subject: the LLM content components that interpolate model-produced (or
 * model-adjacent) strings into the DOM — `StreamingText` (the streamed /
 * final LLM text, via its `text` prop) and `LlmErrorState` (the RFC 7807
 * `detail` string surfaced from an error terminal, via its `detail` prop).
 * The property statement (design.md): for any LLM-produced string —
 * including strings embedding `<script>`, event-handler attributes, and
 * arbitrary HTML — rendering through the LLM content components produces a
 * DOM containing no element or attribute originating from the content; the
 * content appears only as text.
 *
 * Strategy: render each component with adversarial generated strings
 * (arbitrary text interleaved with real attack fragments) and assert that
 * (a) the element structure — tag names plus attribute-name sets, in
 * document order — is byte-identical to a baseline render with benign text,
 * so the content contributed no element and no attribute; (b) no
 * script-capable element or `on*` handler attribute exists anywhere; and
 * (c) the content lands verbatim as inert `textContent` of the designated
 * text node. Structure equality is the load-bearing check: it holds only if
 * the string went through React's text-child escaping (never
 * `dangerouslySetInnerHTML` or any HTML-parsing path).
 *
 * Conventions mirror the co-located component tests
 * (`skeleton-loader.test.tsx`): `@testing-library/react` render/cleanup,
 * `afterEach(cleanup)`, `toBeInstanceOf`, no jest-dom matchers — and the
 * co-located property test (`use-llm-stream.property.test.ts`) for
 * fast-check usage. All fixtures are synthetic (security.md).
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { cleanup, render } from "@testing-library/react";
import fc from "fast-check";
import { afterEach, describe, expect, it } from "vitest";

import { LlmErrorState, type LlmErrorKind } from "./LlmErrorStates";
import { StreamingText } from "./StreamingText";

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * A render's structural fingerprint: every element's tag name plus its
 * sorted attribute names, in document order. If injected content created an
 * element (`<script>`, `<img>`, a stray `</p><div>` split) or an attribute
 * (`onerror`, `onclick`) anywhere, the fingerprint diverges from the benign
 * baseline.
 */
function structureOf(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("*")).map(
    (el) => `${el.tagName}[${el.getAttributeNames().sort().join(",")}]`,
  );
}

/** Assert no script-capable element and no event-handler attribute exists. */
function expectNoActiveContent(container: HTMLElement): void {
  expect(
    container.querySelector(
      "script, iframe, img, object, embed, style, link, form",
    ),
  ).toBeNull();

  for (const el of Array.from(container.querySelectorAll("*"))) {
    const handlerAttrs = el
      .getAttributeNames()
      .filter((name) => name.toLowerCase().startsWith("on"));
    expect(handlerAttrs).toEqual([]);
  }
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Real-world injection payload fragments (Req 17.3's enumerated shapes:
 * `<script>`, event-handler attributes, arbitrary HTML, plus entity and
 * attribute-breakout forms). */
const attackFragmentArb: fc.Arbitrary<string> = fc.constantFrom(
  "<script>alert(1)</script>",
  '<img src=x onerror="alert(1)">',
  "<iframe src='javascript:alert(1)'></iframe>",
  "</p><div id='pwn'>injected</div>",
  "<b onmouseover=alert(1)>hover</b>",
  '<a href="javascript:alert(1)">click</a>',
  "<style>*{display:none}</style>",
  '" onclick="alert(1)" data-x="',
  "'><svg onload=alert(1)>",
  "&lt;script&gt;alert(1)&lt;/script&gt;",
  "&#60;script&#62;alert(1)&#60;/script&#62;",
  "&amp;&quot;&apos;",
  "<!-- comment --><p>para</p>",
  "<template><script>alert(1)</script></template>",
);

/** An adversarial LLM output: arbitrary text interleaved with attack
 * fragments, so payloads appear at the start, middle, end, or alone. */
const llmContentArb: fc.Arbitrary<string> = fc
  .array(
    fc.oneof(
      { weight: 1, arbitrary: fc.string() },
      { weight: 2, arbitrary: attackFragmentArb },
    ),
    { minLength: 1, maxLength: 6 },
  )
  .map((parts) => parts.join(""));

const errorKindArb: fc.Arbitrary<LlmErrorKind> = fc.constantFrom(
  "quota",
  "unavailable",
  "interrupted",
);

// ---------------------------------------------------------------------------
// Property 23
// ---------------------------------------------------------------------------

describe("Property 23: LLM content rendering injects no HTML", () => {
  it("StreamingText renders any LLM string as inert text — no element or attribute originates from the content", () => {
    // Benign baseline: the element structure any text-only render produces.
    const baseline = render(
      <StreamingText text="benign baseline" streaming={false} />,
    );
    const baselineStructure = structureOf(baseline.container);
    baseline.unmount();

    fc.assert(
      fc.property(llmContentArb, fc.boolean(), (content, streaming) => {
        const { container, unmount } = render(
          <StreamingText text={content} streaming={streaming} />,
        );
        try {
          // (a) For non-empty content (empty text may take the skeleton
          // branch while streaming), the adversarial content contributed no
          // element and no attribute: structure is identical to the benign
          // baseline.
          if (content !== "") {
            expect(structureOf(container)).toEqual(baselineStructure);

            // (c) The content appears only as text, verbatim, in the
            // designated paragraph node.
            const paragraph = container.querySelector("p");
            expect(paragraph).toBeInstanceOf(HTMLElement);
            expect(paragraph?.textContent).toBe(content);
          }

          // (b) Regardless of branch (skeleton or text), nothing
          // script-capable exists anywhere in the render.
          expectNoActiveContent(container);
        } finally {
          unmount();
        }
      }),
    );
  });

  it("LlmErrorState renders any RFC 7807 detail as inert text — no element or attribute originates from the detail", () => {
    fc.assert(
      fc.property(errorKindArb, llmContentArb, (kind, detail) => {
        // Benign baseline for the same kind (structure varies by kind — the
        // interrupted state may carry a retry button — so compare per kind).
        const baseline = render(
          <LlmErrorState kind={kind} detail="benign baseline" />,
        );
        const baselineStructure = structureOf(baseline.container);
        baseline.unmount();

        const { container, unmount } = render(
          <LlmErrorState kind={kind} detail={detail} />,
        );
        try {
          expect(structureOf(container)).toEqual(baselineStructure);
          expectNoActiveContent(container);

          // Non-empty detail is rendered verbatim as the body text (empty
          // detail falls back to the component's static copy).
          if (detail !== "") {
            const body = Array.from(container.querySelectorAll("p")).at(-1);
            expect(body).toBeInstanceOf(HTMLElement);
            expect(body?.textContent).toBe(detail);
          }
        } finally {
          unmount();
        }
      }),
    );
  });
});
