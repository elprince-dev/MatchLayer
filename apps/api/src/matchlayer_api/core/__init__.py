"""Cross-cutting infrastructure for the MatchLayer API.

Modules in this package own concerns that aren't tied to a single feature
router: structured logging, request-id middleware, RFC 7807 error
handling, and the async SQLAlchemy engine/session wiring. Feature
routers under :mod:`matchlayer_api.api` import from here; nothing in
``core`` imports from a feature router, keeping the dependency direction
one-way (``structure.md``).
"""
