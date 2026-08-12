"""Feature: phase-3-llm-layer — Property 16.

# Feature: phase-3-llm-layer, Property 16: Ownership indistinguishability

Property 16: Ownership indistinguishability.

    *For any* authenticated User_Account, any LLM_Feature endpoint, and
    any Match_Result identifier that is either owned by a different user
    or nonexistent, the response is a 404 RFC 7807 with identical
    status, ``type``, and body shape in both cases, and no provider call
    or LLM pipeline work occurs.

**Validates: Requirements 5.6, 16.2**

Why this drives the real HTTP surface (with Postgres)
-----------------------------------------------------
The guarantee is an HTTP-level one: the two failure causes — a
Match_Result row that exists but belongs to someone else, and a row that
does not exist at all — must be *indistinguishable from outside*. That
can only be proven by issuing real requests through the full stack
(auth dependency → ``_load_owned_match`` → ``Scoring_Service.get_match``
→ the registered ``NotFoundError`` handler), because the
indistinguishability rests on both causes flowing through the byte-same
lookup and envelope machinery. The design's Testing Strategy explicitly
allows "Postgres-in-Docker sessions for persistence-dependent properties
per the existing integration-test setup", so this module reuses that
harness shape (``create_app`` + ``get_session`` dependency override +
direct row inserts) and skips when docker-compose is not running —
mirroring ``tests/integration/test_llm_api.py``, whose example-based
404 test this property generalizes.

What Hypothesis quantifies over (>=100 examples)
------------------------------------------------
* the **LLM_Feature endpoint**: all three sub-resources crossed with
  all three operations (POST generate, GET list, GET one) — the full
  nine-route surface of Requirement 16.1;
* the **authenticated User_Account**: sampled from two pre-created
  requesters — one owning no data at all, and one owning a match of its
  own (owning *something* must not help you see someone else's rows);
* the **nonexistent identifier**: random UUIDs and malformed non-UUID
  path segments (a malformed id denotes no existing Match_Result, and
  the router maps it onto the same single 404);
* the **other-owner target**: the owner's real match id — and for
  GET-one, the owner's real persisted LLM_Result id, the strongest leak
  candidate (probing the exact id of another user's row).

Each example issues the *pair* of requests — same requester, same
feature, same operation; one aimed at the other user's real rows, one at
a nonexistent identifier — and asserts:

1. both responses are 404;
2. the RFC 7807 bodies are identical apart from ``request_id``
   (same ``type`` ``not_found``, same ``title``, same ``detail``, same
   ``status``) — so the body *shape and content* leak nothing;
3. the body carries exactly the canonical envelope keys;
4. the LLM pipeline was never entered: the router's
   ``_build_orchestrator`` factory is replaced for the whole module by a
   sentinel that records and raises — any request that survived the
   ownership check would blow up with a distinctive error (and the
   recorded-calls list is asserted empty after every example), so no
   provider call, quota reservation, redaction, or cache work can occur.

The Daily_Quota / cache / breaker dependencies are never reached on
these paths (the 404 is raised before them), and the module pins the
key-absent availability state as a belt-and-braces guard against any
provider call. POSTs to ``bullet-rewrites`` carry a *valid* body so the
404 (not the 422 body validation) is the behavior being observed.

Async note: Hypothesis drives sync test functions, so a module-scoped
harness owns a single ``asyncio.Runner`` (one event loop for the app
client and DB session across all examples); every request is read-only,
so examples are independent by construction. Rows are only flushed —
never committed — and the teardown rollback leaves the database clean.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, Final
from unittest import mock

import pytest
from httpx import ASGITransport, AsyncClient, Response
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

import matchlayer_api.api.matches.llm.router as llm_router_module
import matchlayer_api.ml.llm.availability as availability_module
from matchlayer_api.config import get_settings
from matchlayer_api.core.db import get_session
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.core.security.passwords import hash_password
from matchlayer_api.db.models import LLMResult, MatchResult, Resume, User
from matchlayer_api.main import create_app
from matchlayer_api.ml.prompts.registry import LLMFeature

# ---------------------------------------------------------------------------
# Infra availability (self-contained socket checks — the integration
# conftest is a different pytest package, so its helpers are mirrored
# here rather than imported).
# ---------------------------------------------------------------------------


def _service_available(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not (_service_available("127.0.0.1", 5432) and _service_available("127.0.0.1", 6379)),
    reason="Postgres and Redis required (docker-compose not running)",
)

# ---------------------------------------------------------------------------
# The nine-route surface: feature path segment → operations. ``post``
# generates, ``list`` reads a page, ``get_one`` reads a single result.
# ---------------------------------------------------------------------------

_FEATURE_PATHS: Final[dict[str, LLMFeature]] = {
    "coaching-reports": LLMFeature.RESUME_COACH,
    "bullet-rewrites": LLMFeature.BULLET_REWRITE,
    "interview-question-sets": LLMFeature.INTERVIEW_QUESTIONS,
}
_OPS: Final[tuple[str, ...]] = ("post", "list", "get_one")

# The canonical RFC 7807 envelope keys (``core/errors.py`` /
# ``conventions.md`` "Error shape") — the exact body *shape* both cases
# must share.
_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {"type", "title", "detail", "status", "request_id"}
)

# A valid bullet-rewrites body: the 404 must be observed on a request
# that would otherwise pass Pydantic validation (Requirement 6.3's 422
# is a different, earlier gate).
_VALID_BULLETS_BODY: Final[dict[str, Any]] = {"bullets": ["Shipped the project on time."]}

RESUME_TEXT: Final[str] = (
    "Backend engineer with production Python and FastAPI services, "
    "PostgreSQL data modeling, and Docker-based deployments on AWS."
)
JOB_DESCRIPTION: Final[str] = (
    "Hiring a backend engineer for Python REST APIs with FastAPI, "
    "PostgreSQL, Docker, and Kubernetes on AWS."
)


class _PipelineEnteredError(AssertionError):
    """Raised if any ownership-404 request reaches the LLM pipeline."""


# ---------------------------------------------------------------------------
# Harness: one event loop, one app+session, fixed rows, shared by every
# Hypothesis example (all requests are read-only 404 paths).
# ---------------------------------------------------------------------------


@dataclass
class _Harness:
    runner: asyncio.Runner
    client: AsyncClient
    owner_match_id: str
    owner_result_ids: dict[str, str]
    requester_tokens: tuple[str, ...]
    pipeline_calls: list[str] = field(default_factory=list)

    def request(
        self, *, op: str, feature_path: str, token: str, match_id: str, result_id: str
    ) -> Response:
        """Issue one LLM sub-resource request on the harness loop."""
        headers = {"Authorization": f"Bearer {token}"}
        base = f"/api/v1/matches/{match_id}/{feature_path}"
        if op == "post":
            body = _VALID_BULLETS_BODY if feature_path == "bullet-rewrites" else None
            return self.runner.run(self.client.post(base, headers=headers, json=body))
        if op == "list":
            return self.runner.run(self.client.get(base, headers=headers))
        return self.runner.run(self.client.get(f"{base}/{result_id}", headers=headers))


@dataclass
class _State:
    engine: Any
    session: AsyncSession
    client: AsyncClient
    owner_match_id: str
    owner_result_ids: dict[str, str]
    requester_tokens: tuple[str, ...]


def _make_user(prefix: str) -> User:
    return User(
        id=uuid7(),
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Password!12345"),
        display_name=prefix,
        failed_login_count=0,
        last_failed_login_at=None,
        locked_until=None,
        deleted_at=None,
    )


async def _insert_match(session: AsyncSession, *, user_id: Any) -> MatchResult:
    """Insert an owned Resume + MatchResult pair (integration-factory shape)."""
    resume = Resume(
        id=uuid7(),
        user_id=user_id,
        original_filename="resume.pdf",
        storage_key=f"{uuid7()}.pdf",
        content_type="application/pdf",
        byte_size=2048,
        extracted_text=RESUME_TEXT,
        extraction_status="succeeded",
        extraction_char_count=len(RESUME_TEXT),
        deleted_at=None,
    )
    session.add(resume)
    await session.flush()
    match = MatchResult(
        id=uuid7(),
        user_id=user_id,
        resume_id=resume.id,
        job_description_text=JOB_DESCRIPTION,
        score=72,
        score_breakdown={
            "similarity_component": 0.7,
            "keyword_coverage_component": 0.75,
            "weight_similarity": 0.6,
            "weight_keyword": 0.4,
            "final_score": 72,
        },
        matched_keywords=[{"term": "python", "weight": 1.0}],
        missing_keywords=[{"term": "kubernetes", "weight": 0.8}],
        suggestions=["Add Kubernetes experience to your resume."],
        scorer_version="test-scorer",
    )
    session.add(match)
    await session.flush()
    return match


async def _setup() -> _State:
    """Build the app client and the fixed row population (flush, no commit)."""
    engine = create_async_engine(
        str(get_settings().database_url),
        echo=False,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = session_factory()

    # The victim: owns the match every "not yours" probe targets, plus
    # one persisted LLM_Result per feature so GET-one probes a *real*
    # other-owner result id (the strongest leak candidate). The payloads
    # are never read on a 404 path.
    owner = _make_user("p16-owner")
    session.add(owner)
    await session.flush()
    owner_match = await _insert_match(session, user_id=owner.id)
    owner_result_ids: dict[str, str] = {}
    for feature_path, feature in _FEATURE_PATHS.items():
        row = LLMResult(
            id=uuid7(),
            user_id=owner.id,
            match_result_id=owner_match.id,
            feature=feature.value,
            prompt_template_version=1,
            llm_model="test-model",
            payload={"probe": "never-read-on-404"},
        )
        session.add(row)
        await session.flush()
        owner_result_ids[feature_path] = str(row.id)

    # The requesters ("any authenticated User_Account"): one owning
    # nothing, one owning a match of its own — owning *some* match must
    # not make another user's rows visible.
    requester_bare = _make_user("p16-bare")
    requester_with_match = _make_user("p16-hasmatch")
    session.add_all([requester_bare, requester_with_match])
    await session.flush()
    await _insert_match(session, user_id=requester_with_match.id)

    app = create_app()

    async def _override_session() -> Any:
        yield session

    app.dependency_overrides[get_session] = _override_session
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

    return _State(
        engine=engine,
        session=session,
        client=client,
        owner_match_id=str(owner_match.id),
        owner_result_ids=owner_result_ids,
        requester_tokens=(
            issue_access_token(sub=str(requester_bare.id)),
            issue_access_token(sub=str(requester_with_match.id)),
        ),
    )


async def _teardown(state: _State) -> None:
    await state.client.aclose()
    await state.session.rollback()
    await state.session.close()
    await state.engine.dispose()


@pytest.fixture(scope="module")
def harness() -> Iterator[_Harness]:
    """Module-scoped app + rows + pipeline sentinel, one loop for all examples."""
    pipeline_calls: list[str] = []

    def _sentinel_build_orchestrator(**_kwargs: Any) -> Any:
        pipeline_calls.append("orchestrator_built")
        raise _PipelineEnteredError(
            "LLM pipeline entered during an ownership-404 request (Property 16)"
        )

    with asyncio.Runner() as runner, ExitStack() as stack:
        # Belt and braces: key absent means no provider call is even
        # possible; the sentinel proves the pipeline is never composed.
        stack.enter_context(mock.patch.object(availability_module, "_key_present", False))
        stack.enter_context(
            mock.patch.object(
                llm_router_module, "_build_orchestrator", _sentinel_build_orchestrator
            )
        )
        state = runner.run(_setup())
        try:
            yield _Harness(
                runner=runner,
                client=state.client,
                owner_match_id=state.owner_match_id,
                owner_result_ids=state.owner_result_ids,
                requester_tokens=state.requester_tokens,
                pipeline_calls=pipeline_calls,
            )
        finally:
            runner.run(_teardown(state))


# ---------------------------------------------------------------------------
# Strategies.
# ---------------------------------------------------------------------------


def _is_not_a_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return True
    return False


# A nonexistent Match_Result identifier: either a well-formed UUID that
# matches no row, or a malformed path segment (which likewise denotes no
# existing Match_Result and maps onto the same single 404). URL-safe
# alphabet so the path the router sees is exactly what was generated.
_missing_uuid = st.uuids(version=4).map(str)
_malformed_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-",
    min_size=1,
    max_size=36,
).filter(_is_not_a_uuid)
_absent_match_id = st.one_of(_missing_uuid, _malformed_id)

_feature_path = st.sampled_from(sorted(_FEATURE_PATHS))
_op = st.sampled_from(_OPS)
_requester_index = st.sampled_from((0, 1))


def _stable(body: dict[str, Any]) -> dict[str, Any]:
    """The body minus the per-request correlation id."""
    return {k: v for k, v in body.items() if k != "request_id"}


# ---------------------------------------------------------------------------
# Property 16 — the pair of probes is indistinguishable, and the
# pipeline is never entered.
# ---------------------------------------------------------------------------


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    feature_path=_feature_path,
    op=_op,
    requester_index=_requester_index,
    absent_match_id=_absent_match_id,
    absent_result_id=_missing_uuid,
)
@example(
    feature_path="coaching-reports",
    op="post",
    requester_index=0,
    absent_match_id="00000000-0000-4000-8000-000000000000",
    absent_result_id="00000000-0000-4000-8000-000000000001",
)
@example(
    feature_path="bullet-rewrites",
    op="get_one",
    requester_index=1,
    absent_match_id="not-a-uuid",
    absent_result_id="00000000-0000-4000-8000-000000000001",
)
@example(
    feature_path="interview-question-sets",
    op="list",
    requester_index=1,
    absent_match_id="00000000-0000-4000-8000-000000000000",
    absent_result_id="00000000-0000-4000-8000-000000000001",
)
def test_not_yours_and_nonexistent_are_indistinguishable_404(
    harness: _Harness,
    feature_path: str,
    op: str,
    requester_index: int,
    absent_match_id: str,
    absent_result_id: str,
) -> None:
    """Property 16 (Requirements 5.6, 16.2): for any authenticated user,
    any of the nine LLM feature routes, and any Match_Result identifier
    that is another user's or nonexistent, the response is the identical
    404 RFC 7807 envelope in both cases — same ``type`` (``not_found``),
    same ``title``/``detail``/``status``, same body shape — and no LLM
    pipeline work (and therefore no provider call) ever occurs."""
    assume(absent_match_id != harness.owner_match_id)
    token = harness.requester_tokens[requester_index]

    # Probe 1 — another user's REAL rows: the owner's match id, and (for
    # GET-one) the owner's real persisted result id under it.
    not_yours = harness.request(
        op=op,
        feature_path=feature_path,
        token=token,
        match_id=harness.owner_match_id,
        result_id=harness.owner_result_ids[feature_path],
    )
    # Probe 2 — a nonexistent identifier (well-formed or malformed).
    nonexistent = harness.request(
        op=op,
        feature_path=feature_path,
        token=token,
        match_id=absent_match_id,
        result_id=absent_result_id,
    )

    # Identical status.
    assert not_yours.status_code == 404
    assert nonexistent.status_code == 404

    not_yours_body = not_yours.json()
    nonexistent_body = nonexistent.json()

    # Identical type and body shape: the canonical RFC 7807 envelope,
    # byte-equal apart from the per-request correlation id.
    assert set(not_yours_body) == set(nonexistent_body) == _ENVELOPE_KEYS
    assert _stable(not_yours_body) == _stable(nonexistent_body)
    assert not_yours_body["type"] == "not_found"
    assert not_yours_body["status"] == 404

    # No provider call or LLM pipeline work occurred: the sentinel
    # replacing the router's orchestrator factory was never invoked
    # (it would also have crashed the request with a distinctive error).
    assert harness.pipeline_calls == []
