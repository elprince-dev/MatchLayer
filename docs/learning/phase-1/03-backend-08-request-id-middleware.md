# The request-id middleware and the X-Request-Id header

## Introduction

This document explains how a web service tags every request with a unique
identifier so that all the work done for that request can be tied back together
afterwards, and the small piece of software that does the tagging. That piece is
middleware — code that sits between the server and the application and sees every
request on the way in and every response on the way out, without being tied to
any single endpoint. The identifier it manages is the request id: a short,
opaque string that is unique to one request and is carried alongside the request
so logs, errors, and the caller can all refer to the same request by the same
name. The contract for passing that string over the wire is a Hypertext Transfer
Protocol (HTTP) header — a named line of metadata attached to a request or
response — specifically the header named `X-Request-Id`. This topic belongs to
the Backend track because the middleware wraps every backend request and feeds
the identifier into both the logging system and the error responses.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a request id is and why correlating every log line and error for one request to a single identifier makes debugging tractable.
- Describe the rule for when an inbound identifier is trusted and reused versus when a fresh one is generated.
- Explain the `X-Request-Id` header contract in both directions — reading it in and echoing it back out.
- Recognise the common mistakes when implementing request-id middleware and recover from them.

Prerequisites: this document builds on
[FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md),
which explains the Asynchronous Server Gateway Interface and where middleware is
attached, and on
[structlog and structured JSON logging](03-backend-07-structlog-and-json-logging.md), which
explains the structured logger and the per-request context fields this
middleware binds.

## Problem it solves

When a single web request touches several components — it is logged on arrival,
runs some business logic that logs more, perhaps fails and produces an error
response — each of those log lines and the error are separate records. The
concrete problem is reconnecting them after the fact. An operator looking at a
failed request needs to find every log line that belongs to it, and a user
reporting a problem needs a way to point support at the exact request that went
wrong. Without a shared handle, the records are scattered with nothing tying them
together.

The prior approach was to correlate by rough signals: a timestamp range, the
client address, the route. That is unreliable, because many requests share those
signals — two users hitting the same route at the same second are
indistinguishable, and concurrent requests interleave their log lines.

A request id solves this by stamping one unique string onto the request the
moment it arrives, binding that string into every log line emitted while the
request runs, including it in any error response body, and sending it back to the
caller in a response header. Now every record for the request carries the same
identifier, the operator filters logs by it, and the user can quote it from the
response they received.

## Mental model

Think of the coat check at a venue. When you arrive, the attendant either reads
the numbered ticket you already hold (if it is a valid one) or tears off a fresh
numbered ticket for you. That number is written on everything connected to your
visit, and when you leave, the same number is handed back to you so you can
prove which items were yours. Nobody has to remember your face — the number does
the work.

When the middleware handles one request, it runs these steps:

1. Look for an inbound `X-Request-Id` header on the arriving request.
2. If that header is present and its value matches the accepted format, reuse it; otherwise generate a fresh, time-ordered identifier.
3. Bind the identifier — along with the route and method — into the per-request logging context so every log line for this request carries it.
4. Run the rest of the application and let it produce a response.
5. Write the identifier onto the outgoing response as an `X-Request-Id` header, stripping any value the application tried to set, and emit one access-log line recording the status and how long the request took.

Because the identifier is decided first and echoed last, every piece of work in
between shares the same handle.

## How it works

The middleware is structured as a wrapper around the rest of the application. On
the way in, it inspects the request's headers for an inbound identifier. Trusting
an arbitrary inbound value is risky — a malformed or oversized value could
poison the structured logs or be used to inject content — so the middleware
validates it against a strict format (a bounded run of letters, digits, hyphens,
and underscores) and only reuses values that pass. This lets a trusted upstream
proxy or a distributed-tracing system thread its own identifier through, while
rejecting garbage. When no acceptable value is present, the middleware generates
a fresh one. A time-ordered identifier is preferred so that, even when entries
are gathered from several machines, they remain roughly sortable by arrival
order.

Once the identifier is settled, the middleware binds it into a per-request
context store, together with the route and the request method, so the structured
logger automatically merges those fields into every entry emitted during the
request. The binding uses a context variable — a per-task storage slot that stays
isolated between concurrent tasks — which is what keeps two simultaneous requests
from seeing each other's identifier.

On the way out, the middleware intercepts the message that begins the response
and rewrites its headers so that exactly one `X-Request-Id` header is present,
carrying the chosen identifier. It deliberately strips any identifier the
application itself may have set, so the value the middleware emits is
authoritative and never duplicated. The header travels in both directions under
the same name: the client reads it from the response to learn the identifier of
the request it issued, and may send it back on a later request. Finally, after
the response is fully sent, the middleware emits a single access-log entry
recording the status code and the elapsed time, choosing a severity that matches
the response class so that error responses stand out. Non-HTTP traffic passes
through untouched, because the header contract is HTTP-only.

## MatchLayer Phase 1 usage

In MatchLayer the middleware is the class `RequestIdMiddleware` in
`apps/api/src/matchlayer_api/core/middleware.py`. The accepted format for an
inbound identifier is a single compiled pattern, and the header name is held as a
lowercase byte string because the Asynchronous Server Gateway Interface (ASGI) —
the standard contract between a Python web application and the server that runs
it — normalises header names to lowercase bytes:

Source: `apps/api/src/matchlayer_api/core/middleware.py`

```python
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
```

Source: `apps/api/src/matchlayer_api/core/middleware.py`

```python
_REQUEST_ID_HEADER_NAME: Final[bytes] = b"x-request-id"
```

The decision rule — reuse a valid inbound value, otherwise generate one — is a
single expression, and the chosen identifier is then bound into the structlog
context alongside the route and method:

Source: `apps/api/src/matchlayer_api/core/middleware.py`

```python
        request_id = _extract_inbound_request_id(scope) or _generate_request_id()
```

The header contract is verified by the test suite in
`apps/api/tests/test_middleware.py`: one test asserts that when no inbound header
is present the emitted value is a freshly generated time-ordered identifier;
another asserts that a valid inbound value (for example, `abc12345` at the
minimum length, up to 128 characters) is reused verbatim on the outbound
`X-Request-Id` header; and a third asserts that an invalid value — too short, too
long, or containing spaces or semicolons — is dropped and replaced. A further
test confirms that the per-request context is cleared once the request finishes,
so no identifier leaks into the next request served by the same worker. The
bound identifier is also the value the error envelope reports, as described in
[the RFC 7807 error envelope](03-backend-09-rfc-7807-error-envelope.md).

## Common pitfalls

- **Mistake:** Trusting and reusing an inbound identifier without validating its format.
  **Symptom:** Log output is corrupted or hard to query because a caller sent an enormous or whitespace-laden value, and the bad value propagates into every correlated record.
  **Recovery:** Validate any inbound value against a strict bounded pattern and fall back to a generated identifier whenever it does not match.

- **Mistake:** Forgetting to clear the per-request context after the response is sent.
  **Symptom:** A later request handled by the same worker inherits the previous request's identifier, so unrelated log lines appear to belong to one request.
  **Recovery:** Clear the context store at the end of every request (and at the start, defensively), relying on per-task isolation for concurrent requests.

- **Mistake:** Letting the application set its own `X-Request-Id` header without stripping pre-existing values.
  **Symptom:** The response carries two `X-Request-Id` headers, or a value that disagrees with the one in the logs, so correlation breaks.
  **Recovery:** Rebuild the outbound headers to remove any existing request-id header before appending the authoritative one, so exactly one is emitted.

- **Mistake:** Applying the HTTP header logic to non-HTTP traffic such as lifespan or websocket events.
  **Symptom:** Startup or shutdown events fail or behave oddly because the middleware tried to read or write HTTP headers that do not exist for that traffic.
  **Recovery:** Check the connection type first and pass non-HTTP traffic straight through to the wrapped application untouched.

## External reading

- [MDN Web Docs: HTTP headers](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers)
- [MDN Web Docs: X-Request-ID (non-standard header)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Request-ID)
- [Python documentation: contextvars](https://docs.python.org/3/library/contextvars.html)
- [structlog documentation: context variables](https://www.structlog.org/en/stable/contextvars.html)
