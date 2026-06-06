# The Next.js proxy and security response headers

## Introduction

This document explains how a web framework attaches protective instructions to
every response it sends to the browser, and where that logic lives in the
current version of the framework. The framework is Next.js (a framework for
building web applications with React, the JavaScript library for building user
interfaces). The protective instructions are security response headers (named
fields sent alongside a page that tell the browser how to behave safely — for
example, which sources of script to trust). The place this logic runs is a
request interceptor that the framework calls a "proxy"; in earlier versions the
same interceptor was called "middleware", and this document explains that
rename.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a response header is and why certain headers harden a page against common web attacks. A header is a small instruction the browser obeys before or while rendering.
- Describe what a request interceptor does and why running header logic there covers every route at once. One interceptor sits in front of all responses.
- Read the interceptor code that sets a content-security policy and related headers, and tell which routes it runs on. The matcher configuration states the route scope.
- Recognise the file-convention rename from the old name to the new one, and recover from the common mistakes around header policies. The current name is the one the framework looks for on disk.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
which introduces the framework, its routing model, and the server-versus-browser
split that an interceptor sits in front of.

## Problem it solves

A web page is vulnerable to a family of browser-side attacks unless it tells the
browser how to defend itself. The clearest example is script injection: if an
attacker can slip a script into the page, the browser will run it unless the
page has declared which scripts it trusts. Other risks include the page being
embedded inside a hostile site to trick a reader (clickjacking), and the browser
guessing a file's type and treating data as executable code. The concrete
problem is: how do you attach the right defensive instructions to every single
response — pages, data endpoints, everything — without remembering to do it by
hand in each one?

The common prior approach set these headers per route, or in server
configuration far away from the application code. That scattered the policy, let
new routes ship without it, and made the rules hard to review in one place. Some
applications omitted the headers entirely and relied on the framework's defaults,
which are intentionally permissive.

A single request interceptor solves this. It runs in front of responses for a
configured set of routes, so one piece of code can stamp the same defensive
headers onto everything the application serves, and a reviewer has exactly one
file to audit.

## Mental model

Think of the interceptor as a mailroom that every outgoing package passes
through before it leaves the building. The mailroom does not write the contents
of any package; it only stamps each one with the same set of safety labels —
"do not open inside another box", "only trust contents from these senders" —
before it goes out the door.

When a request arrives, the flow is:

1. The browser requests a route, and the framework checks whether that route falls within the interceptor's configured scope.
2. If it does, the framework runs the interceptor, handing it the incoming request and a handle to the response that will be sent.
3. The interceptor decides which headers apply — some go on every matched response, some only on a subset such as private or data routes.
4. The interceptor sets those headers on the response and returns it.
5. The framework finishes building the actual page or data and sends it with the stamped headers attached, so the browser enforces the policy as it renders.

Step 3 is where judgement lives: a content-security policy goes everywhere,
while a "do not index this" instruction is added only to private routes.

## How it works

A request interceptor is a function the framework runs in front of matching
responses, before the response reaches the browser. It receives the incoming
request and produces (or augments) the response. Because it sits in front of a
configured set of routes rather than inside any one page, it is the natural home
for cross-cutting concerns such as security headers that should apply uniformly.

The most important header it sets is the content-security policy — a single
header that lists which sources the browser may load scripts, styles, images,
and fonts from. By naming an explicit allowlist (for example, "only run scripts
served from this same site"), the policy stops the browser from executing an
injected script that came from anywhere else. A related set of smaller headers
hardens other surfaces: one forbids the page from being embedded in a frame on
another site (defeating clickjacking), one tells the browser never to guess a
response's content type, one controls how much address information is shared
when the reader navigates away, and one disables device capabilities the page
does not use, such as the camera or microphone. Over secure connections, a
strict-transport header tells the browser to refuse plain, unencrypted
connections to the site in future.

A subtlety is that some headers should not apply everywhere. A directive that
asks search engines not to index a response belongs only on private,
data-bearing, or authentication routes — never on the public landing page that
should be discoverable. So the interceptor inspects the request path and adds
that particular header only when the path falls into a non-public class.

The interceptor's scope is declared in a matcher — a configuration value listing
which request paths it should run on. A typical matcher covers all paths except
the framework's internal static assets, where running the interceptor would burn
work without protecting any reader-facing page.

A note on naming: in a recent major version, this framework renamed the
on-disk file convention for this interceptor. The function and its file used to
be called "middleware"; they are now called "proxy". The behaviour, the matcher
configuration, and the request and response types are unchanged — only the file
name and the exported function name changed. The framework looks for the new
name on disk, so an interceptor still named by the old convention is the most
common reason the headers silently stop being applied after an upgrade.

## MatchLayer Phase 1 usage

In MatchLayer the interceptor lives at `apps/web/src/proxy.ts`. The filename and
the exported function are the **current** `proxy` name — the Next.js 16 rename
from the older `middleware` convention. The file's own header comment documents
that rename explicitly:

Source: `apps/web/src/proxy.ts`

```typescript
 * Next.js 16 renamed the `middleware` file convention to `proxy` (see
 * https://nextjs.org/docs/app/api-reference/file-conventions/proxy and the
 * `@next/codemod middleware-to-proxy` migration). The file lives at
 * `apps/web/src/proxy.ts` and exports a function named `proxy`; behavior,
 * `config.matcher` semantics, and the `NextRequest`/`NextResponse` imports
 * are unchanged from the prior `middleware` convention.
```

The exported function receives the request, creates the response, and sets the
content-security policy plus the hardening headers on it:

Source: `apps/web/src/proxy.ts`

```typescript
export function proxy(request: NextRequest): NextResponse {
  const response = NextResponse.next();
```

The hardening headers are set on that response object:

Source: `apps/web/src/proxy.ts`

```typescript
response.headers.set("X-Content-Type-Options", "nosniff");
response.headers.set("X-Frame-Options", "DENY");
response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
response.headers.set(
  "Permissions-Policy",
  "camera=(), microphone=(), geolocation=()",
);
```

The "do-not-index" header is applied only to the private, authentication, and
data routes, never the public landing page, by checking the request path first:

Source: `apps/web/src/proxy.ts`

```typescript
if (isNoIndexPath(request.nextUrl.pathname)) {
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
}
```

Finally the matcher declares the scope — every path except the framework's
internal static-asset and image endpoints and the favicon:

Source: `apps/web/src/proxy.ts`

```typescript
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

## Common pitfalls

- **Mistake:** Leaving the interceptor file under the old `middleware` name after upgrading to the framework version that renamed it to `proxy`.
  **Symptom:** Every security header silently disappears — the page still renders, but the browser receives none of the protective headers because the framework no longer finds the interceptor.
  **Recovery:** Rename the file and the exported function to the current `proxy` convention (the upgrade ships a codemod that does this automatically), then confirm the headers reappear on a response.

- **Mistake:** Writing a content-security policy that omits a source the application genuinely needs, such as the local data endpoint it calls.
  **Symptom:** Parts of the page fail to load or calls are blocked, and the browser console logs content-security-policy violation messages naming the blocked source.
  **Recovery:** Add the legitimately needed source to the matching policy directive — and only that source — rather than disabling the policy wholesale.

- **Mistake:** Applying the "do-not-index" header to every route, including the public landing page.
  **Symptom:** The marketing page stops appearing in search results because it is being told not to be indexed, defeating the point of a public page.
  **Recovery:** Restrict the index-blocking header to the private, authentication, and data route classes, and exclude the public landing page from that set.

- **Mistake:** Sending the strict-transport (force-HTTPS) header over plain, unencrypted local connections during development.
  **Symptom:** The local browser starts refusing plain connections to the dev host and caches that refusal, making local development confusing to undo.
  **Recovery:** Emit the strict-transport header only on secure connections, guarded by a check on the request's protocol, so it never applies in plain-HTTP development.

## External reading

- [Next.js: the proxy file convention](https://nextjs.org/docs/app/api-reference/file-conventions/proxy)
- [MDN Web Docs: Content-Security-Policy header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy)
- [MDN Web Docs: Strict-Transport-Security header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Strict-Transport-Security)
- [MDN Web Docs: X-Content-Type-Options header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Content-Type-Options)
