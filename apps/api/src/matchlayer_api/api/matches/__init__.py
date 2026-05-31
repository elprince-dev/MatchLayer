"""Match HTTP surface for the MatchLayer API.

Houses the FastAPI ``Matches_Router`` and its Pydantic request/response
models. Per the phase-1-matching design "Components and Interfaces"
section, this package owns only HTTP-shape concerns; all business logic
(scoring orchestration, persistence, retrieval, soft delete) is delegated
to :mod:`matchlayer_api.services.matching`.

Submodules:
  * :mod:`.schemas` -- Pydantic v2 request/response models, source of
    truth for the match section of the OpenAPI schema consumed by
    ``pnpm codegen``.
  * ``.router`` -- FastAPI ``APIRouter`` mounted on ``/api/v1/matches``.
    Added in a later sub-task; see ``tasks.md`` 11.4.
"""
