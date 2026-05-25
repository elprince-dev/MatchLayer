"""HTTP routers for the MatchLayer API.

Each submodule under :mod:`matchlayer_api.api` exposes a feature-scoped
:class:`fastapi.APIRouter` that the application factory in
:mod:`matchlayer_api.main` mounts onto the FastAPI app. The module
boundary is intentionally one-way: routers depend on
:mod:`matchlayer_api.core` (logging, db, middleware, errors) and on
feature-local services, never on each other or on ``main``
(``structure.md``).

Phase 1 foundation ships only :mod:`matchlayer_api.api.health`; later
specs add ``auth``, ``resumes``, and ``matches`` packages here.
"""
