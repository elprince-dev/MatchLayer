# Idempotency keys and safe retries

## Introduction

This document explains how a web service can let a client safely retry a
request that changes data — without performing the change twice — using an
idempotency key (a unique value the client attaches to the request so the
server can recognise a repeat and return the original result instead of acting
again). The web here runs on the Hypertext Transfer Protocol (HTTP), the
request-and-response protocol behind every page load, and the service exposes
its operations through an Application Programming Interface (API), the set of
endpoints one program offers for another to call. A request that changes data —
creating a record, uploading a file, charging a card — is called a _mutating_
request, because it alters server state rather than only reading it. The key
travels in a request header named `Idempotency-Key`, and the server remembers
each key for a bounded window so a retry inside that window is answered from
memory.

This topic belongs to the API and data conventions track because the rule is
cross-cutting: every mutating endpoint where a retry could cause harm follows
the same header contract and the same retention window, so the behaviour is
predictable across the whole surface.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an idempotency key is and how it lets a client retry a mutating request without repeating its side effect. The key is the handle the server uses to recognise a repeat.
- Describe how a server stores the outcome of the first request and replays it for any later request carrying the same key. The stored outcome is what makes the retry safe.
- Recognise the common mistakes teams make when adopting idempotency keys and recover from them. Most come from choosing the key badly or forgetting the retention window.

Prerequisites:

- [Redis fundamentals and the Phase 1 standby](07-database-03-redis-fundamentals.md) — the short-lived key store that remembers each idempotency key is backed by this in-memory data store.
- [Rate limiting and account lockout](06-auth-05-rate-limiting-and-account-lockout.md) — the rate-limit dependency runs before the idempotency lookup and gates the store-unavailable case, so the two features are wired together on the same endpoints.

## Problem it solves

Networks are unreliable in one direction at a time. A client sends a mutating
request — say, "upload this resume" — the server receives it, does the work, and
starts sending back the response. If the connection drops before that response
arrives, the client has no way to tell what happened: the upload may have
succeeded, or it may have failed before doing anything. The safe-looking move,
retrying, is exactly the dangerous one. If the first request actually succeeded,
the retry creates a _second_ record. The concrete problem is the double side
effect: two stored files, two charges, two of whatever the endpoint creates,
from what the user intended as one action.

The state most services start in offers no defence against this. Each mutating
request is treated as wholly new, so a retry is indistinguishable from a genuine
second action, and the duplicate is created without complaint. Some teams try to
paper over it by inspecting the request body and rejecting anything that "looks
like" a recent one, but that is fragile: two legitimately distinct uploads can
carry identical bytes, and two retries of one action can differ in incidental
details, so content-matching both rejects real requests and lets duplicates
through.

An idempotency key solves this by moving the decision from guesswork to an
explicit handle. The client generates one unique value per intended action and
attaches it to every attempt at that action, including retries. The server uses
that value — not the body, not the timing — to decide whether it has seen the
action before. A first-seen key is processed normally; a repeat key returns the
stored result of the first attempt. One intended action produces one side
effect, no matter how many times the request is sent.

## Mental model

Think of a coat check at a theatre. When you hand over your coat, the attendant
gives you a numbered claim ticket and hangs the coat on a hook. If you walk back
and present the same ticket again — because you forgot you already had your coat,
or the line was confusing — the attendant does not take a second coat from you
and hang it on a second hook. They look at the ticket number, see it already
points at a hook, and hand back what is there. The ticket number, not the
coat, is what prevents a duplicate. You, the client, are responsible for putting
a fresh number on each genuinely new coat; the attendant, the server, is
responsible for honouring a number it has already seen.

Walking through how one mutating request is handled with a key:

1. Before the first attempt, the client generates one unique key for this intended action and attaches it in the `Idempotency-Key` header.
2. The server looks the key up in a short-lived store; finding nothing, it performs the work, then saves the key together with the response it produced.
3. The server returns that response to the client as normal.
4. If a retry arrives carrying the same key, the server finds the saved entry and returns the stored response without redoing the work.
5. After a fixed retention window passes, the stored entry expires and the key is forgotten, so the key space cannot grow without bound.

The detail newcomers miss is step 5: the memory is deliberately temporary. The
key only needs to outlive the brief window in which a client might retry, not
forever.

## How it works

An operation is _idempotent_ when performing it many times has the same effect
as performing it once. Some request methods are idempotent by definition: reading
a record with `GET`, or replacing one wholesale with `PUT`, leaves the same state
whether done once or ten times. Creating a new record with `POST` is the opposite
— each call is meant to make another record — so it is not naturally idempotent.
An idempotency key is the mechanism that makes a non-idempotent operation behave
idempotently when the client asks for that guarantee.

The contract has two sides. The client side is responsible for the key's
_uniqueness per intended action_: it generates one fresh, hard-to-guess value for
each distinct thing it wants to do, reuses that same value across every retry of
that one thing, and never reuses it for a genuinely different action. The server
side is responsible for _remembering outcomes_: on receiving a key, it checks
whether it has already recorded a result for that key, and either replays the
stored result or, for a first-seen key, does the work and records the result
before responding.

Storing the outcome — not merely a "seen" flag — is what lets the retry return
the _same_ answer the first attempt gave, including the identifier of whatever
was created. A server typically serialises the original response into a compact
text form such as JavaScript Object Notation (JSON), a plain-text format that
writes data as key/value pairs, and keeps it under the key. A later request with
that key deserialises the stored body and returns it verbatim, so the client
cannot tell a replay apart from the original success.

Two safeguards make the scheme robust. The first is a _retention window_: the
stored entry is given a Time To Live (TTL), an expiry after which the store
discards it automatically. The window is chosen to comfortably cover realistic
retry behaviour — long enough that a client retrying after a timeout still hits
the stored result, short enough that the store does not accumulate keys forever.
The second is _first-writer-wins_ on the save. Two retries can race so closely
that both find the key absent and both do the work; to keep that from storing two
different outcomes, the save is a conditional "store only if still absent", so the
first outcome to land is the one every later replay sees, and a concurrent second
write is discarded rather than overwriting it.

A final design choice is what the server does when the memory store itself is
briefly unreachable. Because the store is an optimisation for safety rather than
the source of truth for the created record, a sensible service _fails soft_: a
failed lookup is treated as "not seen" so the request still proceeds, and a failed
save is logged but never turned into an error, because the record was already
created successfully. The retry guarantee degrades for that one blip without
turning a success into a failure.

## MatchLayer Phase 1 usage

MatchLayer's security baseline names the mutating endpoints where re-execution
risk matters — uploads, password reset, and webhook handlers — as the places that
accept an `Idempotency-Key` header, and it fixes the retention window at 24 hours.
In Phase 1 the implemented mutating creates are the resume upload and the
match-creation endpoints, and both wire the same header contract through a shared
store defined in `apps/api/src/matchlayer_api/core/dependencies.py`.

The retention window is a single named constant, so the 24-hour figure lives in
one place:

Source: `apps/api/src/matchlayer_api/core/dependencies.py`

```python
# 24 hours, per ``security.md`` ("Persist keys for 24h") and the design.
_IDEMPOTENCY_TTL_SECONDS: Final[int] = 24 * 60 * 60
```

The store key is built from the account id, the route name, and the
client-supplied value, so one account's key can never replay another account's
stored response, and the same key sent to two different endpoints is treated as
two independent actions:

Source: `apps/api/src/matchlayer_api/core/dependencies.py`

```python
def _build_idempotency_key(*, user_id: UUID, route: str, key: str) -> str:
    """Return the Redis key for an idempotent outcome (Design §idempotency).

    Scoped by ``user_id`` so one account's key can never replay another's
    stored response, and by ``route`` so the same client-supplied key on a
    different endpoint is treated independently.
    """
    return f"idem:{user_id}:{route}:{key}"
```

Here the store is Redis (an in-memory data store the service uses as a fast,
shared cache that can expire entries on its own). The save uses Redis's
store-only-if-absent option together with the 24-hour expiry, which is the
first-writer-wins behaviour described above:

Source: `apps/api/src/matchlayer_api/core/dependencies.py`

```python
            await self._redis.set(redis_key, payload, ex=_IDEMPOTENCY_TTL_SECONDS, nx=True)
```

On the request path, the match-creation handler at
`apps/api/src/matchlayer_api/api/matches/router.py` shows the replay short-circuit:
when a key is present and already has a stored outcome, the handler returns the
stored response and performs no second write. The stored body is the serialised
original response, so revalidating it reproduces the identical result:

Source: `apps/api/src/matchlayer_api/api/matches/router.py`

```python
    if idempotency_key:
        record = await idempotency_store.get(
            user_id=user.id, route=_IDEMPOTENCY_ROUTE, key=idempotency_key
        )
        if record is not None:
            return MatchResponse.model_validate(record.body)
```

The resume upload handler at `apps/api/src/matchlayer_api/api/resumes/router.py`
follows the same two-step shape — replay-if-seen, then store-the-outcome — so the
upload endpoint and the match endpoint share one idempotency contract rather than
each inventing its own.

## Common pitfalls

- **Mistake:** Generating a new idempotency key on every attempt, including retries, instead of reusing one key per intended action.
  **Symptom:** Retries after a dropped connection still create duplicate records, because each attempt carries a different key the server has never seen.
  **Recovery:** Generate the key once, before the first attempt, and attach that same value to every retry of the same action; only a genuinely new action gets a new key.

- **Mistake:** Reusing one idempotency key across genuinely different actions, for example a hard-coded constant shared by every upload.
  **Symptom:** A second, legitimately different request returns the first request's stored response and the real action never happens.
  **Recovery:** Make the key unique per intended action (a fresh random value per action), and never share a key between distinct operations.

- **Mistake:** Storing only a "seen" flag for the key rather than the full original response.
  **Symptom:** A retry is recognised but cannot be answered with the original result — the created record's identifier is lost, so the client gets a different or empty response.
  **Recovery:** Store the serialised original response under the key and replay that body on a repeat, so a retry is indistinguishable from the first success.

- **Mistake:** Treating a store outage as a hard failure during the idempotency lookup or save.
  **Symptom:** A brief cache blip turns a request that would otherwise succeed into a server error, even though the underlying create could complete.
  **Recovery:** Fail soft — treat a failed lookup as "not seen" so the request proceeds, and log a failed save without surfacing it, since the record was already created.

## External reading

- [Mozilla web documentation: Idempotent (glossary)](https://developer.mozilla.org/en-US/docs/Glossary/Idempotent)
- [The Idempotency-Key HTTP header field (specification draft)](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header)
- [FastAPI documentation: header parameters](https://fastapi.tiangolo.com/tutorial/header-params/)
- [Redis documentation: setting a key with an expiry and a conditional write](https://redis.io/docs/latest/commands/set/)
