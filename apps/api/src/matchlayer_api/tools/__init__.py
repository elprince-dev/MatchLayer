"""Developer/codegen tooling for the MatchLayer API.

Each module under :mod:`matchlayer_api.tools` is a self-contained CLI
intended to be invoked via ``python -m matchlayer_api.tools.<name>``.
Tools live inside the API source tree (rather than a sibling
``apps/api/scripts/`` directory) so they share the package's mypy
strict scope, ruff config, and import path — every tool is a typed
first-class citizen, not a loose script.

Phase 1 foundation ships only :mod:`matchlayer_api.tools.dump_openapi`,
the OpenAPI spec dumper consumed by the codegen orchestrator at
``packages/shared-types/scripts/codegen.mjs`` (Design §6.9, §8.1).
"""
