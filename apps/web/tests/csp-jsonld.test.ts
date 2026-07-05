/**
 * CSP-untouched + no-JSON-LD guard (`seo-foundation` task 5.2; Req 11.1, 11.2).
 *
 * ADR 0006 Decision 5 defers JSON-LD to a future per-request CSP nonce; this
 * spec ships no structured-data payload and makes no CSP change for SEO. These
 * guards pin both facts:
 *
 *   1. The `proxy.ts` `script-src` is exactly the pre-existing Phase-1 value
 *      (`'self' 'unsafe-inline'` outside dev) — this spec did NOT broaden it
 *      with a hash/nonce/extra host to accommodate structured data (Req 11.1).
 *   2. No `(marketing)` page emits a `<script type="application/ld+json">`
 *      block (Req 11.2).
 *
 * Source/module assertions only → default Vitest `node` environment.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import { type NextRequest } from "next/server";

import { proxy } from "@/proxy";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "../src/app");

function cspFor(pathname: string): string {
  const req = {
    nextUrl: { protocol: "https:", pathname },
  } as unknown as NextRequest;
  return proxy(req).headers.get("content-security-policy") ?? "";
}

describe("CSP is not broadened for SEO (Req 11.1)", () => {
  const csp = cspFor("/");

  it("keeps the Phase-1 script-src ('self' 'unsafe-inline') with no nonce/hash added", () => {
    // Outside development the proxy emits exactly this. `'unsafe-eval'` is a
    // dev-only allowance and must not appear under the test/prod NODE_ENV.
    expect(csp).toContain("script-src 'self' 'unsafe-inline'");
    expect(csp).not.toContain("'unsafe-eval'");
    // No structured-data accommodation: no nonce source, no sha hash allowance.
    expect(csp).not.toContain("'nonce-");
    expect(csp).not.toContain("'sha256-");
  });
});

describe("no JSON-LD payload ships on the marketing surface (Req 11.2)", () => {
  function collectTsx(dir: string): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) out.push(...collectTsx(full));
      else if (entry.name.endsWith(".tsx")) out.push(full);
    }
    return out;
  }

  const marketingFiles = collectTsx(path.join(appRoot, "(marketing)")).map(
    (f) => path.relative(appRoot, f),
  );

  it.each(marketingFiles)("%s emits no application/ld+json script", (rel) => {
    const source = fs.readFileSync(path.join(appRoot, rel), "utf8");
    expect(source).not.toContain("application/ld+json");
  });
});
