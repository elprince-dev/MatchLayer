"""LLM sub-resource routers beneath ``/api/v1/matches/{match_id}/``.

The HTTP surface of the Phase 3 LLM features (phase-3-llm-layer design
§"Routers and SSE"):

* ``router.py`` — the three plural kebab-case sub-resource routers
  (``coaching-reports``, ``bullet-rewrites``, ``interview-question-sets``),
  each exposing POST / GET-list / GET-one (Requirement 16.1).
* ``schemas.py`` — the concrete response envelopes and list models the
  routers expose through OpenAPI.

Pure HTTP-shape concerns only — every pipeline stage (quota, redaction,
caching, spend control, validation, logging) lives in
:mod:`matchlayer_api.services.llm`.
"""
