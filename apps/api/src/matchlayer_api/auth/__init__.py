"""Authentication module for the MatchLayer API.

Houses the FastAPI ``Auth_Router`` and its Pydantic request/response
models. Per the phase-1-auth design "Components and Interfaces"
section, this package owns only HTTP-shape concerns; all business
logic is delegated to :mod:`matchlayer_api.services.auth`.

Submodules:
  * :mod:`.schemas` -- Pydantic v2 request/response models, source of
    truth for the auth section of the OpenAPI schema consumed by
    ``pnpm codegen``.
  * ``.router`` -- FastAPI ``APIRouter`` mounted on ``/api/v1/auth``.
    Added in a later sub-task; see ``tasks.md`` 7.1 / 7.2.
"""
