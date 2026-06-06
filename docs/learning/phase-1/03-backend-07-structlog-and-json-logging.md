# structlog and structured JSON logging

## Introduction

This document explains how a program can record what it is doing in a form that
machines can read reliably, and the library this project uses to do that. The
core idea is structured logging — a style of logging where each log entry is a
set of named fields (for example, a field named `status` holding `200`) rather
than one human-sentence string, so that automated tools can filter and search
the entries without guessing at their wording. The library is structlog, a
Python logging library that builds each entry as a dictionary of fields and then
passes it through a configurable chain of small functions before it is written
out. The usual output format here is JavaScript Object Notation (JSON), a plain
text format that writes data as key/value pairs inside braces and is understood
by virtually every log-ingestion tool. This topic sits in the Backend track
because the configuration runs once when the web application starts and then
every other backend component logs through it.

**Learning outcomes** — after reading this document you will be able to:

- Explain what structured logging is and why a set of named fields is easier for a machine to consume than a free-text sentence.
- Describe how a processor chain transforms a log entry step by step before it reaches its final written form.
- Explain why redacting sensitive fields inside the logging layer is a stronger defense than trusting every call site to omit them.
- Recognise the common mistakes when configuring a logging library and recover from them.

Prerequisites: this document builds on
[FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md),
which explains the startup function where logging is configured, and on
[Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md), which explains
the typed configuration object that selects the output format and verbosity.

## Problem it solves

A running web service emits a constant stream of log lines, and someone — or
some tool — has to make sense of them later, often while an incident is in
progress. The concrete problem is that a log line written as a free-text
sentence, such as `User 42 logged in from 10.0.0.1 in 35ms`, is easy for a human
to read one at a time but hard for a machine to query in bulk. To answer a
question like "show me every request slower than 500 milliseconds", a tool would
have to parse the latency back out of prose whose wording can drift from one log
statement to the next.

The prior approach most projects start with is the standard library's plain text
logging: each message is a formatted string, and structure (if any) is baked
into the format string by hand. That approach has real costs. Searching requires
fragile pattern-matching against sentence wording. Adding a new field to every
line means editing every log statement. And there is no systematic place to
strip sensitive values — each author has to remember not to log a password or an
email address every single time, and one forgotten call leaks data.

Structured logging solves this by making every entry a set of named fields from
the start. A machine can then filter on the `latency_ms` field directly, a new
field can be attached to every entry in one place, and sensitive values can be
scrubbed by a single function that inspects field names before anything is
written.

## Mental model

Think of a single log entry as a tray moving down an assembly line. The tray
starts holding a few items you placed on it (the event name and any fields you
passed), and each station along the line either adds something, changes
something, or inspects the tray. The last station boxes the tray up into its
final shipping form — a line of JSON — and sends it out. The stations are fixed
in order, so every tray is treated identically.

When the library handles one log call, the entry flows through the chain like
this:

1. Start with the fields the caller supplied, such as the event name and a status code.
2. Merge in context fields that were bound earlier for this unit of work (for example, an identifier shared by every entry from the same request).
3. Run a redaction step that replaces the value of any field whose name looks sensitive with a fixed placeholder.
4. Add standard fields every entry should carry, such as the severity level and a timestamp.
5. Hand the finished field set to a renderer that serialises it — as colourised text for a human in development, or as one line of JSON for a machine in production.

Because the order is fixed and lives in one place, every entry that the service
emits is shaped the same way, and changing the shape means changing the line, not
the thousands of call sites.

## How it works

Structured logging treats a log entry as data rather than as a finished
sentence. When code calls the logger with an event name and some keyword fields,
the library assembles them into a dictionary and then passes that dictionary
through an ordered sequence of functions called a processor chain. Each function
in the chain — a processor — receives the dictionary, may add, remove, or modify
fields, and returns it for the next function. The final processor is a renderer:
it converts the dictionary into the bytes that are actually written, whether that
is human-friendly coloured text or a single line of JavaScript Object Notation
(JSON).

Two ideas make this powerful. The first is contextual binding. A unit of work —
typically one inbound web request — can bind fields once (an identifier for the
request, the route, the method) into a context store, and a merge processor
copies those fields into every entry emitted while that work is in flight. The
mechanism underneath is a context variable, a per-task storage slot that stays
isolated between concurrent tasks, so two requests handled at the same time never
see each other's bound fields. The result is that correlation fields appear on
every line automatically, without each log statement having to pass them.

The second idea is that cross-cutting policy lives in the chain, not at the call
sites. Because every entry passes through the same processors, a redaction
processor can inspect each field's name and, when the name matches a sensitive
pattern (such as one containing `password`, `token`, or `email`), replace the
value with a sentinel placeholder before any renderer sees it. This is
defense in depth: even if a developer mistakenly logs a sensitive value, the
value is scrubbed in transit. Placing redaction early in the chain — before the
renderer — guarantees the sensitive value is never serialised.

Filtering by severity is handled at the boundary too. A bound logger can be
configured with a minimum level so that calls below the threshold short-circuit
before any processor runs, which keeps verbose debug calls cheap in hot paths.
The choice of renderer is usually environment-driven: a colourised console
renderer when a developer is reading the terminal, and a strict one-line-per-entry
JSON renderer when a log-collection system is ingesting the output.

## MatchLayer Phase 1 usage

In MatchLayer the logging configuration lives in
`apps/api/src/matchlayer_api/core/logging.py`, which exposes a single
`configure_logging` function called once from the application factory in
`apps/api/src/matchlayer_api/main.py`. The redaction policy is driven by a
compiled pattern of sensitive field-name fragments:

Source: `apps/api/src/matchlayer_api/core/logging.py`

```python
_PII_KEY_PATTERN = re.compile(
    r"password|token|secret|email|resume_text|parsed_text",
    re.IGNORECASE,
)
```

Any field whose name matches that pattern has its value swapped for a single,
greppable sentinel so an accidental leak is both blocked and easy to spot after
the fact:

Source: `apps/api/src/matchlayer_api/core/logging.py`

```python
_REDACTED_VALUE = "***REDACTED***"
```

The processor chain is assembled in a fixed order — merge the per-request context
first, then redact, then stamp the level and an ISO-8601 Coordinated Universal
Time (UTC) timestamp — so the redaction step always runs before any renderer.
The merged context fields (the request identifier, route, and method) are bound
by the request-id middleware described in
[the request-id middleware](03-backend-08-request-id-middleware.md). In production the chain
ends by formatting any exception and rendering one line of JSON per entry:

Source: `apps/api/src/matchlayer_api/core/logging.py`

```python
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())
```

The renderer is chosen from the validated settings object: a development
environment gets a colourised console renderer for terminal reading, while every
other environment gets the strict JSON renderer above so logs ingested by a
hosted collector parse cleanly. Caching of bound loggers is deliberately left
off so test helpers that swap the processor chain (and modules that build a
logger at import time) observe the configured chain rather than a frozen one.

## Common pitfalls

- **Mistake:** Configuring the logging library after other modules have already created their loggers and emitted lines.
  **Symptom:** Early startup entries come out in the wrong format or are missing fields, and test helpers that capture logs see nothing because the captured chain is not the one in use.
  **Recovery:** Call the configuration function once, as the first step of application startup, and disable per-logger caching so loggers built at import time still observe the configured chain.

- **Mistake:** Relying on each call site to omit sensitive values instead of redacting centrally.
  **Symptom:** A password, token, or email address shows up in the log output after one author forgets the rule in one place.
  **Recovery:** Add a redaction processor near the front of the chain that scrubs values by field name, so the policy is enforced for every entry regardless of the call site.

- **Mistake:** Putting the redaction step after the renderer, or after a processor that has already serialised the entry.
  **Symptom:** Sensitive values still appear in the written output because the bytes were produced before redaction ran.
  **Recovery:** Order the chain so redaction runs before any renderer; the renderer must be the last processor.

- **Mistake:** Emitting human-prose sentences with values interpolated into the message string instead of passing named fields.
  **Symptom:** Queries like "all entries slower than 500 milliseconds" require fragile text parsing and break when wording changes.
  **Recovery:** Pass values as keyword fields (for example, `latency_ms=35`) so each one becomes a queryable key in the rendered JSON.

## External reading

- [structlog documentation: getting started](https://www.structlog.org/en/stable/getting-started.html)
- [structlog documentation: processors](https://www.structlog.org/en/stable/processors.html)
- [structlog documentation: context variables](https://www.structlog.org/en/stable/contextvars.html)
- [Python documentation: the logging facility](https://docs.python.org/3/library/logging.html)
- [Python documentation: contextvars](https://docs.python.org/3/library/contextvars.html)
