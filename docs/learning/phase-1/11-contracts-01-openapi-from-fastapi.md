# OpenAPI and how FastAPI generates it

## Introduction

This document explains what a machine-readable description of a web service's
contract is, and how a modern Python web framework produces that description
automatically from the code that handles requests. The description format is
OpenAPI — an open, widely adopted specification for describing a web
Application Programming Interface (API), where an API is the set of endpoints
one program exposes for another program to call. The framework is FastAPI, a
Python library for building such endpoints that reads the typed definitions you
write and turns them into both running request handlers and the OpenAPI
description of those handlers. The description is emitted as JavaScript Object
Notation (JSON), a plain-text format that stores data as key/value pairs inside
braces. This topic sits in the Contracts and codegen track because the
generated description is the single source of truth that downstream tools read
to produce matching client code, where code generation (codegen) is the
practice of producing source files mechanically from that description rather
than writing them by hand.

**Learning outcomes** — after reading this document you will be able to:

- Explain what an OpenAPI document is and which parts of an API it describes.
- Describe how a type-driven framework derives an OpenAPI document from endpoint declarations and typed models instead of from a hand-written file.
- Identify the document fields that come from the application object's own configuration, such as its title and version.
- Recognise the common mistakes that make a generated document inaccurate and recover from them.

Prerequisites: this document builds on
[FastAPI and the application-factory pattern](03-backend-01-fastapi-application-factory.md),
which explains the function that builds the web application whose routes are
described, and on
[Pydantic and pydantic-settings](03-backend-02-pydantic-and-pydantic-settings.md), which
explains the typed request and response models the framework reads to describe
each endpoint. It is complemented by
[the OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md), which explains
the small program that prints the generated document on demand.

## Problem it solves

A web service and every program that calls it must agree on a contract: which
endpoints exist, what each request body must contain, and what shape each
response takes. The concrete problem is keeping that contract both complete and
honest as the service grows. A description that lives apart from the code has no
mechanical tie to reality, so it drifts the moment someone adds a field to a
response and forgets to write it down. The mismatch then surfaces far away, as a
confusing runtime failure in a caller, rather than as an early, obvious error.

The prior approach was a description maintained by hand — a separate file that a
developer edited whenever an endpoint changed, relying on discipline to keep it
current. That state has real costs. Every consumer has to trust that the file is
up to date, with no way to confirm it against the running service. The file
records only what its author remembered to record, so optional fields, error
shapes, and data types quietly fall out of sync. Under deadlines, large changes,
or many contributors, the discipline fails and the description becomes fiction.

Deriving the description from the service's own typed definitions removes the
drift at its root: the description is computed from the same declarations that
handle the requests, so it cannot disagree with the implementation. The contract
becomes a by-product of writing typed endpoints, not a separate chore, and any
tool that needs the current contract can obtain it from the code rather than
from a document that might be stale.

## Mental model

Think of an OpenAPI document as the printed spec sheet for a machine, produced
directly from the machine's own assembled parts rather than from a designer's
notes. Because the sheet is generated from what was actually built, it always
matches the product on the floor; nobody keeps a drawer of older sheets that
might disagree with the current machine.

When the framework builds that spec sheet, it works through these steps:

1. Collect every endpoint you declared, each paired with the path and the request method that reach it.
2. Read the typed parameters and the typed request and response models attached to each endpoint.
3. Translate each type into a schema — a structured description of a value's fields and their data types — and gather the schemas into a reusable components section.
4. Assemble a single document object: a top-level information block (the title and version), a paths block listing every endpoint, and the components block holding the shared schemas.
5. Hand that document object back to the caller as plain data, ready to be written out as JSON.

Each step turns code you already wrote into one section of the document, so the
finished description is a faithful mirror of the declared interface rather than a
separate artifact that has to be maintained alongside it.

## How it works

A modern, type-driven web framework already holds everything needed to describe
its own contract. Each endpoint is declared as a function annotated with a path,
a request method, typed parameters, and typed request and response models. As
the framework registers each endpoint, it records those declarations in an
in-memory routing table. Asking the framework for its OpenAPI document triggers
a pure traversal of that table: it walks every registered endpoint, reads the
associated types, and assembles one document object describing the whole
interface. No network calls and no live traffic are involved, because the
description comes from the registered declarations, not from observed requests.

The translation from a typed model to a schema is the heart of the process. A
schema is a structured description of a value — its fields, each field's data
type, and which fields are required. The framework converts each declared model
into a schema and stores the reusable ones in a shared components section, so an
endpoint that returns a given model references the schema by name instead of
repeating it. The result is a document with three layers that fit together: an
information block carrying the interface's title and version, a paths block that
maps every endpoint to the operations available on it, and the components block
that holds the named schemas the operations point at.

Two properties make this generated document dependable. First, it is derived,
not authored: the title and version come from how the application object was
configured, and every path and schema comes from a declaration in the code, so
there is no separate file for anyone to forget to update. Second, it is data,
not text: the framework returns the document as an ordinary in-memory mapping of
keys to values, which a standard serializer can render to JSON without any custom
encoding. Because the document is rebuilt from the routing table each time it is
requested, it reflects the current code rather than a remembered snapshot, and
the only way to change the contract is to change the typed declarations the
document is computed from.

A subtle but important detail is ordering. The framework emits the document's
keys in the order the endpoints and models were declared rather than re-sorting
them alphabetically. Downstream generators read the document top to bottom and
emit their output in the same order, and an automated drift check often compares
those generated files byte-for-byte across runs. Preserving the natural
declaration order keeps regeneration deterministic, so an unrelated change does
not reshuffle the whole document and trip the check.

## MatchLayer Phase 1 usage

In MatchLayer the application object is built by the factory function
`create_app` in `apps/api/src/matchlayer_api/main.py`. The information block of
the generated document — the title and the version a caller reads first — comes
directly from the arguments passed when that object is constructed:

Source: `apps/api/src/matchlayer_api/main.py`

```python
    app = FastAPI(
        title="MatchLayer API",
        version="0.0.0",
```

The `title` and `version` here become the document's information block; changing
them changes what every consumer of the contract sees, which is why they live
beside the application object's construction rather than in a hand-edited file.
Every endpoint mounted onto this object — and every typed model those endpoints
declare — is what populates the document's paths and components sections when the
description is generated.

Generating the description is a single call. The dump tool at
`apps/api/src/matchlayer_api/tools/dump_openapi.py` builds a fresh application
through the same factory and then asks it for the document:

Source: `apps/api/src/matchlayer_api/tools/dump_openapi.py`

```python
    app = create_app()
    spec: dict[str, Any] = app.openapi()
```

The `app.openapi()` call is the framework's documented way to retrieve the
generated document; it returns a JSON-shaped dictionary (`dict[str, Any]`) that a
standard JSON serializer can write without a custom encoder. Building the
application is enough to register every endpoint, so the call needs no database
and no running server — it reads the in-memory routing table that construction
filled. The printed document is then consumed by the codegen orchestrator at
`packages/shared-types/scripts/codegen.mjs`, which feeds it to the downstream
type and schema generators; that delivery path is covered in
[the OpenAPI dump command-line interface](03-backend-10-openapi-dump-cli.md). Because the
document is rebuilt from the live code on every call, there is no cached copy to
fall out of step with the endpoints it describes.

## Common pitfalls

- **Mistake:** Declaring an endpoint without a typed request or response model, returning a loosely typed value instead.
  **Symptom:** The generated document carries an empty or permissive schema for that endpoint, and downstream typed clients fall back to an untyped value, losing the contract guarantees the document is supposed to provide.
  **Recovery:** Annotate the endpoint with explicit request and response models so the framework has concrete types to translate into precise schemas.

- **Mistake:** Treating a saved copy of the generated document as the contract and editing it by hand to fix or extend a description.
  **Symptom:** The hand edits disappear the next time the document is regenerated, or the saved copy and the live code disagree about an endpoint's shape.
  **Recovery:** Change the typed endpoint declarations in the code and regenerate the document; never hand-edit the generated output, and treat any committed copy as a build artifact rather than the source of truth.

- **Mistake:** Leaving the document's title and version unset or bumping the version arbitrarily, disconnected from real interface changes.
  **Symptom:** Consumers cannot tell one release of the contract from another, and the version reported in the document does not reflect what actually changed.
  **Recovery:** Set the title and version where the application object is constructed, and change the version deliberately as part of a real contract change so the information block stays meaningful.

- **Mistake:** Returning a value whose type the JSON serializer cannot represent, such as a raw object with no schema mapping.
  **Symptom:** Generating or writing the document raises an error, and the description fails to emit at all rather than emitting something incomplete.
  **Recovery:** Model responses with serializable types the framework can translate into schemas, so the generated document is always valid JSON.

## External reading

- [FastAPI: features and automatic interactive documentation](https://fastapi.tiangolo.com/features/)
- [FastAPI: first steps and the automatically generated contract](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI: extending the generated OpenAPI document](https://fastapi.tiangolo.com/how-to/extending-openapi/)
- [Python documentation: the json module](https://docs.python.org/3/library/json.html)
