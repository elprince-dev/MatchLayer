# The OpenAPI dump command-line interface

## Introduction

This document explains how a web service can describe its own contract in a
machine-readable document, and the tiny program this project uses to print that
document on demand. The contract format is OpenAPI — an open, widely adopted
specification for describing a web Application Programming Interface (API), where
an API is the set of endpoints one program exposes for another to call. An
OpenAPI document lists every path the service offers, the shape of each request
and response, and the data types involved, all written as JavaScript Object
Notation (JSON), a plain-text format that stores data as key/value pairs inside
braces. The small program that prints this document is a
command-line interface (CLI) — a program you run from a terminal that reads
input and writes output as
text rather than through a graphical window. This topic sits in the Contracts and
codegen track because the printed document is the single source of truth that
downstream tools read to generate matching client code.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an OpenAPI document is and why a machine-readable description of an API is more reliable than a hand-written one.
- Describe how a framework can generate an OpenAPI document automatically from the typed definitions of its endpoints.
- Explain why a stand-alone dump command writes only to standard output and lets failures surface as a non-zero exit.
- Recognise the common mistakes when generating and dumping an API specification and recover from them.

Prerequisites: this document builds on
[FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md),
which explains the function that builds the web application this command
inspects, and on
[Pydantic and typed settings](03-backend-02-pydantic-and-pydantic-settings.md), which explains
the typed request and response models the framework reads to describe each
endpoint.

## Problem it solves

A web service and the programs that call it must agree on a contract: which paths
exist, what each request body looks like, and what shape each response takes. The
concrete problem is keeping that contract honest. When the contract is written by
hand in a separate document, it drifts: someone adds a field to the server,
forgets to update the document, and the callers are now coding against a
description that no longer matches reality. The mismatch surfaces as confusing
runtime failures rather than as a clear, early error.

The prior approach was exactly that hand-maintained document, plus the discipline
to update it on every change. That discipline fails under real conditions —
deadlines, large changes, many contributors — and there is no automatic check
that the document still matches the code. Each consumer of the contract also has
to trust that the document is current, with no way to regenerate it from the
running service.

Generating the document automatically from the service's own typed definitions
solves the drift problem at its root: the description is derived from the same
code that handles the requests, so it cannot disagree with the implementation. A
small command that prints this generated document then gives every tool and every
contributor one reliable way to obtain the current contract — run the command,
read its output. There is no cached copy to go stale, because the command always
rebuilds the description from the live code.

## Mental model

Think of a factory that prints the spec sheet for a machine directly from the
machine's own assembled parts, rather than from a designer's notes. Because the
sheet is produced from what was actually built, it always matches the product on
the floor. Anyone who needs the current spec asks the factory to print a fresh
one; nobody keeps a drawer of old sheets that might be out of date. The printer
has exactly one job — put the current spec on paper — and if any part is missing,
the print job stops with an obvious error rather than printing a half-finished
sheet.

When the dump command runs, it performs these steps:

1. Build a fresh instance of the web application by calling its factory function, which registers every endpoint.
2. Ask the framework to materialise the OpenAPI document by traversing those registered endpoints and their typed models.
3. Serialise that document to JSON text, preserving the framework's natural ordering rather than re-sorting the keys.
4. Write the text to standard output — the default text stream a terminal or a pipe reads from — followed by a trailing newline.
5. Let any failure during the build raise an exception and exit with a non-zero status, so a caller can tell success from failure.

Because the command rebuilds the application every time it runs, the document it
prints is always derived from the current code.

## How it works

A modern, type-driven web framework already holds everything needed to describe
its own contract. Each endpoint is declared with typed parameters and typed
request and response models, and the framework records those declarations as it
registers each route. Asking the framework for its OpenAPI document triggers a
pure traversal of that registry: it walks every registered route, reads the
associated types, and assembles a single document object describing the whole API.
No network calls and no running server are required, because the description comes
from the in-memory route table, not from live traffic.

Crucially, building the application is enough; the application does not have to
_start serving_. A web application typically has a startup-and-shutdown phase —
opening database connections, warming caches — that is separate from merely
constructing the object. Generating the document only needs the object
constructed, so the dump command can build the application and read its
description without opening a single external connection. That keeps the command
fast and free of infrastructure dependencies: it does not need the database, a
cache, or object storage to be running.

The command itself is deliberately minimal — a "filter" in the classic
command-line sense, meaning a program that produces output on standard output and
leaves redirection to the surrounding shell. It takes no flags and prints only
the document. This matters because the program that consumes the output owns the
destination: an orchestrating script captures standard output and writes it to a
file or pipes it into the next tool. Keeping the dumper free of file-writing logic
means it composes cleanly with pipes and redirection and has a single, testable
responsibility.

Two smaller decisions make the output dependable for downstream tooling. First,
the keys are emitted in the framework's natural declaration order rather than
re-sorted alphabetically. Downstream generators emit their output in the order
they read the document, and a drift check often compares the generated files
byte-for-byte across runs; preserving order keeps regeneration deterministic.
Second, the command does not swallow errors. If constructing the application fails
— a missing configuration value, an invalid model — the exception propagates and
the process exits with a non-zero status. A calling script then fails loudly
rather than silently writing an empty or stale document.

## MatchLayer Phase 1 usage

In MatchLayer the dump command is the module
`apps/api/src/matchlayer_api/tools/dump_openapi.py`. Its entire job is to build
the application, ask it for the specification, and write that specification to
standard output as indented JSON:

Source: `apps/api/src/matchlayer_api/tools/dump_openapi.py`

```python
    app = create_app()
    spec: dict[str, Any] = app.openapi()
    sys.stdout.write(json.dumps(spec, indent=2, sort_keys=False))
    sys.stdout.write("\n")
```

The application it inspects is built by the same factory the running server uses,
imported at the top of the module so the dumped contract is exactly the one the
service serves:

Source: `apps/api/src/matchlayer_api/tools/dump_openapi.py`

```python
from matchlayer_api.main import create_app
```

The `app.openapi()` call is the framework's documented way to retrieve the
generated document; it returns a JSON-shaped dictionary that the standard JSON
serialiser can write without any custom encoder. The `sort_keys=False` argument
is intentional: it preserves the framework's natural ordering of paths and
components so the downstream type and schema generators produce output that diffs
cleanly across runs. The trailing newline makes the output a well-formed final
line for shells, pipes, and redirects.

The command is invoked through the project's Python runner, exactly as recorded
in the module's own documentation:

Source: `apps/api/src/matchlayer_api/tools/dump_openapi.py`

```text
    uv run --project apps/api python -m matchlayer_api.tools.dump_openapi
```

Because building the application does not enter
its startup phase, this command runs without the database, cache, or object
storage being available — it only needs the typed configuration to validate at
import time. A failure at that point (for example, a missing required setting)
raises and exits non-zero, so a calling script fails the build rather than
emitting a stale or empty document.

## Common pitfalls

- **Mistake:** Caching the generated document to a committed file and reading that copy instead of regenerating it.
  **Symptom:** Downstream client code is generated against a contract that no longer matches the server, producing confusing runtime mismatches.
  **Recovery:** Always regenerate the document from the live code on each run, and treat any committed copy as a build artifact checked by a drift job, never as the source of truth.

- **Mistake:** Re-sorting the document keys alphabetically before writing them.
  **Symptom:** Generated downstream files reorder on every run, so a byte-for-byte drift check fails even when nothing meaningful changed.
  **Recovery:** Serialise with key sorting turned off so the framework's natural declaration order is preserved and regeneration stays deterministic.

- **Mistake:** Catching exceptions during application build and printing a partial or empty document anyway.
  **Symptom:** A calling script sees a zero exit status and a truncated document, then generates broken client code from it without warning.
  **Recovery:** Let build failures propagate so the process exits non-zero, allowing the orchestrating script to stop the build at the point of failure.

- **Mistake:** Requiring the database, cache, or other services to be running merely to print the contract.
  **Symptom:** The command hangs or errors in environments where those services are absent, such as a clean continuous-integration job.
  **Recovery:** Build the application without entering its startup phase, so generating the document depends only on constructing the object and reading its route table.

## External reading

- [FastAPI documentation: extending OpenAPI](https://fastapi.tiangolo.com/how-to/extending-openapi/)
- [FastAPI documentation: first steps and the generated docs](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [Python documentation: the json module](https://docs.python.org/3/library/json.html)
- [Python documentation: sys.stdout and the standard streams](https://docs.python.org/3/library/sys.html)
