# Non-indexing of PII surfaces as a privacy control

## Introduction

This document explains why some pages must be kept out of search engines on
purpose, and the layered controls that enforce that. The data being protected is
Personally Identifiable Information (PII) — data that identifies a specific
person, such as an email address or the text of an uploaded resume. A search
engine discovers pages by crawling (following links and reading pages) and
indexing (storing them so they can be returned in search results). Search Engine
Optimization (SEO) is the practice of making public pages easy to discover that
way — but the same machinery, pointed at a private page, would publish someone's
resume to the world. This topic sits in the Security track because non-indexing
of private pages is a privacy control, not merely a marketing setting.

**Learning outcomes** — after reading this document you will be able to:

- Explain why keeping authenticated pages out of search results is a privacy concern and not only a search-ranking one. Indexed private data is exposed data.
- Describe the difference between a per-page directive, a response header, and a site-wide crawl rule. Each blocks indexing at a different point.
- Explain why several overlapping controls are used instead of relying on one. Layered controls are defense in depth.
- Recognise the common mistakes that accidentally expose a private page and recover from them. One missed control can leak everything.

Prerequisites:

- [Security headers and what each one defends against](05-security-01-security-headers-explained.md) — introduces the response-header mechanism this document reuses to mark a response non-indexable.
- [The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md) — introduces the routing model and the metadata mechanism used to set per-page indexing directives.

## Problem it solves

A web application has two populations of pages. Public pages — a landing page,
pricing, documentation — should be found in search results, because being
discoverable is the whole point. Authenticated pages — a user's uploaded resume,
their job descriptions, their match results — hold private data and should never
appear in search results, because indexing them would broadcast one person's
private information to anyone who searches. The concrete problem is enforcing that
split reliably, so that effort to make public pages discoverable never spills over
and exposes a private one.

The tempting but wrong assumption is that requiring a login is enough — that
because a page sits behind authentication, a crawler cannot reach it. That breaks
down in practice: a link to a private page can leak, a page can be briefly
reachable during a misconfiguration, and a crawler that does reach it will index
whatever it sees. Authentication controls who can open a page in a browser; it
does not, on its own, tell a crawler "do not store this".

The solution is to state non-indexing explicitly and redundantly. The application
marks each private page as not-to-be-indexed in more than one way, so that even if
one control is missing on one route, another still keeps the page out of the
index. Search-discoverability work is confined strictly to the public pages and is
never applied to the private ones.

## Mental model

Think of a building with public exhibition halls and private offices. The public
halls have signs in the lobby and entries in the visitor brochure, because the
owners want visitors to find them. The private offices do the opposite, with
three overlapping measures: each office door carries a "staff only — do not
photograph" notice, a guard stamps every document leaving those offices with "not
for publication", and the building's printed directory omits the offices
entirely. A photographer who wanders in still gets the message from the door
notice; one who only reads the directory never learns the offices exist.

For a private web page, the overlapping controls work like this:

1. The page itself declares a "do not index, do not follow links" directive through the framework's metadata, so a crawler reading the page is told to skip it.
2. The server stamps a "do not index, do not follow" instruction onto the response as a header, so even a non-page response such as raw data carries the same instruction.
3. The site-wide crawl-rules file tells crawlers not to visit the private paths at all.
4. The site map — the list of pages the site actively invites crawlers to index — omits every private path, listing public pages only.
5. Search-discoverability metadata (titles, descriptions, social previews) is added only to public pages, never to private ones.

Steps 1 through 4 are deliberately redundant: each one alone would mostly work,
but together they ensure a single oversight does not expose a private page.

## How it works

There are three distinct mechanisms, applied at three different points, and they
reinforce each other.

The first is a per-page directive. A page can carry instructions, expressed
through the framework's metadata system, telling a crawler not to index this page
and not to follow its links. When a layout shared by a whole group of private
pages declares that directive, every page nested under it inherits the same
instruction without each page repeating it. This is the page saying, in its own
content, "skip me".

The second is a response header. The same not-to-be-indexed instruction can be
attached as a named field on the response itself rather than inside the page
content. This matters because not every response is a page a crawler would parse
for an embedded directive — a response that returns raw data has no place to put
page metadata, but it can still carry a header. Stamping the header on every
response from the private data surface covers those cases, including error
responses, as long as the code that sets it runs on the way out for every status.

The third is site-wide crawl guidance. A site publishes a small file of rules
telling well-behaved crawlers which paths they may visit, and a site map listing
the pages it wants indexed. Private paths are listed as disallowed in the rules
file and omitted from the site map. The site map is best built as an explicit
allowlist — it lists only the public paths, so a new private route is excluded by
default rather than needing to be remembered and removed.

Two principles tie these together. The first is defense in depth: no single
control is trusted alone, because each can be missed on some route, so the same
private page is protected by the page directive, the header, and the crawl rules
at once. The second is default-deny for classification: when it is not obvious
whether a new route is public or private, it is treated as private, because the
cost of wrongly exposing private data is far higher than the cost of a public page
being momentarily harder to find. Authentication is never treated as a substitute
for any of these; it is a separate concern that controls access, while these
controls govern indexing.

## MatchLayer Phase 1 usage

The authenticated route group's shared layout at
`apps/web/src/app/(app)/layout.tsx` exports the per-page directive, which every
nested authenticated route inherits:

Source: `apps/web/src/app/(app)/layout.tsx`

```typescript
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};
```

On the backend, an interceptor in
`apps/api/src/matchlayer_api/core/middleware.py` stamps the response header. The
header value and the path prefix it applies to are fixed constants:

Source: `apps/api/src/matchlayer_api/core/middleware.py`

```python
_X_ROBOTS_TAG_VALUE: Final[bytes] = b"noindex, nofollow"
```

Source: `apps/api/src/matchlayer_api/core/middleware.py`

```python
_API_PATH_PREFIX: Final[str] = "/api/v1/"
```

The site-wide crawl rules in `apps/web/src/app/robots.ts` disallow the
private and data paths:

Source: `apps/web/src/app/robots.ts`

```typescript
return {
  rules: {
    userAgent: "*",
    disallow: ["/api/", "/upload", "/matches", "/library", "/dashboard"],
  },
};
```

The site map in `apps/web/src/app/sitemap.ts` uses an explicit allowlist of
public paths, so any route not named is excluded by default:

Source: `apps/web/src/app/sitemap.ts`

```typescript
const PUBLIC_ROUTES = ["/"] as const;
```

Together these four files realise the layered model: the route-group layout
declares the per-page directive, the backend interceptor stamps the response
header on the data surface, the crawl-rules file disallows the private paths, and
the site map lists only public routes. The response-header mechanism is the same
one introduced in the security-headers prerequisite linked in the Introduction.

## Common pitfalls

- **Mistake:** Relying on authentication alone to keep a private page out of search results.
  **Symptom:** A link to the page leaks or the page is briefly reachable, a crawler reaches it, and private content is indexed despite the login requirement.
  **Recovery:** Add the explicit non-indexing controls — the per-page directive, the response header, and the crawl-rules disallow — so indexing is blocked regardless of access control.

- **Mistake:** Building the site map as a denylist that excludes known private routes instead of an allowlist that includes only public ones.
  **Symptom:** A newly added private route is not in the exclusion list, so it silently lands in the site map and is offered to crawlers.
  **Recovery:** Make the site map an explicit allowlist of public paths, so a new route is excluded by default until it is consciously classified public.

- **Mistake:** Setting the non-indexing header only on successful responses and not on error responses.
  **Symptom:** A private data endpoint returns an error that still contains identifying detail, and that error response carries no non-indexing header.
  **Recovery:** Apply the header in an interceptor that runs on the way out for every response, so it lands on success and error responses alike.

## External reading

- [Google Search Central: block search indexing with noindex](https://developers.google.com/search/docs/crawling-indexing/block-indexing)
- [Google Search Central: introduction to robots.txt](https://developers.google.com/search/docs/crawling-indexing/robots/intro)
- [MDN Web Docs: X-Robots-Tag header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Robots-Tag)
- [Next.js: robots.txt file convention](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/robots)
- [Next.js: sitemap.xml file convention](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/sitemap)
