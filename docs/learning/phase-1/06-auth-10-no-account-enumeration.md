# No account enumeration

## Introduction

Account enumeration is the ability of an outsider to learn whether a particular
account exists on a system by watching how the system answers. A login form that
says "no such user" for an unknown email but "wrong password" for a real one has
handed an attacker a yes/no oracle: feed it addresses, read the replies, and
harvest a list of valid accounts to target with password guessing or phishing.
This document explains the defence that closes that oracle — making a failed
login look and feel the same whether the email is unknown or the password is
wrong. You will see why the response _body_ alone is not enough, how a decoy
password hash equalises the _response time_ of the two paths, and how the login
code keeps both branches indistinguishable from the caller's point of view.

The defence leans on a slow password-hashing algorithm. The one used here is
Argon2id, a deliberately memory-hard and time-costly function for turning a
password into a stored verifier; "memory-hard" means each verification is
engineered to consume a fixed, non-trivial amount of memory and central
processing unit (CPU) time so it cannot be sped up cheaply. That slowness is a
feature for storing passwords, but it also creates the timing gap this document
is about.

**Learning outcomes** — after reading this document you will be able to:

- Explain what account enumeration is and why an attacker values a list of valid accounts.
- Describe how differing response bodies _and_ differing response times each leak account existence, and why fixing only one leaves the other open.
- Explain how verifying a throwaway "dummy" hash on the unknown-account path equalises response time with the wrong-password path.
- Recognise the common ways this defence is accidentally broken and how to restore it.

Prerequisites:

- [Rate limiting and account lockout](06-auth-05-rate-limiting-and-account-lockout.md) — covers the same login-verification path and the failed-login handling this document builds on.
- [The RFC 7807 error envelope](03-backend-09-rfc-7807-error-envelope.md) — introduces the structured error shape that the generic login failure is returned through.

## Problem it solves

Authentication endpoints are public by necessity: anyone on the internet can
submit an email and a password. A naive login implementation looks up the
account, and if no row matches, returns early with a message like "account not
found"; if a row matches but the password fails, it returns "incorrect password".
Each message is helpful to a confused user — and equally helpful to an attacker.
By submitting a wordlist of email addresses and sorting the responses into "not
found" versus "incorrect password", the attacker recovers exactly which addresses
have accounts, without ever guessing a single password. That list is valuable on
its own: it feeds targeted phishing, credential-stuffing with leaked password
dumps, and social-engineering.

The first fix most teams reach for is to return one identical message for both
cases — a single generic "email or password is incorrect" for every failure. That
removes the obvious tell, but it leaves a subtler one. When the email is unknown,
the naive code never verifies a password hash, because there is no stored hash to
verify against, so it returns almost instantly. When the email is real but the
password is wrong, the code runs the slow password-hash verification before
deciding the password does not match. The real-account path is therefore
measurably slower. An attacker timing the responses sees the same gap the
messages used to reveal: fast means "no account", slow means "account exists,
wrong password". The body was equalised; the clock was not.

The full defence has to close both channels at once: an identical response body
**and** an indistinguishable response time, regardless of whether the account
exists.

## Mental model

Picture a bank teller who must check signatures against a card on file. An
honest, lazy teller does this: if there is no card on file for the name, they say
"declined" right away; if there is a card, they spend ten careful seconds
comparing the signature before saying "declined". A fraudster standing in line
with a stopwatch learns everything — an instant "declined" means no account
exists, a ten-second "declined" means the account is real and only the signature
was off.

Now picture a disciplined teller who follows one rule: **always spend the ten
seconds, even when there is no card on file.** When a name has no card, the teller
pulls out a blank decoy card and goes through the exact same ten-second comparison
ritual against it, knowing it will fail, then says "declined" — the same word, at
the same moment on the stopwatch, as every other failure. The fraudster's
stopwatch is now useless, and so is reading the teller's lips: every rejection is
identical.

The login defence is that disciplined teller, expressed in three steps:

1. Look the account up by email. If it does not exist, verify the submitted password against a fixed **decoy hash** that is guaranteed never to match, paying the same hashing cost a real account would, then discard the result.
2. If the account does exist, verify the submitted password against that account's stored hash exactly as normal.
3. On any failure — unknown account or wrong password — return the _same_ generic error message, with the same status code and the same shape, so neither the body nor the timing distinguishes the two cases.

## How it works

The defence combines two equalisations: the message and the timing.

Equalising the message is the straightforward half. Every failed-login response
returns one generic error — conventionally "email or password is incorrect" — with
one status code (the Hypertext Transfer Protocol (HTTP) status `401 Unauthorized`)
and one body shape. Nothing in the response names which half of the credential
was wrong. The success response is naturally different; the rule is only that
_failures_ are mutually indistinguishable.

Equalising the timing is the half that is easy to get wrong. The cost asymmetry
comes from the password-hash verification. Verifying a password against a stored
hash with a memory-hard algorithm is deliberately slow — that slowness is what
makes stolen hashes expensive to crack offline. When an account exists, the login
path pays that cost on every wrong-password attempt. When the account does not
exist, there is no stored hash, so a naive path skips the verification entirely
and returns far sooner. The measurable difference between "ran the slow hash" and
"skipped it" is the timing oracle.

The fix is to make the unknown-account path pay the identical cost. The system
keeps one precomputed **dummy hash**: a valid password-hash string produced once,
at startup, from an arbitrary throwaway password that is never a real credential.
On the unknown-account path, instead of returning early, the code runs the same
verification routine against this dummy hash. The verification is guaranteed to
fail — the submitted password was not the throwaway one — but it consumes the same
memory and CPU time a real verification would, so the response lands at the same
point on the clock. The boolean result is thrown away; only its timing cost
mattered.

Two details make or break the equalisation:

- **Precompute the dummy hash once.** Hashing the throwaway password on every unknown-account request would do _extra_ work the real path does not, swinging the timing the other way and reopening the oracle from the opposite direction. The dummy hash is computed a single time and reused.
- **Never let an input short-circuit the verify.** If the verification routine bailed out early for, say, an implausibly short submitted password, the unknown-account path would again return faster than the wrong-password path for those inputs. The verify must run to completion on every failing input so the cost is uniform.

There is a deliberate trade here. Running a hash verification for accounts that do
not exist spends CPU on requests that were always going to fail, which slightly
raises the cost of a flood of garbage logins. That cost is accepted because the
alternative — leaking which accounts exist — is worse, and because a separate rate
limit caps how many such requests any caller can make.

A final point: timing equalisation is best-effort, not perfect. Network jitter,
garbage-collection pauses, and load all add noise, and a determined attacker
averaging thousands of samples can sometimes still tease out a residual
difference. The dummy-hash technique closes the large, reliable gap — the
presence or absence of an entire hash verification — which is the one that makes
enumeration cheap and practical. Defence in depth (rate limiting, monitoring,
generic messaging) covers the rest.

## MatchLayer Phase 1 usage

The login logic lives in the authentication service at
`apps/api/src/matchlayer_api/services/auth.py`, and the decoy hash it relies on is
defined in `apps/api/src/matchlayer_api/core/security/passwords.py`. The dummy
hash is computed exactly once, at module import, from a throwaway password:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
DUMMY_HASH: str = _hasher.hash("dummy-password-never-used-in-production")
```

The `authenticate` method looks the account up by case-insensitive email. When no
row matches, it does **not** return early — it verifies the submitted password
against `DUMMY_HASH`, discards the result, records the failure, and returns the
generic `invalid_credentials` outcome:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
            verify_password(DUMMY_HASH, password)
            await self._audit.emit(
                session,
                event_type="login_failure",
                user_id=None,
                payload={"submitted_email": email.lower()},
            )
            return AuthenticateOutcome(
                status=_AuthenticateStatus.INVALID_CREDENTIALS,
                user=None,
                access_token=None,
                refresh_token=None,
            )
```

When the account _does_ exist but the password is wrong, the method reaches a
different branch — yet it returns the identical outcome variant, so the caller
cannot tell the two apart:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
        if not matches:
            await self._record_failed_login(session=session, user=user, now=now)
            await self._audit.emit(
                session,
                event_type="login_failure",
                user_id=user.id,
                payload={"submitted_email": email.lower()},
            )
            return AuthenticateOutcome(
                status=_AuthenticateStatus.INVALID_CREDENTIALS,
                user=None,
                access_token=None,
                refresh_token=None,
            )
```

The verification helper deliberately performs no length check, so a too-short
submitted password cannot short-circuit the cost on the unknown-account path:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
    try:
        _hasher.verify(stored, _normalize(plaintext))
    except VerifyMismatchError:
        return False, False
```

Finally, the login route maps the single `invalid_credentials` outcome to one
generic error — the same status code, type, and message for both the
unknown-account and wrong-password branches. This is in the auth router at
`apps/api/src/matchlayer_api/auth/router.py`:

Source: `apps/api/src/matchlayer_api/auth/router.py`

```python
    if outcome.status == "invalid_credentials":
        await session.commit()
        raise MatchLayerError(
            "Email or password is incorrect.",
            status_code=401,
            error_type="invalid_credentials",
            title="Invalid Credentials",
        )
```

Because the service folds both failure branches into one `INVALID_CREDENTIALS`
variant and the router has exactly one place that turns that variant into a
response, there is no second code path where the two cases could accidentally
diverge in message or shape.

## Common pitfalls

- **Mistake:** Returning a distinct message for an unknown account (for example "no account with that email") separate from the wrong-password message.
  **Symptom:** An attacker submitting a wordlist of emails sorts the responses into two buckets and recovers the full list of valid accounts without guessing any password; the two failure responses differ in body text or status code.
  **Recovery:** Collapse both failures into one generic error with identical status code, type, and message, returned from a single code path so the branches cannot drift apart.

- **Mistake:** Equalising the message but skipping the password-hash verification when the account is not found, so that path returns early.
  **Symptom:** The unknown-account response is consistently and measurably faster than the wrong-password response; an attacker timing replies distinguishes real accounts by latency even though every message reads the same.
  **Recovery:** On the unknown-account path, verify the submitted password against a fixed decoy hash and discard the result, so both paths pay the same hashing cost before returning.

- **Mistake:** Computing the decoy hash fresh on every unknown-account request instead of precomputing it once.
  **Symptom:** Unknown-account responses become _slower_ than wrong-password ones because they pay an extra hashing step, reopening the timing oracle from the opposite direction.
  **Recovery:** Compute the decoy hash a single time at startup and reuse the stored value on every unknown-account request.

- **Mistake:** Letting the verification routine short-circuit on certain inputs — for instance, rejecting an implausibly short password before hashing.
  **Symptom:** For those inputs the unknown-account path returns early again, so the timing gap returns for a subset of requests even though the decoy hash exists.
  **Recovery:** Keep the verification free of input-dependent early exits so it runs to completion and costs the same on every failing input.

## External reading

- [OWASP Authentication Cheat Sheet — authentication and error responses](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP WSTG — Testing for Account Enumeration and Guessable User Account](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/03-Identity_Management_Testing/04-Testing_for_Account_Enumeration_and_Guessable_User_Account)
- [MDN Web Docs — 401 Unauthorized](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/401)
- [RFC 9106 — Argon2 Memory-Hard Function for Password Hashing](https://datatracker.ietf.org/doc/html/rfc9106)
