# Security headers and what each one defends against

## Introduction

This document explains the small set of named instructions a web server can
attach to every page it sends, and exactly what attack each one blocks. A
security header is a named field sent alongside a web page (for example, a field
telling the browser which sources of code it is allowed to run) that the browser
reads and enforces before or while it renders the page. The page itself is
written in Hypertext Markup Language (HTML), the tag-based language browsers turn
into a visible document. The web delivers pages over the Hypertext Transfer
Protocol (HTTP), the request-and-response protocol of the web, and these headers
ride along with each HTTP response. Six headers do most of the work in a modern
application. They are a Content Security Policy (CSP), a directive named HTTP
Strict Transport Security (HSTS), and four smaller hardening headers. This topic
sits in the Security track because these headers are the browser-facing half of
the project's defense against script injection, connection downgrade, and clickjacking.

**Learning outcomes** — after reading this document you will be able to:

- Explain what each of the six headers instructs the browser to do, in one sentence per header. Each header is a single named rule the browser obeys.
- Describe the specific attack each header is designed to stop, such as script injection or clickjacking. The defense is concrete, not decorative.
- Read the code that sets these headers and tell which value applies to which header. The values are plain strings the browser parses.
- Recognise the common mistakes that silently weaken or disable a header, and recover from them. A weakened header looks fine until it is tested.

Prerequisites:

- [The Next.js proxy and security response headers](02-frontend-08-nextjs-proxy-security-headers.md) — introduces the request interceptor where these headers are set and the framework rename that governs the file's name.

## Problem it solves

A browser is a powerful, trusting machine. Left to its defaults, it will run any
script the page contains, load the page inside a frame on any other site, guess
the type of an ambiguous response and act on its guess, and happily talk to a
server over an unencrypted connection. Each of those default behaviours is an
attack surface. The concrete problem is: how does a server tell the browser to
be stricter — to run only trusted scripts, refuse to be framed, never guess a
content type, and never downgrade to an unencrypted connection — without
rewriting the browser?

Before these headers existed (or when a team forgets them), the page relies
entirely on the browser's permissive defaults. An attacker who can inject a
single `<script>` tag — through a comment field, a query parameter reflected
into the page, or a compromised third-party include — gets their code executed
with the full authority of the site. A page with no framing rule can be loaded
invisibly inside an attacker's site and have its clicks hijacked. A response
whose type the browser guesses can be coaxed into executing data as code.

Security headers solve this by letting the server declare, per response, a set
of rules the browser must follow. The rules travel with the page, so the browser
enforces them at exactly the moment it matters — while it is building and running
the page.

## Mental model

Think of a parcel arriving at a careful household. The parcel (the web page)
comes with a printed instruction card taped to the outside (the headers). Before
anyone opens the box, they read the card: "only accept ingredients from these
named suppliers", "do not open this inside another container", "do not guess
what is inside — trust only the stated label", "if this did not arrive by the
secure courier, refuse it next time". The people inside follow the card to the
letter, so a tampered parcel is caught by the rules on the card rather than by
luck.

When the browser receives a response, it processes the headers in this order:

1. It reads the policy header that lists which sources of script, style, image, and font it may load, and refuses anything not on those lists.
2. It checks the framing header and refuses to display the page inside a frame if the header forbids it.
3. It reads the content-type-options header and stops guessing the type of any response, trusting only the declared type.
4. It reads the referrer and permissions headers to decide how much address information to share and which device features to switch off.
5. On a secure connection, it reads the strict-transport header and remembers to refuse any future unencrypted connection to the same site.

Because the rules arrive with every response, step 1 through step 5 run again on
every page, so a single missed header on a single route is the only gap an
attacker needs.

## How it works

Each header is a single line the browser parses into a rule. The most important
is the Content Security Policy. It is one header carrying a list of directives,
each naming a resource type (script, style, image, font, network connection) and
an allowlist of sources that type may come from. When the policy says scripts may
load only from the page's own origin, the browser refuses to execute a script
injected from anywhere else — which is the core defense against Cross-Site
Scripting (XSS), the class of attack where an adversary gets their script to run
on someone else's page. A directive value of `'self'` means "the same origin as
this page"; an explicit source list means "these and nothing else".

The strict-transport directive, sent only over an already-secure connection,
tells the browser to refuse plain unencrypted connections to the site for a
stated number of seconds. After the browser sees it once, an attacker can no
longer downgrade a visitor to an interceptable connection, because the browser
itself rejects the unencrypted attempt before any request leaves the device. It
is sent only over a secure connection because a browser ignores it otherwise, and
sending it during local unencrypted development would teach the browser to refuse
the developer's own machine.

The four smaller headers each close one specific gap. A content-type-options
value of `nosniff` tells the browser to stop "sniffing" — guessing — a response's
type and to trust only the declared type, which stops a data file from being
reinterpreted as executable script. A frame-options value of `DENY` forbids the
page from being embedded in a frame on any site, defeating clickjacking, where a
victim is tricked into clicking a hidden framed page. A referrer policy controls
how much of the current address is sent to the next site the reader visits,
limiting how much an external site learns about where the reader came from. A
permissions policy switches off device capabilities the page does not use — the
camera, microphone, and location sensors — so a compromised page cannot reach for
them.

A subtlety shared by all of these is uniformity: the protection is only as good
as its coverage. A header set on the landing page but missing on a data endpoint
leaves that endpoint exposed, so the headers are best applied in one shared place
that runs in front of every response rather than re-declared per page.

## MatchLayer Phase 1 usage

The headers are set in the request interceptor at `apps/web/src/proxy.ts`. The
Content Security Policy is assembled as a list of directives joined into one
header value — scripts and styles are limited to the page's own origin, and the
only cross-origin network destination allowed is the local backend during
development:

Source: `apps/web/src/proxy.ts`

```typescript
response.headers.set(
  "Content-Security-Policy",
  [
    "default-src 'self'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    scriptSrc,
    "font-src 'self' data:",
    "connect-src 'self' http://localhost:8000",
  ].join("; "),
);
```

The four hardening headers are set immediately after, each as a single named
value:

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

The strict-transport header is guarded by a check on the request protocol, so it
is emitted only on a secure connection and never during plain local development:

Source: `apps/web/src/proxy.ts`

```typescript
if (request.nextUrl.protocol === "https:") {
  response.headers.set(
    "Strict-Transport-Security",
    "max-age=31536000; includeSubDomains; preload",
  );
}
```

The policy in Phase 1 deliberately allows inline styles and inline scripts
because the framework's runtime injects them for hydration; tightening that to a
per-request token is tracked for a later phase. The detail of how the interceptor
runs and which routes it covers lives in the prerequisite document linked in the
Introduction.

## Common pitfalls

- **Mistake:** Writing a Content Security Policy that omits a source the page genuinely needs, such as the backend the page calls for data.
  **Symptom:** Parts of the page fail to load or network calls are blocked, and the browser console prints content-security-policy violation messages naming the blocked source.
  **Recovery:** Add only the specific missing source to the matching directive (for example, the backend origin to the connection directive) rather than disabling the policy or widening it to allow everything.

- **Mistake:** Emitting the strict-transport header over a plain, unencrypted local connection during development.
  **Symptom:** The local browser starts refusing plain connections to the development host and caches that refusal, which is confusing to undo and blocks local work.
  **Recovery:** Guard the header behind a protocol check so it is sent only over a secure connection, exactly as the protocol check in the code above does.

- **Mistake:** Setting the headers on the page routes but forgetting the data or error responses.
  **Symptom:** A scan of the landing page looks clean, but a data endpoint or an error page returns none of the headers, leaving a real attack surface uncovered.
  **Recovery:** Set the headers in one shared interceptor that runs in front of every matched response, and confirm with a request to a data route that the headers are present there too.

## External reading

- [MDN Web Docs: Content-Security-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy)
- [MDN Web Docs: Strict-Transport-Security](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Strict-Transport-Security)
- [MDN Web Docs: X-Frame-Options](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Frame-Options)
- [MDN Web Docs: X-Content-Type-Options](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Content-Type-Options)
- [MDN Web Docs: Permissions-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Permissions-Policy)
- [Next.js: the proxy file convention](https://nextjs.org/docs/app/api-reference/file-conventions/proxy)
