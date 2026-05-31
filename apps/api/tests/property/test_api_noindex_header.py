"""Feature: phase-1-matching — Property 23.

Property 23: Every API response is marked non-indexable.

    *For any* request to a ``/api/v1/*`` endpoint defined by this spec and
    *any* resulting outcome (success or an error status such as 401, 404,
    413, 415, 422, 429, 503), the response carries the header
    ``X-Robots-Tag: noindex, nofollow``.

**Validates: Requirements 15.3**

This module is the universal companion to the integration coverage of the
resume/match endpoints. Where those tests drive concrete HTTP requests against
the fully-wired application, this file asserts the *header invariant* of
:class:`~matchlayer_api.core.middleware.ApiNoIndexMiddleware` holds across a
wide, generated space of request paths and response status codes using
Hypothesis (>=100 examples).

The middleware — never the full :func:`~matchlayer_api.main.create_app` — is the
subject. Building the real app would pull in the auth/DB/Redis dependencies, none
of which this privacy-header invariant depends on. Instead each test wraps a tiny
Starlette app in ``ApiNoIndexMiddleware`` *and nothing else*, mirroring the
production wiring where it is the outermost user middleware. Starlette always
composes two framework middlewares around the user stack::

    ServerErrorMiddleware  →  [ ApiNoIndexMiddleware ]  →  ExceptionMiddleware  →  routes

``ApiNoIndexMiddleware`` therefore sits *inside* the catch-all 500 layer
(``ServerErrorMiddleware``) but *outside* the layer that turns registered
exceptions into RFC 7807 envelopes (``ExceptionMiddleware``). This is the exact
topology ``create_app`` sets up (it adds ``ApiNoIndexMiddleware`` last, so it is
the outermost user middleware, wrapping the exception-handling layer). It is the
property that lets the header survive on handler-produced 4xx/5xx envelopes —
and it is why Property 23 enumerates 401/404/413/415/422/429/503 (all produced by
registered handlers, inside ``ExceptionMiddleware``) but not 500 (produced by
``ServerErrorMiddleware``, outside every user middleware).

The invariant is proven through three complementary lenses:

* **Any status on any ``/api/v1/*`` path.** For a generated request path under
  ``/api/v1/`` and a generated status code spanning the whole 200..599 space, the
  response that the downstream app *returns* carries exactly
  ``X-Robots-Tag: noindex, nofollow``.

* **Handler-produced error envelopes.** For each error status the property
  enumerates, a route that *raises* a :class:`MatchLayerError` (the real
  spec error types) — turned into an RFC 7807 envelope by a registered handler
  exactly as production does — still carries the header. This is the case the
  middleware ordering exists to guarantee, so it is checked explicitly rather
  than left to the returned-Response generator.

* **The complement.** For a generated path that is *not* under ``/api/v1/`` (the
  marketing/static/health surface, ``/api/v2/*``, the bare ``/api/v1`` with no
  trailing slash, etc.), the header is **absent** regardless of status code —
  the middleware marks only the versioned API surface and must not leak the
  directive onto public, indexable pages.

The middleware reads ``scope["path"]`` directly, so a single catch-all route is
enough to produce a response for every generated path; routing never alters the
path the middleware inspects.
"""

from __future__ import annotations

from collections.abc import Callable
from string import ascii_lowercase, digits
from typing import Final

from hypothesis import example, given, settings
from hypothesis import strategies as st
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from matchlayer_api.core.errors import (
    MalformedUploadError,
    MatchLayerError,
    NotFoundError,
    PayloadTooLargeError,
    QuotaExceededError,
    ResumeNotExtractableError,
    UnsupportedMediaTypeError,
)
from matchlayer_api.core.middleware import ApiNoIndexMiddleware

# ---------------------------------------------------------------------------
# The exact header the middleware must stamp (Requirement 15.3 / Property 23).
# ---------------------------------------------------------------------------
_X_ROBOTS_TAG_HEADER: Final[str] = "x-robots-tag"
_EXPECTED_VALUE: Final[str] = "noindex, nofollow"

# Header the test routes read to learn which status code to emit. Driving the
# status through a request header lets one catch-all route serve every example.
_STATUS_HEADER: Final[str] = "x-test-status"


# ---------------------------------------------------------------------------
# App builders. Each wraps a tiny Starlette app in ``ApiNoIndexMiddleware`` and
# nothing else, reproducing the production middleware position exactly.
# ---------------------------------------------------------------------------


async def _echo(request: Request) -> Response:
    """Return an empty-body response whose status is taken from a header.

    A single catch-all route handles every generated path; the middleware keys
    off ``scope["path"]`` (untouched by routing), so this one endpoint exercises
    both the ``/api/v1/*`` and the complement surfaces.
    """
    code = int(request.headers[_STATUS_HEADER])
    return Response(status_code=code)


def _build_echo_client() -> TestClient:
    """A client over an app that *returns* a response for any path/status."""
    app = Starlette(
        routes=[Route("/{path:path}", _echo, methods=["GET", "POST"])],
        middleware=[Middleware(ApiNoIndexMiddleware)],
    )
    # ``redirect_slashes`` off so the catch-all never trampolines a path
    # through a 307 that would change the status the test asserts on.
    app.router.redirect_slashes = False
    return TestClient(app)


# Real spec error types keyed by the status Property 23 enumerates. The two that
# production raises from auth/rate-limit dependencies rather than a
# ``MatchLayerError`` subclass (401, 503) are represented by a ``MatchLayerError``
# carrying that status/type — the envelope and the ASGI position are identical,
# which is all the header invariant depends on.
_ERROR_FACTORY_BY_STATUS: Final[dict[int, Callable[[], MatchLayerError]]] = {
    401: lambda: MatchLayerError(
        "Missing or invalid authentication credentials.",
        status_code=401,
        error_type="unauthenticated",
        title="Unauthenticated",
    ),
    404: lambda: NotFoundError("Not Found"),
    413: lambda: PayloadTooLargeError("Payload Too Large"),
    415: lambda: UnsupportedMediaTypeError("Unsupported Media Type"),
    422: lambda: MalformedUploadError("Malformed Upload"),
    429: lambda: QuotaExceededError("Quota Exceeded"),
    503: lambda: MatchLayerError(
        "Rate limiter temporarily unavailable.",
        status_code=503,
        error_type="rate_limiter_unavailable",
        title="Service Unavailable",
    ),
}

# An extra 422 type so both 422-mapped spec errors are exercised on the error
# path, not only ``MalformedUploadError``.
_EXTRA_422_FACTORY: Final[Callable[[], MatchLayerError]] = lambda: ResumeNotExtractableError(  # noqa: E731
    "Resume Not Extractable"
)


async def _raise(request: Request) -> Response:
    """Raise the spec error mapped to the requested status (never returns)."""
    code = int(request.headers[_STATUS_HEADER])
    raise _ERROR_FACTORY_BY_STATUS[code]()


async def _matchlayer_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render a :class:`MatchLayerError` as the canonical RFC 7807 envelope.

    Mirrors ``core.errors.matchlayer_error_handler`` / ``_problem_response``
    (kept inline so the test stays hermetic — it needs no ``Settings``, DB, or
    Redis). Because it is registered for ``MatchLayerError`` it is dispatched by
    Starlette's ``ExceptionMiddleware`` (inside the user middleware), so the
    response flows back out through ``ApiNoIndexMiddleware`` exactly as a real
    error envelope does.
    """
    assert isinstance(exc, MatchLayerError)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": exc.error_type,
            "title": exc.title,
            "detail": exc.detail,
            "status": exc.status_code,
            "request_id": None,
        },
    )


def _build_error_client() -> TestClient:
    """A client over an app whose ``/api/v1/raise`` route *raises* a spec error."""
    app = Starlette(
        routes=[Route("/api/v1/raise", _raise, methods=["GET"])],
        middleware=[Middleware(ApiNoIndexMiddleware)],
        exception_handlers={MatchLayerError: _matchlayer_error_handler},
    )
    return TestClient(app)


# Built once and reused across examples: every app is stateless, so sharing the
# in-process client keeps the generated runs fast without any cross-example
# coupling.
_ECHO_CLIENT: Final[TestClient] = _build_echo_client()
_ERROR_CLIENT: Final[TestClient] = _build_error_client()


# ---------------------------------------------------------------------------
# Smart generators.
# ---------------------------------------------------------------------------

# URL-safe path segments only, so the path the middleware sees
# (``scope["path"]``) is exactly what the generator produced — no percent
# encoding that could shift the ``/api/v1/`` prefix check.
_PATH_SEGMENT = st.text(alphabet=ascii_lowercase + digits + "-_", min_size=1, max_size=12)

# Any path under the versioned API surface. The empty segment list yields the
# bare ``/api/v1/`` prefix, which still satisfies ``startswith("/api/v1/")``.
_api_v1_path = st.lists(_PATH_SEGMENT, min_size=0, max_size=4).map(
    lambda segs: "/api/v1/" + "/".join(segs)
)

# Any path that is NOT under ``/api/v1/``: an arbitrary path with the prefix
# filtered out. Catches the bare ``/`` root and near-misses like ``/api/v1``
# (no trailing slash) and ``/api/v10/...`` that must stay unmarked.
_non_api_v1_path = (
    st.lists(_PATH_SEGMENT, min_size=0, max_size=4)
    .map(lambda segs: "/" + "/".join(segs))
    .filter(lambda p: not p.startswith("/api/v1/"))
)

# The full HTTP status space the property quantifies over.
_status_code = st.integers(min_value=200, max_value=599)


# ---------------------------------------------------------------------------
# Property 23 — any status on any /api/v1/* path carries the header.
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(path=_api_v1_path, status_code=_status_code)
@example(path="/api/v1/", status_code=200)
@example(path="/api/v1/resumes", status_code=201)
@example(path="/api/v1/matches/0190aaaa-0000-7000-8000-000000000001", status_code=200)
@example(path="/api/v1/resumes", status_code=413)
@example(path="/api/v1/matches", status_code=429)
@example(path="/api/v1/resumes", status_code=599)
def test_every_api_v1_response_is_marked_noindex(path: str, status_code: int) -> None:
    """``X-Robots-Tag: noindex, nofollow`` lands on every ``/api/v1/*`` response.

    Property 23 (Requirement 15.3): for any path under ``/api/v1/`` and any
    status code in 200..599 that the downstream app returns, the response
    carries exactly the non-indexing header.
    """
    response = _ECHO_CLIENT.request(
        "GET",
        path,
        headers={_STATUS_HEADER: str(status_code)},
        follow_redirects=False,
    )

    assert response.status_code == status_code
    assert response.headers.get(_X_ROBOTS_TAG_HEADER) == _EXPECTED_VALUE


# ---------------------------------------------------------------------------
# Property 23 (error path) — handler-produced RFC 7807 envelopes carry it too.
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(status_code=st.sampled_from(sorted(_ERROR_FACTORY_BY_STATUS)))
def test_handler_produced_error_envelopes_are_marked_noindex(status_code: int) -> None:
    """The header survives the RFC 7807 error envelopes the property enumerates.

    Property 23 (Requirement 15.3): a ``/api/v1/*`` route that *raises* a spec
    :class:`MatchLayerError` — rendered into an RFC 7807 envelope by a registered
    handler inside Starlette's ``ExceptionMiddleware``, exactly as production
    does — still carries ``X-Robots-Tag: noindex, nofollow`` on the
    401/404/413/415/422/429/503 response. This is the case the middleware
    ordering (outermost user middleware, wrapping the exception layer) exists to
    guarantee.
    """
    response = _ERROR_CLIENT.request(
        "GET",
        "/api/v1/raise",
        headers={_STATUS_HEADER: str(status_code)},
        follow_redirects=False,
    )

    assert response.status_code == status_code
    assert response.headers.get(_X_ROBOTS_TAG_HEADER) == _EXPECTED_VALUE
    # The body really is the RFC 7807 envelope produced by the handler, so the
    # header is proven to ride on a handler-rendered error response — not a
    # plain returned Response.
    body = response.json()
    assert body["status"] == status_code
    assert body["type"]


def test_both_422_spec_error_types_are_marked_noindex() -> None:
    """Both 422-mapped spec errors carry the header on a raised envelope.

    The generated error path above exercises ``MalformedUploadError`` for 422;
    this pins the sibling ``ResumeNotExtractableError`` so the second real 422
    type is covered too.
    """

    async def _raise_not_extractable(_request: Request) -> Response:
        raise _EXTRA_422_FACTORY()

    app = Starlette(
        routes=[Route("/api/v1/raise-422", _raise_not_extractable, methods=["GET"])],
        middleware=[Middleware(ApiNoIndexMiddleware)],
        exception_handlers={MatchLayerError: _matchlayer_error_handler},
    )
    client = TestClient(app)

    response = client.get("/api/v1/raise-422", follow_redirects=False)

    assert response.status_code == 422
    assert response.headers.get(_X_ROBOTS_TAG_HEADER) == _EXPECTED_VALUE
    assert response.json()["type"] == "resume_not_extractable"


# ---------------------------------------------------------------------------
# Complement — non-/api/v1 paths never carry the header.
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(path=_non_api_v1_path, status_code=_status_code)
@example(path="/", status_code=200)
@example(path="/healthz", status_code=200)
@example(path="/api", status_code=200)
@example(path="/api/", status_code=200)
@example(path="/api/v1", status_code=200)  # no trailing slash: not under /api/v1/
@example(path="/api/v2/resumes", status_code=200)
@example(path="/api/v10/resumes", status_code=200)  # near-miss prefix
@example(path="/pricing", status_code=200)
@example(path="/docs", status_code=404)
def test_non_api_v1_paths_are_never_marked_noindex(path: str, status_code: int) -> None:
    """Paths outside ``/api/v1/`` never receive the non-indexing header.

    The complement of Property 23: ``ApiNoIndexMiddleware`` marks *only* the
    versioned API surface. For any path not under ``/api/v1/`` — the public
    marketing/static surface, the health probe, ``/api/v2/*``, or the bare
    ``/api/v1`` with no trailing slash — the header is absent for every status
    code, so the directive can never leak onto an indexable public page.
    """
    response = _ECHO_CLIENT.request(
        "GET",
        path,
        headers={_STATUS_HEADER: str(status_code)},
        follow_redirects=False,
    )

    assert response.status_code == status_code
    assert _X_ROBOTS_TAG_HEADER not in response.headers
