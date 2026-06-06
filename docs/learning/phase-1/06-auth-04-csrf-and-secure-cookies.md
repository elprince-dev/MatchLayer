# CSRF defense with the double-submit-cookie pattern and secure cookie attributes

## Introduction

This document explains how a web application stops a malicious website from
making your browser perform actions on a site you are signed in to, and how the
three security attributes on a cookie — `HttpOnly`, `Secure`, and `SameSite` —
narrow the ways a cookie can be stolen or misused. The attack being defended
against is Cross-Site Request Forgery (CSRF), a class of attack where a page you
did not trust tricks your browser into sending an authenticated request to a
site you did trust. The defense at the centre of this topic is the
double-submit-cookie pattern, a way to prove that a request was initiated by the
real site's own front-end code rather than forged by a third party.

A cookie is a small piece of data a server asks the browser to store and send
back automatically on later requests to the same site, and it travels over the
Hypertext Transfer Protocol (HTTP), the request-and-response protocol the web is
built on. That automatic resending is convenient for keeping a user signed in,
and it is also the exact behaviour CSRF abuses, which is why cookie-based
sessions need a dedicated defense.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a browser automatically attaching a cookie to every request is what makes CSRF possible. The browser sends the cookie without the page's code asking it to.
- Describe how the double-submit-cookie pattern proves a request came from the real site's own code. It relies on a value an attacker's page cannot read or replay.
- State what each of `HttpOnly`, `Secure`, and `SameSite` defends against and why no single one is sufficient alone. Each closes a different gap, so they are layered.
- Recognise the common implementation mistakes in cookie-based authentication and recover from them. Most come from confusing which cookie is the secret and which is the proof.

Prerequisites:

- [Signed tokens with PyJWT](06-auth-01-jwt-and-pyjwt.md) — covers the signed-token format the refresh cookie carries, referenced here without re-deriving it.
- [Refresh-token rotation and reuse detection](06-auth-03-refresh-token-rotation.md) — covers the refresh-token lifecycle whose transport this document secures.

## Problem it solves

A site that authenticates a user with a cookie has a built-in vulnerability that
has nothing to do with stealing the cookie. Once the browser holds a session
cookie for `bank.example`, it attaches that cookie to _every_ request bound for
`bank.example` — including requests triggered by a completely different site the
user happens to be visiting at the same time. So if an attacker hosts a page at
`evil.example` containing a hidden form that submits a state-changing request to
`bank.example/transfer`, the victim's browser dutifully attaches the
`bank.example` session cookie, and the bank's server sees a fully authenticated
request it has no way to distinguish from a real one. That is CSRF: the
attacker never reads the cookie, they only need the browser to send it.

The earliest cookie-authenticated applications had no defense for this at all;
any state-changing endpoint that trusted the session cookie was forgeable. The
first widespread fix was the synchronizer-token pattern: the server generates a
random token, stores it in the user's server-side session, embeds it in every
form it renders, and rejects any submission whose token does not match the
stored copy. That works, but it requires the server to keep per-session state
for the token and to inject the token into every rendered form — awkward for an
application whose front end is a separate program talking to the back end over
an interface rather than rendering server-side forms.

The double-submit-cookie pattern solves the same problem without the server
having to store the token. It moves the comparison to a place an attacker's page
structurally cannot reach, which is what the rest of this document unpacks.

## Mental model

Think of a members-only club that stamps your hand at the door and also prints
the same code on a paper ticket it hands you. To order at the bar you must show
both: the stamp on your hand and the code on your ticket, and they have to
match. Anyone can see you walk in (the door is public), but a pickpocket on the
street cannot reproduce the pairing — they would need to both copy the stamp on
your hand and write the matching code on a ticket, and they can do neither from
outside. The stamp-and-ticket pairing proves the order came from someone
actually standing inside the club.

In the web version, the "stamp on your hand" is a value the site's own
front-end code can read, and the "code on the ticket" is the same value echoed
back in a place only that code can set. A foreign page can cause your browser to
send a request, but it cannot read your site's cookie and cannot set the custom
header, so it cannot make the two halves match.

Walking through one protected request step by step:

1. On sign-in the server generates a random token and returns it to the browser in a cookie that the front-end code is allowed to read.
2. Before sending a state-changing request, the front-end code reads that token value out of the cookie.
3. The front-end code copies the token into a custom request header that it sets explicitly on the request.
4. The browser sends the request with two carriers of the token: the cookie (attached automatically) and the custom header (set by the code).
5. The server accepts the request only when the token in the cookie and the token in the header are present and equal; otherwise it rejects the request.

The detail newcomers miss is step 5's dependency on the same-origin policy: a
page on another origin can make your browser send the cookie, but the browser
will not let that page read your site's cookie value or attach a custom header
to a cross-site request, so the attacker can never supply a matching header.

## How it works

Two facts about browsers make this pattern work. First, cookies carry _ambient
authority_: the browser attaches a site's cookies to requests bound for that
site automatically, whether the request came from the site's own pages or from a
foreign page, and the code making the request does not have to do anything to
include them. Second, the _same-origin policy_ — the browser rule that isolates
content from different origins — prevents code running on one origin from
reading another origin's cookies through scripting and from attaching arbitrary
custom headers to cross-site requests. CSRF exploits the first fact; the
double-submit-cookie pattern leans on the second to defeat it.

The pattern uses one random token delivered two ways. The server issues a token
and sets it in a cookie that scripting _is_ allowed to read. The site's own
front-end code reads that value and sends it back on every state-changing
request as a custom request header. On the server, a check compares the token
from the cookie with the token from the header and proceeds only when both are
present and identical. A forged request from a foreign page will still carry the
cookie (ambient authority), but it cannot carry a matching header, because the
foreign page can neither read the cookie value to learn the token nor set the
custom header on a cross-site request. The two copies therefore disagree, and
the request is rejected.

The comparison must be done in _constant time_ — taking the same amount of time
regardless of how many leading characters match — so that an attacker cannot
measure response timing to recover the token one character at a time. A naive
character-by-character string equality can return early on the first mismatch,
which leaks how much of a guess was correct; a constant-time comparison removes
that signal.

Three cookie attributes harden the cookies themselves, each against a different
threat:

- `HttpOnly` tells the browser that scripting must not be able to read the cookie. A cookie marked this way is invisible to page code, so a script-injection flaw — Cross-Site Scripting (XSS), where an attacker gets their own code to run on your page — cannot read it out and exfiltrate it. The trade-off is that an `HttpOnly` cookie cannot be the one the double-submit pattern reads, so the readable token cookie and the secret session cookie must be two different cookies.
- `Secure` tells the browser to send the cookie only over Hypertext Transfer Protocol Secure (HTTPS), the encrypted form of HTTP. This keeps the cookie off any plaintext connection where a network eavesdropper could capture it. During local development over plain HTTP the attribute is typically disabled, because a `Secure` cookie would never be sent and sign-in would silently fail.
- `SameSite` tells the browser whether to attach the cookie to requests that originate from other sites. `SameSite=Strict` withholds the cookie from every cross-site request; `SameSite=Lax` withholds it from cross-site sub-requests (such as a hidden form post) while still sending it on top-level navigations the user initiates; `SameSite=None` sends it on all cross-site requests and is only honoured together with `Secure`. A restrictive `SameSite` value is itself a strong first-line CSRF defense, because it stops the cookie from being attached to the very cross-site requests CSRF depends on.

No single attribute is a complete defense. `SameSite` reduces cross-site cookie
sending but older browsers and certain navigation flows weaken it; `HttpOnly`
protects against script theft but does nothing about ambient authority; `Secure`
protects the wire but not the application logic. Layering the double-submit
check on top of restrictive cookie attributes is defense in depth: each control
covers a gap the others leave open.

## MatchLayer Phase 1 usage

In the Phase 1 backend, every cookie in the authentication surface is written by
a single module, `apps/api/src/matchlayer_api/core/security/cookies.py`, so the
attribute policy lives in exactly one place. The refresh-token cookie — the
sensitive credential — is set `HttpOnly` so page scripting can never read it,
`Secure` outside development so it never travels in plaintext, and
`SameSite=Lax`:

Source: `apps/api/src/matchlayer_api/core/security/cookies.py`

```python
def set_refresh_cookie(response: Response, *, value: str, max_age: int, settings: Settings) -> None:
    """Set the HttpOnly refresh-token cookie."""
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=value,
        max_age=max_age,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_is_secure(settings),
        samesite="lax",
    )
```

The CSRF token cookie is the readable half of the double-submit pair. It is set
with `httponly=False` on purpose, because the front end has to read its value to
echo it back; it is not a credential, only a proof-of-origin value:

Source: `apps/api/src/matchlayer_api/core/security/cookies.py`

```python
def set_csrf_cookie(response: Response, *, value: str, max_age: int, settings: Settings) -> None:
    """Set the non-HttpOnly CSRF cookie (readable by JS)."""
    response.set_cookie(
        key=_CSRF_COOKIE,
        value=value,
        max_age=max_age,
        path=_CSRF_COOKIE_PATH,
        httponly=False,
        secure=_is_secure(settings),
        samesite="lax",
    )
```

The two cookies are also scoped to different paths. The refresh cookie is pinned
to the auth path (so the browser only attaches the sensitive token on the two
endpoints that consume it, the refresh and logout routes), while the CSRF cookie
is scoped to the site root so the front end can read it from any page. The
`secure` flag on both is computed once by a helper that returns true whenever the
environment is not development, which is what allows local sign-in to work over
plain HTTP while production stays HTTPS-only.

The server-side half of the double-submit check is a dependency in
`apps/api/src/matchlayer_api/core/dependencies.py`. It runs on the
cookie-authenticated routes (wired in via `Depends(csrf_required)` on the
refresh and logout endpoints in `apps/api/src/matchlayer_api/auth/router.py`).
When no refresh cookie is present there is no cookie-derived authority to
protect, so the check is skipped; when the refresh cookie is present, both the
CSRF cookie and the `X-CSRF-Token` header must be present and equal under a
constant-time comparison:

Source: `apps/api/src/matchlayer_api/core/dependencies.py`

```python
async def csrf_required(request: Request) -> None:
    refresh_cookie = request.cookies.get("matchlayer_refresh")
    if not refresh_cookie:
        # Requirement 9.4 anchor: no cookie authority → no CSRF check.
        return

    cookie_csrf = request.cookies.get("matchlayer_csrf")
    header_csrf = request.headers.get("X-CSRF-Token")

    if not cookie_csrf or not header_csrf:
        raise CsrfMismatchError()

    if not secrets.compare_digest(cookie_csrf, header_csrf):
        raise CsrfMismatchError()
```

The `secrets.compare_digest` call is the constant-time comparison described in
the conceptual section: it does not short-circuit on the first differing
character, so it leaks no timing signal about the token.

The matching front-end half lives in `apps/web/src/lib/auth.ts`. Because the
CSRF cookie is not `HttpOnly`, the client can read it out of `document.cookie`
and echo it as the `X-CSRF-Token` header on the refresh and logout calls. A
helper reads the cookie value, returning `null` when it is absent:

Source: `apps/web/src/lib/auth.ts`

```typescript
function readCsrfCookie(): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const match = document.cookie.match(/(?:^|;\s*)matchlayer_csrf=([^;]+)/);
  if (!match || match[1] === undefined) {
    return null;
  }
  return decodeURIComponent(match[1]);
}
```

The client also sends its requests with credentials included so the browser
attaches the cookies, and reads the token through `document.cookie` — the same
surface the `HttpOnly` refresh cookie is deliberately hidden from. That split is
the whole design: the credential is unreadable to scripting, the proof-of-origin
value is readable by scripting, and a foreign origin can supply neither matching
half.

## Common pitfalls

- **Mistake:** Marking the CSRF token cookie `HttpOnly`, treating it like the session credential.
  **Symptom:** The front end reads an empty value from `document.cookie`, omits the `X-CSRF-Token` header, and every refresh or logout request is rejected with a CSRF-mismatch error even though the user is signed in.
  **Recovery:** Set the CSRF cookie with `HttpOnly` disabled; only the refresh-token cookie is `HttpOnly`. Confirm in browser dev tools that the CSRF cookie is visible to scripting and the refresh cookie is not.

- **Mistake:** Comparing the cookie token and header token with ordinary string equality.
  **Symptom:** The check passes functionally and tests look green, but the comparison returns early on the first mismatched character, exposing a timing side channel an attacker can use to recover the token byte by byte.
  **Recovery:** Compare the two tokens with a constant-time function so the comparison time does not depend on how many leading characters matched.

- **Mistake:** Hard-coding the `Secure` attribute as always-on, or always-off, instead of gating it on the environment.
  **Symptom:** Always-on breaks local sign-in over plain HTTP because the browser silently refuses to send the cookie; always-off ships a session cookie that can travel over plaintext in production.
  **Recovery:** Compute `Secure` from the environment so it is enabled everywhere except local development, and verify the response `Set-Cookie` carries `Secure` in a production-like environment.

- **Mistake:** Relying on `SameSite` alone and never sending the custom header from the client, assuming the cookie attribute is enough.
  **Symptom:** State-changing requests fail with a CSRF-mismatch error whenever the refresh cookie is present but the header is missing, because the server requires both halves of the double-submit pair.
  **Recovery:** Have the client read the CSRF cookie and echo it in the `X-CSRF-Token` header on every state-changing request, and send the request with credentials included so the cookie is attached.

## External reading

- [Mozilla Developer Network Web Docs: Using HTTP cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies)
- [Mozilla Developer Network Web Docs: Set-Cookie and the SameSite attribute](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)
- [Mozilla Developer Network Web Docs: Cross-Site Request Forgery (CSRF)](https://developer.mozilla.org/en-US/docs/Web/Security/Attacks/CSRF)
- [Request for Comments (RFC) 6265: HTTP State Management Mechanism](https://datatracker.ietf.org/doc/html/rfc6265)
