"""Resume HTTP surface for the MatchLayer API.

Houses the FastAPI ``Resumes_Router`` and its Pydantic response models.
Per the phase-1-matching design "Components and Interfaces" section,
this package owns only HTTP-shape concerns; all business logic
(upload, validation, extraction orchestration, retrieval, soft delete)
is delegated to :mod:`matchlayer_api.services.resumes`.

Submodules:
  * :mod:`.schemas` -- Pydantic v2 response models, source of truth for
    the resume section of the OpenAPI schema consumed by ``pnpm codegen``.
  * ``.router`` -- FastAPI ``APIRouter`` mounted on ``/api/v1/resumes``.
    Added in a later sub-task; see ``tasks.md`` 10.6.
"""
