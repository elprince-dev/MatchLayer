/**
 * Public marketing-surface SEO guards (`seo-foundation` tasks 4.4, 5.1;
 * Req 1.4, 1.5, 6.2, 7.x, 10.1–10.4).
 *
 * Three guarantees, all as source/module assertions (no DOM), so the default
 * Vitest `node` environment suffices:
 *
 *   1. Uniqueness — every indexable public page has a title and description
 *      unique across the Marketing_Surface (Req 1.4, 1.5).
 *   2. Resolution — every route in `PUBLIC_ROUTES` maps to a real page file, so
 *      a sitemap entry can never point at a 404 (Req 6.2 integrity).
 *   3. Metadata-API-only — no `(marketing)` page hand-places `<head>`/`<meta>`/
 *      canonical/social tags (Req 10.2, 10.3), and no `(app)`/`(auth)` route
 *      imports the marketing SEO helpers (Req 10.4; ADR 0006, ADR 0007).
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { metadata as marketingRoot } from "@/app/(marketing)/layout";
import { metadata as aboutMeta } from "@/app/(marketing)/about/page";
import { metadata as privacyMeta } from "@/app/(marketing)/privacy/page";
import { metadata as termsMeta } from "@/app/(marketing)/terms/page";
import { PUBLIC_ROUTES } from "@/lib/seo";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "../src/app");

/** The indexable public pages and their resolved metadata. */
const PUBLIC_PAGES = [
  { route: "/", meta: marketingRoot },
  { route: "/about", meta: aboutMeta },
  { route: "/privacy", meta: privacyMeta },
  { route: "/terms", meta: termsMeta },
] as const;

describe("Marketing_Surface — unique titles & descriptions (Req 1.4, 1.5)", () => {
  it("gives every public page a title unique across the surface", () => {
    const titles = PUBLIC_PAGES.map((p) => String(p.meta.title));
    expect(new Set(titles).size).toBe(titles.length);
  });

  it("gives every public page a description unique across the surface", () => {
    const descriptions = PUBLIC_PAGES.map((p) => String(p.meta.description));
    expect(new Set(descriptions).size).toBe(descriptions.length);
  });

  it("keeps every title ≤ 60 and description ≤ 155 chars (Req 1.2, 1.3)", () => {
    for (const { meta } of PUBLIC_PAGES) {
      expect(String(meta.title).length).toBeLessThanOrEqual(60);
      expect(String(meta.description).length).toBeLessThanOrEqual(155);
    }
  });
});

describe("Marketing_Surface — every PUBLIC_ROUTES entry resolves to a page (Req 6.2)", () => {
  // Map a root-relative public route to its App Router page file under
  // `(marketing)`. `/` is served by the group's `page.tsx`.
  function pageFileFor(route: string): string {
    const segment = route === "/" ? "" : route.replace(/^\//, "");
    return path.join(appRoot, "(marketing)", segment, "page.tsx");
  }

  it.each(PUBLIC_ROUTES)("route %s has a rendering page.tsx", (route) => {
    expect(fs.existsSync(pageFileFor(route))).toBe(true);
  });
});

describe("Metadata-API-only — no hand-placed head markup in (marketing) (Req 10.2, 10.3)", () => {
  function collectTsx(dir: string): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) out.push(...collectTsx(full));
      else if (entry.name.endsWith(".tsx")) out.push(full);
    }
    return out;
  }

  const marketingFiles = collectTsx(path.join(appRoot, "(marketing)"));

  // Hand-placed head/meta/canonical/social markup is a rejected pattern: all
  // metadata must flow through the Metadata API (`seo.md` anti-patterns).
  const BANNED = [
    /<head[\s>]/,
    /<meta[\s>]/,
    /<link\s+rel=["']canonical/,
    /property=["']og:/,
    /name=["']twitter:/,
  ] as const;

  // Strip block comments (incl. JSX `{/* ... */}`) and inline backtick code
  // spans so a docstring that *mentions* `<head>`/`<meta>` (as these files do,
  // explaining the Metadata-API-only rule) is not mistaken for real markup.
  function stripCommentsAndCodeSpans(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/`[^`]*`/g, "");
  }

  it.each(marketingFiles.map((f) => path.relative(appRoot, f)))(
    "%s contains no hand-placed head/meta/canonical/social tag",
    (rel) => {
      const source = stripCommentsAndCodeSpans(
        fs.readFileSync(path.join(appRoot, rel), "utf8"),
      );
      for (const pattern of BANNED) {
        expect(source).not.toMatch(pattern);
      }
    },
  );
});

describe("Metadata-API-only — (app)/(auth) never import the marketing SEO helpers (Req 10.4; ADR 0006/0007)", () => {
  function collectTs(dir: string): string[] {
    const out: string[] = [];
    if (!fs.existsSync(dir)) return out;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) out.push(...collectTs(full));
      else if (/\.tsx?$/.test(entry.name)) out.push(full);
    }
    return out;
  }

  const guardedFiles = [
    ...collectTs(path.join(appRoot, "(app)")),
    ...collectTs(path.join(appRoot, "(auth)")),
  ].map((f) => path.relative(appRoot, f));

  const SEO_IMPORT = /from\s+["']@\/lib\/seo/;

  it("finds at least one (app)/(auth) source file to guard", () => {
    expect(guardedFiles.length).toBeGreaterThan(0);
  });

  it.each(guardedFiles)("%s does not import @/lib/seo", (rel) => {
    const source = fs.readFileSync(path.join(appRoot, rel), "utf8");
    expect(source).not.toMatch(SEO_IMPORT);
  });
});
