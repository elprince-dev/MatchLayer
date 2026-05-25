"""Emit the API's OpenAPI 3.x spec as JSON to stdout.

The codegen orchestrator at
``packages/shared-types/scripts/codegen.mjs`` (Design §8.1, §8.2)
shells out to this module to obtain the live OpenAPI spec, then feeds
it to ``openapi-typescript`` and ``openapi-zod-client`` to regenerate
the shared TypeScript types and Zod schemas. The spec is the single
source of truth for the API contract — the orchestrator never reads a
cached or stale ``openapi.json`` (Requirement 7.7).

Invocation::

    uv run --project apps/api python -m matchlayer_api.tools.dump_openapi

Behavior:

* Imports :func:`matchlayer_api.main.create_app` and calls it. This
  builds a fresh :class:`fastapi.FastAPI` instance with all routers
  mounted but **without** entering the lifespan — ``app.openapi()`` is
  a pure synchronous traversal of the registered routes (Design §6.9).
  Concretely: this command does NOT require Postgres, Redis, or MinIO
  to be running. It DOES require ``.env`` to exist at the repo root so
  :class:`~matchlayer_api.config.Settings` can validate at import time
  (``cp .env.example .env`` from §2.2 is sufficient).
* Calls ``app.openapi()`` to materialise the spec dict.
* Writes ``json.dumps(spec, indent=2)`` to ``sys.stdout`` followed by a
  trailing newline so shells, pipes, and ``>`` redirects produce a
  POSIX-compliant final line (the orchestrator pipes stdout straight
  into ``openapi.json``).
* Uses ``sort_keys=False`` so the emitted JSON preserves FastAPI's
  natural ordering of paths/operations/components. The downstream
  generators (``openapi-typescript``, ``openapi-zod-client``) emit
  ``api-types.ts`` / ``api-schemas.ts`` in declaration order, and the
  CI ``openapi-drift`` job (§9.1) diffs those files byte-for-byte —
  preserving FastAPI's order keeps regeneration idempotent across
  runs.

This module is intentionally a "stdin/stdout filter" with no flags: no
argparse, no click, no typer. The orchestrator owns the pipe; this
script's only job is to print valid JSON. Errors during import
(missing ``.env``, malformed Settings) propagate as a non-zero exit
through Python's default exception handling and ``execa`` in the
orchestrator surfaces them as a build failure (Design §8.2).

Design reference: §6.9, §8.1, §8.2.
Requirements covered: 7.2, 7.7.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from matchlayer_api.main import create_app


def main() -> None:
    """Build the app, dump its OpenAPI spec, and write JSON to stdout.

    ``app.openapi()`` is FastAPI's documented public API for retrieving
    the generated spec. It returns ``dict[str, Any]`` — a JSON-shaped
    Python dict that :func:`json.dumps` can serialise without any
    custom encoder.

    The function deliberately:

    * Does not invoke ``uvicorn`` or any lifespan hook — building the
      app is enough to register every router with the FastAPI
      instance, which is all ``app.openapi()`` needs.
    * Does not catch exceptions. A failure here (e.g.,
      :class:`pydantic.ValidationError` because ``.env`` is missing a
      required key) must surface as a non-zero exit so the codegen
      orchestrator fails the CI build rather than silently emitting a
      stale/empty spec.
    """
    app = create_app()
    spec: dict[str, Any] = app.openapi()
    # ``indent=2`` keeps the spec human-diffable in PRs that touch
    # ``openapi.json`` artifacts; ``sort_keys=False`` preserves
    # FastAPI's declaration order so the generated TS/Zod files diff
    # cleanly across runs (see module docstring).
    sys.stdout.write(json.dumps(spec, indent=2, sort_keys=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
