# The authenticated route-group shell and the unauthenticated redirect

## Introduction

A web application usually has two kinds of pages: public ones that anyone may
open, and private ones that only a signed-in person may see. This document
explains the pattern that enforces that boundary for a whole group of private
pages at once — a shared shell that wraps every private route, checks whether
the visitor is signed in, and sends anyone who is not to the sign-in page. The
same shell also carries the instruction that keeps those private pages out of
search engines, because the pages behind it hold Personally Identifiable
Information (PII) — data that identifies a specific person, such as an email
address or the text of an uploaded resume.

A route group is a way to gather several routes under one shared layout without
adding a segment to their web addresses. The shared layout is the single place
where a cross-cutting concern — here, "is this visitor signed in?" and "do not
index this page" — is written once and applied to every route nested inside the
group.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a route group is and why a shared layout is the right place to gate access to many private pages at once. One gate protects every nested route.
- Describe the redirect-on-unauthenticated pattern and where the access check runs. The check runs before the private page renders.
- Explain why the same shell also declares a non-indexing directive and why an access check alone is not enough to keep private pages out of search results. Indexing and access control are separate concerns.
- Recognise the common mistakes that break the gate or leak a private page, and recover from them. Most failures come from misplacing the check or trusting one control alone.

Prerequisites:

- [The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md) — introduces the routing model, the shared-layout idea, and the server/client split this pattern depends on.
- [Server state and the useAuth hook](06-auth-08-tanstack-query-and-useauth.md) — introduces the client-side session state the shell falls back to.
- [Keeping private pages out of search engines](05-security-07-pii-non-indexing.md) — introduces the layered non-indexing controls this shell participates in.

## Problem it solves

The concrete problem is access control across many pages. A real application has
several private screens — a dashboard, an upload form, a results page, a library
of past results. Checking "is this person signed in?" inside every one of those
pages, by hand, is repetitive and fragile: the day someone adds a new private
page and forgets the check, that page is exposed.

A common earlier approach put the check in each page, or relied on browser-side
code to hide the page after it had already loaded. Both leak. A per-page check
is forgotten sooner or later. A browser-side-only guard renders the private
content first and removes it afterward, so the protected data is briefly present
in the browser and can be seen by anyone watching the network response.

There is a second, quieter problem: keeping these private pages out of search
engines. Search Engine Optimization (SEO) — the practice of making pages easy
for search engines to discover and rank — is something a public marketing page
wants. A private page wants the opposite, because indexing it would publish
someone's personal data to the world. Requiring a login does not, by itself,
tell a search crawler "do not store this page", so the application has to say so
explicitly.

The shared shell solves both at once. It runs the access check in one place
before any private page renders, and it declares the non-indexing directive once
so every nested private route inherits it.

## Mental model

Think of a members-only floor in a building. Instead of putting a guard at the
door of every office on that floor, the building puts one guard at the single
elevator lobby that leads to the floor. Everyone who wants to reach any office
must pass that one checkpoint. The guard also posts one notice at the lobby —
"private floor, do not photograph for the public directory" — that applies to
every office beyond it, so no individual office has to post its own.

When a visitor requests a private page, the shell handles it in this order:

1. The request arrives at the shared layout that wraps the private route group, before the requested page itself is rendered.
2. The layout checks for a valid session — evidence that the visitor has signed in — by inspecting the credentials the browser sent with the request.
3. If the check fails, the layout sends the visitor to the sign-in page instead of rendering the private page, and rendering stops there.
4. If the check succeeds, the layout renders the requested private page inside its shared chrome, and the visitor sees their content.
5. Independently of the access check, the layout declares a "do not index, do not follow" directive that every page nested under it inherits.

The single-checkpoint image is the whole idea: one shared layout enforces access
and non-indexing for every route in the group, so a new private page is
protected the moment it is placed inside the group.

## How it works

A route group is a routing construct: it groups several routes so they share a
layout without that grouping appearing in the web address. The routes inside the
group still answer their own addresses; the group only changes which layout
wraps them. The wrapping layout is rendered for every request to any route in
the group, which makes it the natural home for a rule that must apply to all of
them.

Two such rules live in this layout. The first is an access gate. Because the
layout runs before the page it wraps, it can decide whether the visitor is
allowed to see the page at all. It looks for a valid session — the proof, stored
in the request, that the visitor authenticated earlier — and branches on the
result. When the visitor is authenticated, the layout renders the page. When the
visitor is not, the layout issues a redirect: a response that tells the browser
to navigate to a different address, the sign-in page, rather than returning the
private content. Performing this check in the layout, before render, means the
private markup is never produced for an unauthenticated visitor.

The second rule is a non-indexing directive. The layout declares page metadata
saying "do not index this page, do not follow its links", and because layouts
nest, every route inside the group inherits that directive without repeating it.
This is deliberately separate from the access gate. Access control decides who
may open a page in a browser; the non-indexing directive tells a search crawler
not to store the page. A page can be both reachable by a logged-in user and
invisible to search engines, and private pages need both properties at once.

Where the access check runs matters. Running it on the server, before the page
is sent, is the strong form: the protected content never leaves the server for
an unauthenticated request. A purely browser-side check is weaker, because the
content has usually already been delivered before the browser can hide it. The
robust pattern therefore prefers a server-side check and falls back to a
browser-side one only when the server genuinely cannot see the visitor's
credentials — and even then it shows a neutral loading state rather than the
protected content until the decision resolves.

One subtlety is the redirect loop. If the server insists on redirecting whenever
it cannot see a session, but the visitor actually has a valid session the server
cannot observe, the visitor is bounced back and forth between the private page
and the sign-in page forever. The pattern avoids this by treating "cannot verify
on the server" as "let the browser decide" rather than "deny", so a real session
is never mistaken for a missing one.

## MatchLayer Phase 1 usage

In MatchLayer the private surface is the `(app)` route group, and its shared
layout lives at `apps/web/src/app/(app)/layout.tsx`. That layout is a Server
Component (a layout that runs only on the server and sends finished markup to the
browser), which is what lets it both export indexing metadata and run the access
check before any nested page renders.

The non-indexing directive is a single metadata export on the route-group
layout. Every nested authenticated route — the dashboard, the upload screen, the
results page, and the library — inherits it:

Source: `apps/web/src/app/(app)/layout.tsx`

```tsx
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};
```

This is the privacy control the project's
[SEO and indexing policy](../../../.kiro/steering/seo.md) requires: the `(app)`
route group is classified as an authenticated, PII-bearing surface, so it is
`noindex, nofollow`, excluded from the sitemap, and disallowed in the crawl
rules. The directive is a defense-in-depth measure that stands alongside the
access check — the page must never be indexed even though it also sits behind a
sign-in requirement.

The access gate runs in the same layout. It verifies the session server-side by
replaying the browser's refresh cookie against the authentication service before
rendering anything:

Source: `apps/web/src/app/(app)/layout.tsx`

```tsx
const session = await verifySessionFromRefreshCookie({
  headers,
  cookies,
});
```

The `verifySessionFromRefreshCookie` helper lives apart from the client auth
module, in `apps/web/src/lib/auth-server.ts`, so a Server Component can import
and call it. When it returns a session the layout renders the private chrome
with the server-acquired token; when it returns nothing, the decision is handed
to the client shell.

The redirect itself is performed by the client shell at
`apps/web/src/app/(app)/shell-client.tsx`. When the server could not verify the
session and the browser-side check also finds no session, it navigates to the
sign-in page, preserving where the visitor was headed:

Source: `apps/web/src/app/(app)/shell-client.tsx`

```tsx
if (resolved && !clientAuthed && !isLoading) {
  const next = encodeURIComponent(pathname || "/");
  router.replace(`/login?next=${next}`);
}
```

Splitting the decision this way keeps the strong server-side guarantee for the
same-origin production deployment while staying usable in split-origin local
development, where the refresh cookie is not visible to the server.

## Common pitfalls

- **Mistake:** Trusting the access gate alone to keep a private page out of search results, and skipping the non-indexing directive.
  **Symptom:** A link to a private page leaks or the page is briefly reachable, a crawler fetches it, and personal data shows up in search results even though the page requires a login.
  **Recovery:** Keep the non-indexing metadata on the route-group layout so every private page declares `noindex, nofollow` regardless of the access check, and confirm the route is excluded from the sitemap and crawl rules.

- **Mistake:** Repeating the access check and the indexing directive inside each private page instead of placing them once on the shared layout.
  **Symptom:** A newly added private page is missing the check or the directive, so it either renders for signed-out visitors or gets indexed, while its siblings behave correctly.
  **Recovery:** Move both rules to the route-group layout and delete the per-page copies, so every nested route inherits them and a new page is protected the moment it is added to the group.

- **Mistake:** Hard-redirecting on the server whenever the session cannot be verified there, without considering that a valid session may exist but be invisible to the server.
  **Symptom:** A freshly signed-in visitor bounces between the sign-in page and the private page in a loop and can never reach their content.
  **Recovery:** Treat "cannot verify on the server" as "defer to the browser" rather than "deny": fall back to a browser-side session check, show a neutral loading state while it runs, and redirect only after the browser also finds no session.

- **Mistake:** Putting the access gate in a browser-side component so the protected page renders before the check resolves.
  **Symptom:** Private content or app chrome flashes on screen for a moment before the redirect fires, exposing data to a signed-out visitor and to anyone watching the response.
  **Recovery:** Run the check in the server layout before render, and where a browser-side fallback is unavoidable, render a placeholder rather than the protected content until the session is confirmed.

## External reading

- [Next.js: route groups](https://nextjs.org/docs/app/api-reference/file-conventions/route-groups)
- [Next.js: the robots metadata file convention](https://nextjs.org/docs/app/api-reference/file-conventions/metadata/robots)
- [Next.js: layouts and pages](https://nextjs.org/docs/app/api-reference/file-conventions/layout)
- [Next.js: redirecting](https://nextjs.org/docs/app/building-your-application/routing/redirecting)
- [The Robots Exclusion Protocol specification](https://datatracker.ietf.org/doc/html/rfc9309)
