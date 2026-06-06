# CORS allowlists and why wildcards are unsafe

## Introduction

This document explains the browser rule that governs which websites may read
responses from a given server, and how a server safely opts specific partners
into that access. The rule is Cross-Origin Resource Sharing (CORS) — a browser
mechanism that decides whether code running on one origin (a scheme, host, and
port combination such as `https://app.example.com`) is allowed to read a
response from a different origin. An allowlist is an explicit, finite list of the
origins a server chooses to trust. The opposite of an allowlist is a wildcard, a
single `*` value meaning "any origin at all". This topic sits in the Security
track because a careless CORS configuration is one of the easiest ways to leak
authenticated data to a hostile site.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an origin is and why a browser treats a request between two different origins as a cross-origin request. Two origins differ if their scheme, host, or port differ.
- Describe how a server uses response headers to tell the browser which origins may read a response. The server names the trusted origins explicitly.
- Explain why a wildcard origin combined with credentials is dangerous on an authenticated endpoint. The combination would let any site read private data.
- Recognise the common configuration mistakes and recover from them. Most CORS bugs are a single mismatched string.

Prerequisites:

- [The FastAPI application factory](03-backend-01-fastapi-application-factory.md) — introduces the backend application object and the middleware layer where the cross-origin rules are configured.

## Problem it solves

Browsers enforce a long-standing safety rule called the same-origin policy: by
default, code loaded from one origin may send a request to another origin but may
not read the response. That default protects users — without it, a malicious page
open in one tab could quietly call a bank's website (reusing the visitor's logged-in
session) and read the private response. But the default is too strict for
legitimate setups, such as a single-page front end on one origin that needs to
read data from an application programming interface (API) — a server endpoint
that returns data rather than a page — on a different origin.

Before CORS, teams worked around the same-origin policy with fragile tricks:
routing every call through a same-origin proxy, or abusing script tags to smuggle
data. These approaches were hard to secure and easy to get wrong. There was no
standard, browser-enforced way for a server to say "this specific other origin is
allowed to read my responses, and no one else".

CORS solves this by giving the server a vocabulary of response headers that tell
the browser exactly which foreign origins may read a response, whether
credentials such as cookies may be attached, and which methods and headers are
permitted. The browser does the enforcing; the server only declares the policy.

## Mental model

Think of a private members' club with a guest list at the door. A visitor (the
browser, acting for some website) shows up and says which club they came from
(their origin). The doorkeeper checks that name against the guest list. If the
name is on the list, the visitor is allowed in and may take things home; if not,
they are turned away at the door. A wildcard policy is the doorkeeper deciding to
admit literally anyone who shows up — which is fine for a public lobby with
nothing private in it, but reckless for a room holding members' confidential
files.

When a browser makes a cross-origin request, the exchange runs like this:

1. The browser attaches an `Origin` header naming the website the request came from.
2. For certain requests the browser first sends a lightweight "preflight" check asking whether the real request would be allowed.
3. The server compares the `Origin` value against its configured allowlist and, if it matches, returns a header echoing that origin as permitted.
4. The browser sees its own origin echoed back and lets the page read the response; if the header is absent or names a different origin, the browser blocks the read.
5. If credentials are involved, the server must additionally signal that credentialed access is allowed, and the permitted origin must be a specific name — never the wildcard.

Step 5 is the crux of this document: the browser deliberately refuses to combine
"any origin" with "send the cookies", because that pairing would expose every
user's private data to every website.

## How it works

An origin is the triple of scheme, host, and port. Two addresses share an origin
only when all three match; differ in any one and the browser treats a request
between them as cross-origin and applies the sharing rules. The server expresses
its policy through a small family of response headers. The central one names the
single origin the server is willing to share this particular response with. The
browser compares that returned name against the requesting page's own origin and
permits the read only on an exact match.

A second header governs credentials — cookies, and other ambient authentication
the browser would otherwise attach automatically. Reading a response that was
fetched with credentials is allowed only when the server explicitly opts in with
a credentials-allowed header. Here the specification draws a hard line: when
credentials are allowed, the permitted-origin header must name a specific origin
and must not be the wildcard `*`. A browser that receives both the wildcard and
the credentials-allowed signal refuses the response outright. This is not a
quirk; it is the rule that prevents the most dangerous misconfiguration, because
a wildcard plus credentials would let any site on the internet make authenticated
requests on a logged-in user's behalf and read back the private results.

For requests that could change state or carry unusual headers, the browser sends
a preflight request first — an automatic `OPTIONS` request asking the server
which methods and headers it will accept from this origin. Only if the preflight
response approves does the browser send the real request. This is why a server's
CORS configuration also lists the allowed methods and headers: the preflight
consults that list.

The safe pattern, therefore, is an allowlist: the server holds a finite, reviewed
set of origins it trusts — typically one per deployment environment — and echoes
back only an incoming origin that appears in that set. An origin absent from the
set gets no permission header, and the browser blocks the read. The wildcard
exists for genuinely public, unauthenticated resources where there is nothing to
protect; it has no place on an endpoint that returns data tied to a user's
session.

## MatchLayer Phase 1 usage

The backend reads its allowlist from a typed setting in
`apps/api/src/matchlayer_api/config.py`. The field is a list of validated
Uniform Resource Locator (URL) values, not a free-form string, and it defaults
to an empty list rather than a wildcard:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    cors_allowed_origins: Annotated[list[AnyHttpUrl], NoDecode] = Field(default_factory=list)
```

The application factory in `apps/api/src/matchlayer_api/main.py` installs the
cross-origin middleware using that list. Credentialed access is enabled, and the
allowed origins come straight from the configured allowlist — there is no code
path that produces a wildcard:

Source: `apps/api/src/matchlayer_api/main.py`

```python
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_format_cors_origins(cfg),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
```

The helper that builds the origin list documents the empty-list-is-not-wildcard
rule explicitly, and trims the trailing slash that the URL type appends so the
stored value matches the literal `Origin` header a browser sends:

Source: `apps/api/src/matchlayer_api/main.py`

```python
    return [str(origin).rstrip("/") for origin in settings.cors_allowed_origins]
```

Because credentialed access is on, the wildcard would be rejected by the browser
anyway — but the configuration never reaches a wildcard in the first place. The
allowlist is supplied per environment through configuration; the local default
admits only the front-end development server.

## Common pitfalls

- **Mistake:** Setting the allowed origin to the wildcard `*` while also allowing credentials, to "make CORS errors go away".
  **Symptom:** Either the browser blocks every credentialed response with a console error stating the wildcard cannot be used with credentials, or — if credentials are silently dropped — authenticated calls mysteriously fail to send cookies.
  **Recovery:** Replace the wildcard with an explicit allowlist of the specific origins that need access, and keep credentials enabled only for those named origins.

- **Mistake:** Listing an origin with a trailing slash (`https://app.example.com/`) when the browser sends it without one.
  **Symptom:** The request is rejected even though the origin "looks" correct, because the stored string does not match the `Origin` header byte for byte.
  **Recovery:** Store each origin as a bare scheme-host-port with no trailing path, exactly as the browser sends it, as the trimming helper above does.

- **Mistake:** Adding a new deployment environment's front-end origin to the code but forgetting to add it to the allowlist configuration.
  **Symptom:** The new environment's pages load but every data call is blocked by the browser, while older environments keep working.
  **Recovery:** Treat the allowlist as part of each environment's configuration and add the new origin there; confirm with a cross-origin request from the new front end.

## External reading

- [MDN Web Docs: Cross-Origin Resource Sharing (CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)
- [MDN Web Docs: Access-Control-Allow-Origin](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Access-Control-Allow-Origin)
- [MDN Web Docs: Same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Same-origin_policy)
- [FastAPI: CORS (Cross-Origin Resource Sharing)](https://fastapi.tiangolo.com/tutorial/cors/)
