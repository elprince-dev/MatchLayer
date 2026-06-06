# JSON Web Tokens and PyJWT

## Introduction

Web applications often need to carry proof of identity from one request to the
next as a compact, signed blob of JavaScript Object Notation (JSON) — a
plain-text format that stores data as key/value pairs inside braces. A JSON Web
Token (JWT) is exactly that: a small, self-contained string that states who the
holder is and what they are allowed to do, sealed with a signature so a server
can trust it without storing anything about it. This document explains the JWT
format, the Python library used to create and check tokens (PyJWT, an actively
maintained package with safe defaults), the distinction between short-lived and
long-lived tokens, and why pinning the set of accepted signature algorithms at
verification time is a security requirement rather than a nicety. This topic
sits in the Authentication and accounts track because every protected request
depends on a token being issued correctly and verified strictly.

**Learning outcomes** — after reading this document you will be able to:

- Explain the three parts of a JSON Web Token and how a signature makes tampering detectable.
- Describe the difference between an access token and a refresh token and why each has a different lifetime.
- Explain why verification must pin an explicit algorithm allowlist and what attacks an open verifier invites.
- Explain why a single shared secret with HS256 is a sound Phase 1 choice and when RS256 with key distribution becomes worth its cost.

Prerequisites:

- [Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md) — introduces the validated settings object that holds the token-signing secret and the token-lifetime values this document reads.
- [Secrets management and keeping them out of git](05-security-04-secrets-management.md) — introduces how the signing secret is kept out of source control, which matters because that secret is the only thing protecting every token.

## Problem it solves

A server that requires a user to log in needs to recognise that same user on
every subsequent request. The browser sends each request independently, so
something on each request must prove "this is the account that already
authenticated". The concrete problem is doing that proof cheaply and securely,
across many requests and potentially many server processes, without trusting the
client to assert their own identity with no proof.

The common prior approach is a server-side session. When the user logs in, the
server creates a session record in a shared store and hands the browser an
opaque session identifier in a cookie. On every later request the server takes
that identifier and looks the record up in the store to discover who the user
is. That works, but it has costs: every single request pays a lookup against the
shared store, the store becomes a piece of state that every server process must
reach, and scaling horizontally means all processes must share or replicate it.

A signed token moves the identity claims into the token itself. Because the
server can verify the signature with a key it already holds, it can trust the
claims inside the token without any lookup at all. The trade-off is that a
self-contained token cannot be retracted before it expires, which is the reason
the design pairs a very short-lived token for ordinary requests with a separate,
revocable longer-lived token used only to obtain new ones.

## Mental model

Think of a JSON Web Token as a tamper-evident festival wristband. You prove your
identity once at the entrance gate, and staff seal a wristband onto you that has
your access level printed right on it. After that, any booth inside the festival
can read the wristband and check the seal without phoning the gate to ask who
you are — the proof travels with you. If anyone tries to peel the band and edit
the printed access level, the seal visibly breaks and the next booth rejects it.
The band also has an expiry time printed on it, so it stops working at the end of
the day even if nobody collects it.

Walked through step by step, a token's life looks like this:

1. The user sends their credentials once, and the server confirms they are correct.
2. The server builds a set of claims (the holder's account identifier, an issue time, an expiry time) and signs them, producing the token string.
3. The client stores the token and attaches it to each later request.
4. On each request the server recomputes the signature with its key and rejects the token if the signature does not match or the expiry has passed.
5. When the short-lived token expires, the client presents a separate longer-lived token to obtain a fresh one, rather than asking the user to log in again.

The part newcomers miss is step 4: verification is not "read the claims", it is
"prove the claims were not altered and have not expired" before reading anything.

## How it works

A JSON Web Token is three parts joined by dots: a header, a payload, and a
signature, each encoded with a web-safe form of Base64 so the whole thing is one
plain-text string. The header names the signing algorithm. The payload holds the
claims — named facts about the holder, such as a subject identifier (the account
the token represents), an issued-at time, and an expiry time. The signature is
computed over the header and payload together using a key.

The encoding is not encryption. Anyone holding the token can decode the header
and payload and read every claim, because Base64 is reversible without any key.
The signature provides integrity, not secrecy: it lets a verifier detect that a
single bit of the header or payload was changed, but it does nothing to hide the
contents. The practical consequence is that a token must never carry a secret or
private personal data in its payload.

There are two broad families of signing algorithm, and the difference drives
most design decisions. A symmetric algorithm such as HS256 uses one shared
secret for both signing and verifying; it computes a Hash-based Message
Authentication Code (HMAC) — a keyed checksum — over the token with a 256-bit
hash function. Whoever can verify a symmetric token can also mint one, because
the same key does both. An
asymmetric algorithm such as RS256 uses a key pair: a private key signs and a
separate public key verifies. The public key can be published widely — often as
a JSON Web Key Set (JWKS), a document listing the current public keys — so that
many independent services can verify tokens without ever being able to forge one.

Verification has a trap that the standard warns about. The standard is published
as Request for Comments (RFC) 7519, where an RFC is a numbered specification
document in the series that defines internet protocols. The token itself names
its algorithm in the header, so a
verifier that blindly trusts that field lets an attacker choose how their token
is checked. Two classic attacks follow. In the `alg: none` attack, the attacker
sets the algorithm to "none" and strips the signature, hoping the verifier
accepts an unsigned token. In an algorithm-confusion attack against a service
that publishes an RS256 public key, the attacker signs a token with HS256 using
that public key as if it were the shared secret, hoping the verifier uses the
public key with the wrong algorithm. The defence for both is the same: the
verifier ignores the header's claimed algorithm and instead passes an explicit
allowlist of the algorithms it is willing to accept, so anything else is rejected
before the signature is even examined.

Lifetimes are the other half of the picture. A self-contained token cannot be
revoked before it expires, so an access token — the token attached to ordinary
requests — is given a deliberately short lifetime to bound the damage if it
leaks. A refresh token is a separate, longer-lived token whose only job is to
obtain new access tokens; because it is used rarely and against a single
endpoint, the server can track it and revoke it, and rotating it on each use
(token rotation — issuing a new refresh token and invalidating the old one every
time one is redeemed) lets the server detect theft when an already-used token
reappears.

## MatchLayer Phase 1 usage

In MatchLayer all token creation and checking lives in one module,
`apps/api/src/matchlayer_api/core/security/jwt.py`. It is the only place that
imports PyJWT, so the algorithm choice and the verification rules are enforced in
exactly one spot. The accepted algorithm is fixed as a module-level constant and
wrapped in a one-element allowlist:

Source: `apps/api/src/matchlayer_api/core/security/jwt.py`

```python
_ALGORITHM = "HS256"
_ALGORITHMS_ALLOWLIST = [_ALGORITHM]
```

Phase 1 signs with HS256, a single shared secret, rather than RS256 with a
published key set. The reasoning follows directly from the deployment: one
backend service both issues and verifies every token, so there is no second party
that needs to verify without being able to sign. A shared secret needs no key
pair, no public-key publishing, and no key-distribution document to operate or
rotate. RS256 with a JSON Web Key Set earns its extra moving parts only once
independent services must verify tokens they are not trusted to mint — a Phase 6
concern, not a Phase 1 one. The secret itself comes from the validated settings
object in `apps/api/src/matchlayer_api/config.py`, where it is held as a masked
secret value and rejected at startup if it is shorter than 32 bytes.

Issuing an access token builds the claims and signs them. The payload carries the
subject, an issued-at and an expiry timestamp derived from a configured lifetime,
a unique token identifier, and a `type` claim marking it as an access token:

Source: `apps/api/src/matchlayer_api/core/security/jwt.py`

```python
    payload: dict[str, Any] = {
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.auth_access_token_ttl_seconds)).timestamp()),
        "jti": str(uuid_utils.uuid7()),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)
```

A refresh token is issued by the sibling function using
`settings.auth_refresh_token_ttl_seconds` for a longer lifetime and a `type` of
`"refresh"`, which is what keeps the short-lived and long-lived tokens distinct.

Verification passes the allowlist explicitly, so a tampered header naming a
different algorithm — or no algorithm at all — is rejected rather than honoured:

Source: `apps/api/src/matchlayer_api/core/security/jwt.py`

```python
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=_ALGORITHMS_ALLOWLIST,
        )
```

After the signature and expiry check succeed, the function also confirms the
`type` claim matches the expected kind, so a refresh token can never be replayed
where an access token is required.

## Common pitfalls

- **Mistake:** Calling the decode function without pinning the accepted algorithms, or trusting the algorithm named in the token's own header.
  **Symptom:** Forged tokens are accepted — a token with the algorithm set to "none" and no signature, or one signed with an unexpected algorithm, passes verification in a test that should fail.
  **Recovery:** Always pass an explicit algorithm allowlist to the verifier so any algorithm outside the list is rejected before the signature is examined.

- **Mistake:** Placing a secret or private personal data in the token payload, believing the token is encrypted.
  **Symptom:** Decoding the token without any key reveals the sensitive fields in plain text, because the payload is only Base64-encoded.
  **Recovery:** Keep only minimal, non-sensitive claims in the payload (an account identifier, timestamps, a token type) and hold anything sensitive on the server.

- **Mistake:** Giving access tokens a long lifetime and no separate refresh mechanism, so a leaked token stays usable and a logout cannot take effect.
  **Symptom:** A stolen token keeps working for hours or days, and signing out in one place does not stop the token being used elsewhere.
  **Recovery:** Use a short access-token lifetime for ordinary requests and a separate, server-tracked refresh token that can be revoked and rotated on each use.

- **Mistake:** Skipping the `type` claim check and treating any valid signature as proof a token is the right kind.
  **Symptom:** A long-lived refresh token is accepted as an access token and grants access to protected endpoints far beyond its intended scope.
  **Recovery:** After verifying the signature, assert that the token's `type` claim equals the kind the endpoint expects, and reject the token otherwise.

## External reading

- [PyJWT documentation](https://pyjwt.readthedocs.io/en/stable/)
- [RFC 7519: JSON Web Token](https://datatracker.ietf.org/doc/html/rfc7519)
- [RFC 8725: JSON Web Token Best Current Practices](https://datatracker.ietf.org/doc/html/rfc8725)
- [RFC 7515: JSON Web Signature](https://datatracker.ietf.org/doc/html/rfc7515)
