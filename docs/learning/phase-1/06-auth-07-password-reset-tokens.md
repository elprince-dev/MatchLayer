# Password-reset tokens

## Introduction

This document explains how a system lets a person who has forgotten their
password prove they control an account and choose a new password, without ever
telling anyone the old one. The mechanism is a password-reset token: a long
random string the system hands to the account owner through a side channel
(normally email), which the owner then presents back to the system to authorize
the change. The interesting part is not the string itself but the three
properties that make it safe to use — it is stored only as a one-way hash, it
works exactly once, and it stops working after a short time. This topic sits in
the Authentication and accounts track because a reset flow is a second front
door to every account, and a weak one undoes every other login protection.

**Learning outcomes** — after reading this document you will be able to:

- Explain why a reset token must be stored as a hash rather than in plain form, and what an attacker who reads the database gains in each case.
- Describe what "single-use" and "time-to-live (TTL)" mean for a token, and which concrete attack each property closes.
- Explain why the system reveals nothing about whether an email address has an account when a reset is requested.
- Recognise the common mistakes in building a reset flow and recover from them. A reset path that leaks is worse than no reset path.

Prerequisites:

- [Structured logging as a PII defense](05-security-03-structured-logging-as-pii-defense.md) — explains the redaction control that governs how the development-only reset link is allowed to be logged.
- [PostgreSQL fundamentals](07-database-01-postgresql-fundamentals.md) — introduces tables, columns, and unique indexes, which the token store relies on.
- [SQLAlchemy async and the session dependency](03-backend-04-sqlalchemy-async-and-session-dependency.md) — introduces the database session through which the token is written and read.
- [RFC 7807 error envelope](03-backend-09-rfc-7807-error-envelope.md) — introduces the single generic error shape the confirm step returns for every invalid token.

## Problem it solves

People forget passwords constantly, and an account whose password cannot be
recovered is an account the owner loses forever. The concrete problem is letting
a legitimate owner regain access while giving an attacker no new way in. This is
harder than it looks, because the reset flow has to work for someone who, by
definition, cannot currently authenticate — so it cannot lean on the normal
password check.

The pre-existing approach, still common in old systems, was to email the user
their current password, or to set a new temporary password and email that. Both
require the system to know or generate a usable password and put it in an email,
which is plain text sitting in inboxes and mail-server logs indefinitely. Worse,
emailing the current password proves the system stored it in a recoverable form,
which is a serious failure on its own. Another weak pattern was a "security
question" whose answer (a mother's maiden name, a first pet) is often public or
guessable, turning the reset path into the easiest way to break in.

A reset token replaces all of that with a single-purpose secret. The system
generates a fresh random value, remembers only a fingerprint of it, sends the
value to the address on file, and accepts it back exactly once within a narrow
window to authorize one password change. Nothing reusable is ever stored or
emailed, and the side channel (control of the inbox) becomes the thing being
verified.

## Mental model

Think of a hotel that issues a one-time keycard for a guest who lost their room
key. The front desk does not hand back the original key; they program a brand-new
card that opens the room once, expires at checkout time, and is recorded in their
system only as a scrambled code, not as the working card itself. If a cleaner
later finds the card in a hallway, it is already expired or already used, and the
desk's records cannot be turned back into a card that opens anything.

When one reset runs end to end, the steps are:

1. The owner asks to reset, identifying the account by its email address.
2. The system generates a long random value, computes a one-way fingerprint of it, and stores only the fingerprint together with an expiry time and an empty "used" marker.
3. The system sends the original random value — never the fingerprint — to the address on file through the side channel.
4. The owner presents the value back. The system fingerprints what was presented and looks for a matching stored fingerprint that has not expired and has not been used.
5. On a match, the system sets the new password, stamps the token as used so it cannot work again, and invalidates the account's existing sessions.

Steps 2 and 4 are where the three safety properties live: the fingerprint (hash
at rest), the expiry (TTL), and the used marker (single-use).

## How it works

A reset token is a high-entropy random string — long enough that guessing it is
infeasible — generated by a cryptographically secure random source. The system
treats the raw token as a bearer secret: whoever holds it can perform the one
action it authorizes, so it must travel only to the account owner and must never
be stored in a form an attacker could reuse.

**Hashed at rest.** Rather than saving the raw token, the system saves a one-way
hash of it — the output of a function that is easy to compute forward but
infeasible to reverse. When the token comes back, the system hashes the
presented value and compares hashes. The security gain is precise: an attacker
who reads the entire token table sees only hashes, and a hash cannot be turned
back into a token that the system will accept. Because the token is long and
random (unlike a human-chosen password), a plain fast hash such as a Secure Hash
Algorithm 256-bit (SHA-256) digest is sufficient here; the slow,
salted hashing used for passwords exists to resist guessing of low-entropy
secrets, which a random token is not.

**Time-to-live (TTL) bounded.** Each token carries an expiry timestamp, and the
system rejects any token presented after that moment. A short window — an hour is
typical — shrinks the time during which a leaked token (forwarded email, shared
screenshot, mailbox breach) is dangerous. Expiry is enforced at verification time
by comparing the stored expiry against the current time; an unexpired-looking row
in storage means nothing if the check rejects it.

**Single-use.** Each token records whether it has already been consumed. The
first successful use stamps it as used, and every later presentation of the same
value is rejected even if it has not yet expired. This closes replay: a token
captured after a legitimate reset is already spent and authorizes nothing. The
used marker and the expiry check are evaluated together, so a token must be
present, unexpired, and unused to be accepted.

Two more properties round out a safe flow. First, the request step must not
reveal whether an address has an account — it returns the same response and
takes the same time whether or not a user was found, so the reset endpoint cannot
be used to discover which email addresses are registered (an attack called
account enumeration). Second, completing a reset should invalidate the account's
existing sessions, on the theory that a reset often follows a suspected
compromise, so any sessions an attacker established should be cut off.

The raw token reaches the owner through an out-of-band channel — a path separate
from the request itself, almost always an email containing a link with the token
embedded. The link's job is to carry the token back to the verification step when
the owner clicks it. In environments without an email provider, the system needs
some other sanctioned way to surface that link to a developer, which is the
subject of the next section.

## MatchLayer Phase 1 usage

The reset flow lives in the authentication service at
`apps/api/src/matchlayer_api/services/auth.py`, split across two methods:
`request_password_reset` (mint a token) and `confirm_password_reset` (verify and
apply). The token table itself — `password_reset_tokens`, with a `token_hash`
column, an `expires_at` column, and a nullable `used_at` column — is defined in
the baseline migration under `apps/api/alembic/`.

When a reset is requested for a known account, the service generates a random
token, stores only its hash, and sets a one-hour expiry. The raw token exists
only in this local variable and in the link sent onward; the database never sees
it:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
        plaintext_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).digest()

        now = _now()
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=now + timedelta(hours=1),
                used_at=None,
            )
        )
```

If the email does not belong to any account, the method returns earlier without
creating a token and without changing its response — the no-enumeration property
described above.

Confirming a reset hashes the presented token the same way, looks the hash up,
and rejects the attempt unless a matching row exists that is both unexpired and
unused. The three conditions are checked in one expression, and every failure
collapses to the same `False` return so the caller cannot tell which condition
failed:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
        token_hash = hashlib.sha256(token.encode("utf-8")).digest()

        result = await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        row = result.scalar_one_or_none()

        now = _now()

        # Reject missing, expired, or already-used tokens (Reqs 5.6-5.8).
        if row is None or row.expires_at < now or row.used_at is not None:
            return False
```

That single `False` is surfaced to the client as one generic
`invalid_reset_token` error envelope, so a missing, expired, and already-used
token are indistinguishable from the outside.

Phase 1 has no email provider, so the raw link cannot actually be emailed. The
service instead surfaces it through a development-only channel: it builds the
reset link, emits one structured log line, and records the link in an in-process
single-slot store. This branch runs only when the Application Programming Interface (API) is in the development
environment:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
        if is_dev_environment(self._settings):
            link = f"{self._settings.web_base_url}/reset-password?token={plaintext_token}"
            _log.info(
                "password_reset_link_generated",
                password_reset_link=link,
                user_id=str(user.id),
            )
            DEV_RESET_LINK_STORE.record(link)
```

The store itself is a process-wide singleton defined in
`apps/api/src/matchlayer_api/dev/reset_links.py`. It is a single-slot
least-recently-used (LRU) cache — capacity one — that keeps only the most recent
link in memory and never writes it to disk, Redis, or any external service. Each
`record` call also emits exactly one structured log event carrying the link:

Source: `apps/api/src/matchlayer_api/dev/reset_links.py`

```python
        _log.info("password_reset_link_generated", password_reset_link=link)
```

The `password_reset_link` field is a deliberate, documented carve-out from the
log-redaction policy covered in the prerequisite on structured logging — it is
allowed precisely because it only ever fires in development, where surfacing the
link is the whole point.

## Common pitfalls

- **Mistake:** Storing the raw reset token (or an encrypted-but-reversible form) in the database instead of a one-way hash.
  **Symptom:** The token column contains values that look like usable links or tokens, and anyone with read access to the table — a backup, a support tool, a leaked dump — can complete a reset for any account.
  **Recovery:** Store only a hash of the token, compare by hashing the presented value, and migrate by invalidating all outstanding tokens so no reversible value remains.

- **Mistake:** Checking expiry or the used marker in storage-loading code but not at verification time, or forgetting to stamp the token used after a successful reset.
  **Symptom:** A token works more than once, or works long after its window — replaying an old reset link still changes the password.
  **Recovery:** Make the accept condition require present-and-unexpired-and-unused in one place, and within the same transaction set the used marker before returning success.

- **Mistake:** Returning a different response (or taking a different amount of time) when the requested email has no account.
  **Symptom:** An attacker scripting the request endpoint can tell registered addresses from unregistered ones by the status, body, or latency — account enumeration.
  **Recovery:** Return the same accepted response for every request regardless of whether a user exists, and do the account lookup work on both paths so timing does not betray the answer.

- **Mistake:** Leaving a development-only reset-link surface (a log line, an endpoint, an in-memory store) reachable in production.
  **Symptom:** Raw reset links for real users appear in production logs or behind a debug route, handing out one-time account access to anyone who can read them.
  **Recovery:** Gate the surface behind an explicit environment check, confirm the gate in a test, and make the store in-memory only so there is structurally nowhere for the link to persist.

## External reading

- [OWASP Forgot Password Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
- [Python documentation: the `secrets` module](https://docs.python.org/3/library/secrets.html)
- [Python documentation: the `hashlib` module](https://docs.python.org/3/library/hashlib.html)
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
