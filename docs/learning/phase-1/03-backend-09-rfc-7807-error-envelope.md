# The RFC 7807 error envelope

## Introduction

This document explains how a web service can report failures to its callers in
one predictable shape, and the small standard that defines that shape. The
standard is Request For Comments (RFC) 7807 — a numbered specification, published
by the body that standardises internet protocols, that describes a uniform format
for machine-readable error responses over the web. Here the web runs on the
Hypertext Transfer Protocol (HTTP), the request-and-response protocol behind
every page load, and an Application Programming Interface (API) is
the set of endpoints one program exposes for another to call. The format the
standard defines is often called a Problem Details object, and its full title is
"Problem Details for HTTP APIs". The shape is delivered
as JavaScript Object Notation (JSON), a plain-text format that writes data as
key/value pairs inside braces. Throughout this document the agreed set of fields
that every error response carries is called the error envelope. This topic sits
in the Backend track because the envelope is produced by exception handlers
registered once when the web application starts and is then applied to every
failing request.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a standard error envelope is and why returning every failure in the same set of named fields is easier for a caller to handle than ad-hoc error text.
- Describe how an unhandled exception is turned into a structured response by a registered handler before it reaches the caller.
- Explain why an error response must never leak a stack trace, an internal class name, or sensitive input back to the caller in production.
- Recognise the common mistakes when designing an error envelope and recover from them.

Prerequisites: this document builds on
[FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md),
which explains where exception handlers are registered on the application, on
[the request-id middleware](03-backend-08-request-id-middleware.md), which explains the
per-request identifier the envelope reports, and on
[Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md), which explains
both request-shape validation and the typed setting that selects how much detail
a failure may reveal.

## Problem it solves

A web service fails in many different ways: a caller sends a malformed body, asks
for a record that does not exist, exceeds a quota, or trips over a genuine bug in
the server. The concrete problem is that, left to chance, each of those failures
comes back in a different shape — one is a plain sentence, another is a stack
trace, a third is an empty body with only a status code. A program calling the
service then has to special-case every failure mode, and a human debugging it has
to guess where the useful information lives.

The prior approach most services start with is returning whatever the framework
produces by default: an unhandled exception becomes a generic server-error page,
a validation failure becomes a framework-specific blob, and hand-written
endpoints each invent their own ad-hoc error text. That inconsistency has real
costs. A caller cannot write one routine that reads the failure reason, because
the reason is in a different place each time. Worse, the default server-error
page often includes a full stack trace, which in production leaks file paths,
library versions, and sometimes the very input that caused the failure — a
serious information-disclosure risk.

A standard error envelope solves this by fixing one set of named fields for every
error, regardless of which failure mode produced it. The caller reads the same
fields each time, the human always knows where the reason and the correlation
handle live, and the single place that builds the envelope is also the single
place that decides what is safe to reveal.

## Mental model

Think of a returns desk at a large store. However a purchase went wrong — wrong
size, faulty item, missing part — the clerk fills out the _same_ printed return
slip: a reason code, a short human description, the amount, and a tracking number
you can quote on the phone later. You never receive a handwritten note in one
case and a photocopied receipt in another; the slip is uniform, so both you and
the store's system can process any return the same way. Sensitive internal notes
(the supplier's cost, staff comments) stay behind the desk and never appear on
your copy.

When the service turns a failure into a response, it runs these steps:

1. Some code raises an error — either a deliberate, named application error or an unexpected exception from a bug.
2. A registered handler catches that error based on its type, the most specific matching handler winning.
3. The handler decides the numeric status code, a short stable reason string, a human-readable title, and a safe detail message.
4. The handler reads the current request's correlation identifier so the response can be tied back to the log lines for the same request.
5. The handler serialises those values into one fixed set of JSON fields and sends them back with the chosen status code.

Because every failure funnels through the same handlers, every error response
that leaves the service has the same shape and the same safety guarantees.

## How it works

A standard error envelope is an agreement: every error response carries the same
named fields. A common, practical field set is a stable machine-readable `type`
string, a short human `title`, a longer `detail` message describing this specific
occurrence, the numeric `status` code, and a correlation identifier that ties the
response to the server-side records for the same request. Because the fields are
fixed, a caller writes one routine to read them and never has to special-case a
particular endpoint.

The fields are produced by exception handlers rather than by each endpoint. A
handler is a function registered against an exception type; when code anywhere in
the request raises an error, the framework walks the raised exception's type
hierarchy and dispatches to the most specific handler registered for it. This is
what lets a service register a handler for its own family of deliberate,
named errors, a separate handler for request-validation failures, and a final
catch-all handler for any unexpected exception — and have every one of them emit
the same envelope. Registering these once, when the application starts, means no
endpoint has to remember to format its own errors.

Two ideas make the envelope safe as well as uniform. The first is the split
between _expected_ and _unexpected_ failures. An expected failure — a record not
found, a payload too large — is something the service raises on purpose, with a
message its authors wrote and vetted, so that message is safe to return verbatim.
An unexpected failure is a bug: the exception text may contain a file path, an
internal class name, or even a fragment of the caller's input, none of which
should travel back over the wire. The catch-all handler therefore returns a
generic message in production while still recording the real exception in the
server logs, so operators keep the detail the caller never sees. Whether the
fuller message is revealed is driven by a single environment setting, so the
debuggable behaviour in development cannot accidentally ship to production.

The second idea is that validation errors are summarised, not echoed. When an
incoming request fails shape validation, the framework knows exactly which fields
were wrong and why. The handler can report the field paths and the expectation
("field required", "string too short") without ever including the rejected
_values_, because those values may be a password or an email address. Capping how
many individual field messages are copied into the response also stops a
pathological request — say, a huge array that fails on every element — from
turning the error response into an amplification of the input.

Finally, the correlation identifier is what makes a uniform envelope operationally
useful. The same identifier that the service stamps onto every log line for a
request is placed into the error response, so a caller who reports "I got an error
and it said identifier X" hands an operator an exact key to find every related log
line. The envelope is the one place that copy happens.

## MatchLayer Phase 1 usage

In MatchLayer the error envelope is defined in
`apps/api/src/matchlayer_api/core/errors.py`. The canonical field set is built by
one private helper, so every handler returns the identical shape — a stable
`type`, a human `title`, a safe `detail`, the numeric `status`, and the
correlation `request_id`:

Source: `apps/api/src/matchlayer_api/core/errors.py`

```python
def _problem_response(
    *,
    type_: str,
    title: str,
    detail: str,
    status_code: int,
) -> JSONResponse:
    """Build a JSONResponse with the canonical error envelope."""
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "detail": detail,
        "status": status_code,
        "request_id": _current_request_id(),
    }
    return JSONResponse(status_code=status_code, content=body)
```

Deliberate, named application errors extend a single base class. The base pins
sensible defaults, and each concrete error overrides only the three fields that
distinguish it — so raising one with only a detail string yields the right
envelope without writing a new handler:

Source: `apps/api/src/matchlayer_api/core/errors.py`

```python
    # Class-level defaults; subclasses override what they need.
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "internal_server_error"
    title: str = "Internal Server Error"
```

Source: `apps/api/src/matchlayer_api/core/errors.py`

```python
class NotFoundError(MatchLayerError):
    status_code = 404
    error_type = "not_found"
    title = "Not Found"
```

The `request_id` field is not threaded through every call; it is read from the
per-request context that the request-id middleware binds, falling back to a null
value when no middleware is present (for example, in a unit test that drives a
handler directly):

Source: `apps/api/src/matchlayer_api/core/errors.py`

```python
def _current_request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(value, str):
        return value
    return None
```

The catch-all handler is where the production-versus-development split lives. The
branch is driven by the validated settings object, so a generic message ships in
production while the concrete class and message stay available in development:

Source: `apps/api/src/matchlayer_api/core/errors.py`

```python
        if is_production:
            detail = _GENERIC_INTERNAL_DETAIL
        else:
            detail = f"{type(exc).__name__}: {exc}".strip()
```

The three handlers — for the application-error base class, for request-validation
failures, and for the catch-all exception — are wired onto the application once at
startup by `register_exception_handlers`, called from the application factory in
`apps/api/src/matchlayer_api/main.py`. The behaviour is locked down by the test
suite in `apps/api/tests/test_errors.py`: one test asserts the full envelope is
returned for a named error, another asserts a validation failure never echoes the
submitted password or email back into the response body, and a third asserts that
in production the original exception class and message are absent from the
response while still being captured in the logs.

## Common pitfalls

- **Mistake:** Returning the framework's default unhandled-error page in production.
  **Symptom:** A failing request comes back with a stack trace that exposes file paths, library versions, or fragments of the caller's input.
  **Recovery:** Register a catch-all handler that returns a generic, fixed message in production and logs the real exception server-side, gated on an environment setting.

- **Mistake:** Echoing the rejected input back inside a validation error message.
  **Symptom:** A 422 response contains the submitted password, email address, or other sensitive value, which then lands in client logs and error trackers.
  **Recovery:** Summarise validation failures by field path and expectation only, and deliberately drop the offending input value when building the detail string.

- **Mistake:** Letting each endpoint invent its own error shape.
  **Symptom:** Callers must special-case every endpoint because the reason field is named or placed differently each time, and client error handling drifts out of sync.
  **Recovery:** Build every error response through one shared helper that fixes the field set, and route all failures through registered handlers rather than per-endpoint formatting.

- **Mistake:** Omitting a correlation identifier from the error response.
  **Symptom:** A user reports a failure but neither they nor support can point operators at the exact request, so the matching log lines cannot be found.
  **Recovery:** Read the per-request identifier from the same context the logger uses and include it as a field in every envelope, so the response and the logs share one key.

## External reading

- [RFC 7807: Problem Details for HTTP APIs](https://datatracker.ietf.org/doc/html/rfc7807)
- [FastAPI documentation: handling errors](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [MDN Web Docs: HTTP response status codes](https://developer.mozilla.org/en-US/docs/Web/HTTP/Status)
- [Python documentation: built-in exceptions](https://docs.python.org/3/library/exceptions.html)
