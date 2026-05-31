"""Integration tests for the resume HTTP surface (task 10.7).

Validates Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 2.10,
3.6, 3.7, 4.4, 4.5, 4.7 (and exercises the 1.5/1.6 not-found-vs-other-owner
disclosure rule, the 2.9 safe-field-set response, and the 4.5/4.7
idempotent-delete + retained-bytes guarantees alongside them).

These tests drive the wired ``Resumes_Router`` (mounted at ``/api/v1/resumes``
by ``create_app``) end to end against the docker-compose Postgres and Redis,
reusing the existing integration harness in ``tests/integration/conftest.py``:
the ``client_with_session`` ASGI fixture (whose ``get_session`` override yields
the per-test ``db_session``), the autouse ``_truncate_auth_tables`` reset, the
``factory_user`` builder, and ``unique_email``. Authentication mirrors
``test_me.py`` and ``test_matches_api.py`` -- a real access token minted with
``issue_access_token`` and presented as ``Authorization: Bearer <token>``.

Why gate on Postgres **and** Redis (not Postgres alone, unlike the auth-only
suites): every ``Resumes_Router`` route depends on ``user_rate_limit("resume")``,
which calls the Redis-backed ``RateLimiter`` on each request, and ``POST``
additionally injects the Redis-backed ``IdempotencyStore``. Without Redis the
rate limiter fails closed (503) and the endpoints can't be exercised, so the
module skips when either service is down -- the suite stays green locally
without Docker and runs for real in CI. This mirrors the module-level skipif in
the sibling ``test_matches_api.py``; assertions are NEVER weakened to pass
without infra.

Object storage strategy (no real MinIO/S3 required): the upload path writes
bytes through ``Resume_Storage``. Rather than depend on a live MinIO, these
tests override the ``get_resume_storage`` dependency with an in-memory fake that
records every ``put`` call (key + kwargs). This lets the no-public-read
guarantee (Requirement 2.10) be asserted at the storage boundary -- the fake
verifies ``put`` issues NO public-read ACL, exactly as the production
``Resume_Storage.put`` does (it passes no ``ACL`` argument at all) -- without
needing AWS. The decision to assert 2.10 at the storage layer (vs. an
unauthenticated public GET against a live MinIO) is deliberate: the repo's CI
has no guaranteed MinIO with anonymous-read configured, and the production
guarantee lives in ``Resume_Storage.put`` not issuing an ACL, which is exactly
what the fake observes.

The ``Resume_Service`` resolves storage lazily via ``get_resume_storage()`` (the
process-wide ``@lru_cache`` factory) when no instance is injected, and the
router constructs ``Resume_Service(settings=settings)`` with no storage
argument. So the seam is the ``get_resume_storage`` factory: we monkey-patch the
name the *service module* imported (``services.resumes.get_resume_storage``) per
test so the override is hermetic and never leaks into the cached production
client.

PRIVACY (Requirements 2.6, 3.6, 3.7): ``original_filename``, the file bytes, and
the extracted resume text are Restricted PII. The "never logged" negatives use
the ``structlog.testing.capture_logs`` pattern from
``tests/unit/test_extraction_failures.py``: the upload flow runs inside a
capture context with recognizable sentinels planted in the filename, the file
bytes, and the extractable text, and we assert NO captured log event contains
any sentinel. Defense in depth: the bytes/text sentinels must also never appear
in the response body, and the ``resume_uploaded`` audit payload must carry only
the internal resume id.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.config import get_settings
from matchlayer_api.core.security.jwt import issue_access_token
from matchlayer_api.db.models import AuditEvent, Resume, User

from .conftest import UserFactory, postgres_available, redis_available, unique_email

# Both Postgres and Redis must be reachable: every resume route runs the
# Redis-backed per-user rate limiter, and POST also uses the Redis idempotency
# store. When either is down the suite skips rather than fails (CI runs them for
# real). Mirrors the module-level skipif used across the integration suite.
pytestmark = pytest.mark.skipif(
    not (postgres_available() and redis_available()),
    reason="Postgres and Redis required (docker-compose not running)",
)


# The exact safe field set Requirements 2.9 / 4.2 enumerate for a resume body.
_RESUME_RESPONSE_FIELDS = {
    "id",
    "original_filename",
    "content_type",
    "byte_size",
    "extraction_status",
    "created_at",
    "updated_at",
}

# Fields that must NEVER appear in any resume response body (Requirements 2.9,
# 4.2): the parsed PII text and the internal object-storage key.
_FORBIDDEN_RESUME_FIELDS = {"extracted_text", "storage_key"}


# ---------------------------------------------------------------------------
# In-memory object-storage fake (Requirement 2.10 assertion seam).
# ---------------------------------------------------------------------------


class _RecordingStorage:
    """In-memory stand-in for ``Resume_Storage`` that records every ``put``.

    Implements the same async ``put`` / ``get`` surface the production
    ``Resume_Storage`` exposes, but stores bytes in a dict instead of S3/MinIO.
    Every ``put`` call's keyword arguments are captured so the no-public-read
    guarantee (Requirement 2.10) can be asserted at the storage boundary: like
    the production ``Resume_Storage.put``, this fake accepts no ``ACL`` argument
    and records exactly the kwargs the service passes, so a regression that
    started issuing a public-read ACL would change the recorded call shape and
    trip the assertion in ``test_upload_storage_put_issues_no_public_read_acl``.
    """

    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.objects: dict[str, bytes] = {}

    async def put(self, *, key: str, data: bytes, content_type: str) -> None:
        # Record the call exactly as the service issued it. The production
        # ``Resume_Storage.put`` signature has no ACL parameter; capturing the
        # full kwargs here lets the 2.10 test assert no public-read grant is
        # ever passed.
        self.put_calls.append({"key": key, "data": data, "content_type": content_type})
        self.objects[key] = data

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]


# ---------------------------------------------------------------------------
# File-byte builders -- real PDF / DOCX payloads (no mocks).
#
# The Mime_Validator sniffs magic bytes, so a happy-path upload needs genuine
# PDF/DOCX bytes. The DOCX is authored with ``python-docx`` (the same library
# the extractor reads with); the PDF is a minimal but structurally valid
# single-page document with a real cross-reference table so ``pypdf`` parses
# the page tree and extracts the embedded text.
# ---------------------------------------------------------------------------


def _build_pdf_bytes(text: str) -> bytes:
    """Return a minimal, valid single-page PDF whose page text is ``text``.

    Objects are concatenated and a real ``xref`` table is computed from each
    object's byte offset, so ``pypdf`` reads the page tree and
    ``page.extract_text()`` returns ``text`` -- the extractable content the
    happy-path test relies on. ``text`` must be Latin-1 representable (it is, for
    the ASCII sentinels used here).
    """
    payload = text.encode("latin-1")
    stream = b"BT /F1 24 Tf 72 720 Td (" + payload + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def _build_docx_bytes(paragraphs: list[str]) -> bytes:
    """Author a real ``.docx`` from ``paragraphs`` using ``python-docx``.

    Imported lazily so collection does not pay the cost when the module is
    skipped (no infra). Structurally identical to what the extractor reads in
    production.
    """
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _build_docx_zip_bomb_bytes(*, entries: int) -> bytes:
    """Return a real DOCX repacked with ``entries`` extra archive members.

    Starts from a genuine ``python-docx`` document (so the Mime_Validator still
    classifies the bytes as ``docx``), then rewrites the archive adding
    ``entries`` tiny bloat members. With ``entries`` over
    ``MATCHLAYER_RESUME_MAX_ARCHIVE_ENTRIES`` (default 256) the service's
    ``guard_docx_archive`` entry-count check trips and returns 422
    ``malformed_upload`` before any object is stored (Requirement 2.4).
    """
    base = _build_docx_bytes(["zip bomb base document"])
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(base)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as repacked,
    ):
        for info in source.infolist():
            repacked.writestr(info, source.read(info.filename))
        for index in range(entries):
            repacked.writestr(f"bloat/entry_{index}.txt", b"x")
    return out.getvalue()


def _multipart_file(
    *, filename: str, data: bytes, content_type: str = "application/octet-stream"
) -> dict[str, Any]:
    """Build the httpx ``files=`` mapping for the ``file`` multipart part.

    The declared ``content_type`` is deliberately a generic/wrong value in most
    tests to prove the server relies on magic-byte sniffing, not the client
    header (Requirement 2.3). The part name is ``file`` per Requirement 2.1.
    """
    return {"file": (filename, data, content_type)}


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def recording_storage(monkeypatch: pytest.MonkeyPatch) -> _RecordingStorage:
    """Override ``get_resume_storage`` with an in-memory recording fake.

    The ``Resume_Service`` calls ``get_resume_storage()`` (imported into
    ``services.resumes``) lazily when no storage instance is injected, and the
    router constructs the service with no storage argument. Patching the name
    the service module bound makes every upload in the test use the fake -- no
    live MinIO/S3, and the production ``@lru_cache``d client is never built or
    mutated. Returns the fake so tests can inspect ``put_calls`` / ``objects``.
    """
    storage = _RecordingStorage()
    monkeypatch.setattr(
        "matchlayer_api.services.resumes.get_resume_storage",
        lambda: storage,
    )
    return storage


ResumeRowFactory = Callable[..., Awaitable[Resume]]


@pytest.fixture
def factory_resume_row(db_session: AsyncSession) -> ResumeRowFactory:
    """Insert a ``resumes`` row directly on the per-test session.

    Used by the get/delete/owner-scoping tests that need a pre-existing resume
    without driving the full upload path. The row is flushed (not committed);
    because ``client_with_session`` shares this exact session with the API, the
    inserted resume is visible to the resume endpoints within the same
    transaction. The autouse ``_truncate_auth_tables`` fixture's
    ``TRUNCATE ... CASCADE`` reaches ``resumes`` (it FK-references ``users``),
    so no cross-test leakage occurs.
    """

    async def _build(
        *,
        user_id: object,
        original_filename: str = "resume.pdf",
        content_type: str = "application/pdf",
        extraction_status: str = "succeeded",
        extracted_text: str | None = "extracted resume text",
        deleted_at: datetime | None = None,
    ) -> Resume:
        succeeded = extraction_status == "succeeded"
        text = extracted_text if succeeded else None
        resume = Resume(
            id=uuid7(),
            user_id=user_id,
            original_filename=original_filename,
            storage_key=f"{uuid7()}.pdf",
            content_type=content_type,
            byte_size=1024,
            extracted_text=text,
            extraction_status=extraction_status,
            extraction_char_count=len(text) if text is not None else None,
            deleted_at=deleted_at,
        )
        db_session.add(resume)
        await db_session.flush()
        return resume

    return _build


def _auth(token: str) -> dict[str, str]:
    """Build the ``Authorization: Bearer`` header for *token*."""
    return {"Authorization": f"Bearer {token}"}


async def _make_user_and_token(factory_user: UserFactory, prefix: str) -> tuple[User, str]:
    """Create a user row and a matching access token."""
    user = await factory_user(email=unique_email(prefix))
    token = issue_access_token(sub=str(user.id))
    return user, token


# ---------------------------------------------------------------------------
# Authentication gating (Requirements 1.1, 1.2, 1.3) -- every route 401s
# without a valid access token.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/resumes"),
        ("GET", "/api/v1/resumes"),
        ("GET", "/api/v1/resumes/00000000-0000-7000-8000-000000000000"),
        ("DELETE", "/api/v1/resumes/00000000-0000-7000-8000-000000000000"),
    ],
)
async def test_routes_require_auth_missing_token_401(
    client_with_session: AsyncClient, method: str, path: str
) -> None:
    """Every resume route without an Authorization header → 401 (1.1, 1.2)."""
    res = await client_with_session.request(method, path)
    assert res.status_code == 401
    assert res.json()["type"] == "unauthenticated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/resumes"),
        ("GET", "/api/v1/resumes"),
        ("GET", "/api/v1/resumes/00000000-0000-7000-8000-000000000000"),
        ("DELETE", "/api/v1/resumes/00000000-0000-7000-8000-000000000000"),
    ],
)
async def test_routes_reject_invalid_token_401(
    client_with_session: AsyncClient, method: str, path: str
) -> None:
    """Every resume route with a malformed Bearer token → 401 (1.2)."""
    res = await client_with_session.request(
        method, path, headers={"Authorization": "Bearer not.a.valid.jwt"}
    )
    assert res.status_code == 401
    assert res.json()["type"] == "unauthenticated"


@pytest.mark.asyncio
async def test_list_resumes_wrong_scheme_401(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """A non-Bearer auth scheme is rejected even with a real token value (1.2)."""
    user = await factory_user(email=unique_email("scheme"))
    token = issue_access_token(sub=str(user.id))
    res = await client_with_session.get(
        "/api/v1/resumes", headers={"Authorization": f"Basic {token}"}
    )
    assert res.status_code == 401
    assert res.json()["type"] == "unauthenticated"


@pytest.mark.asyncio
async def test_list_resumes_soft_deleted_user_401(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """A valid token whose user has deleted_at set → 401 unauthenticated (1.3)."""
    user = await factory_user(email=unique_email("gonemr"), deleted_at=datetime.now(UTC))
    token = issue_access_token(sub=str(user.id))
    res = await client_with_session.get("/api/v1/resumes", headers=_auth(token))
    assert res.status_code == 401
    assert res.json()["type"] == "unauthenticated"


# ---------------------------------------------------------------------------
# Ownership / not-found disclosure (Requirements 1.5, 1.6, 4.4).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_resume_missing_id_404_not_found(
    client_with_session: AsyncClient, factory_user: UserFactory
) -> None:
    """GET a resume id that resolves to no row → 404 not_found (1.6, 4.4)."""
    _user, token = await _make_user_and_token(factory_user, "getmissing")
    res = await client_with_session.get(f"/api/v1/resumes/{uuid7()}", headers=_auth(token))
    assert res.status_code == 404
    assert res.json()["type"] == "not_found"


@pytest.mark.asyncio
async def test_get_resume_other_owner_404_not_found(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume_row: ResumeRowFactory,
) -> None:
    """GET another user's resume → 404 not_found, no existence disclosure (1.5, 4.4)."""
    owner = await factory_user(email=unique_email("resowner"))
    resume = await factory_resume_row(user_id=owner.id)
    _intruder, intruder_token = await _make_user_and_token(factory_user, "resintruder")

    res = await client_with_session.get(
        f"/api/v1/resumes/{resume.id}", headers=_auth(intruder_token)
    )
    assert res.status_code == 404
    assert res.json()["type"] == "not_found"


@pytest.mark.asyncio
async def test_delete_resume_other_owner_404_not_found(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume_row: ResumeRowFactory,
    db_session: AsyncSession,
) -> None:
    """DELETE another user's resume → 404, and the row is not soft-deleted (1.5)."""
    owner = await factory_user(email=unique_email("delowner"))
    resume = await factory_resume_row(user_id=owner.id)
    _intruder, intruder_token = await _make_user_and_token(factory_user, "delintruder")

    res = await client_with_session.delete(
        f"/api/v1/resumes/{resume.id}", headers=_auth(intruder_token)
    )
    assert res.status_code == 404
    assert res.json()["type"] == "not_found"

    # The other owner's row remains active (not soft-deleted by the intruder).
    await db_session.refresh(resume)
    assert resume.deleted_at is None
    # No resume_deleted audit row was emitted for either party.
    assert await _count_audit(db_session, "resume_deleted", owner.id) == 0


# ---------------------------------------------------------------------------
# POST /api/v1/resumes -- happy path (Requirements 2.1, 2.7, 2.9) + the
# no-public-read storage guarantee (Requirement 2.10).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_pdf_happy_201_safe_field_set_and_audit(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """A valid PDF upload → 201 with exactly the safe field set + audit (2.1, 2.7, 2.9).

    Drives the real upload orchestration (MIME sniff → store → INSERT → extract
    → audit) with a genuine PDF whose embedded text is extractable, the storage
    layer faked in-memory. Asserts the 201 body carries exactly the seven safe
    fields and never the parsed text or storage key, and that a
    ``resume_uploaded`` audit row referencing the internal id only is emitted.
    """
    user, token = await _make_user_and_token(factory_user, "uppdf")
    pdf = _build_pdf_bytes("JANE DOE RESUME EXTRACTABLE TEXT")

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files=_multipart_file(filename="my-resume.pdf", data=pdf),
    )

    assert res.status_code == 201, res.text
    body = res.json()
    # Requirement 2.9: exactly the safe field set, no extras.
    assert set(body.keys()) == _RESUME_RESPONSE_FIELDS
    for forbidden in _FORBIDDEN_RESUME_FIELDS:
        assert forbidden not in body
    assert body["original_filename"] == "my-resume.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["byte_size"] == len(pdf)
    # Genuine extractable PDF → extraction succeeded.
    assert body["extraction_status"] == "succeeded"

    # Exactly one object was written to storage under a filename-free key.
    assert len(recording_storage.put_calls) == 1
    stored_key = recording_storage.put_calls[0]["key"]
    assert "my-resume" not in stored_key  # Requirement 2.5 (filename-free key).
    assert stored_key.endswith(".pdf")

    # Requirement 2.7: a resume_uploaded audit row with the internal id only.
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "resume_uploaded",
            AuditEvent.user_id == user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.payload == {"resume_id": body["id"]}


@pytest.mark.asyncio
async def test_upload_docx_happy_201(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
) -> None:
    """A valid DOCX upload → 201 with the docx content type (2.1, 2.3, 2.9)."""
    _user, token = await _make_user_and_token(factory_user, "updocx")
    docx_bytes = _build_docx_bytes(["Jane Doe", "Python engineer with FastAPI experience"])

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        # Deliberately wrong client content-type to prove magic-byte sniffing.
        files=_multipart_file(
            filename="resume.docx", data=docx_bytes, content_type="application/pdf"
        ),
    )

    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body.keys()) == _RESUME_RESPONSE_FIELDS
    assert (
        body["content_type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert body["extraction_status"] == "succeeded"


@pytest.mark.asyncio
async def test_upload_storage_put_issues_no_public_read_acl(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
) -> None:
    """The stored object is written with no public-read access (Requirement 2.10).

    Asserted at the storage boundary (the design's chosen approach for repos
    without a guaranteed anonymous-read MinIO): the recorded ``put`` call -- the
    exact call the production ``Resume_Storage.put`` would issue -- carries no
    ``ACL`` argument of any kind, so the object inherits the bucket's
    default-private visibility and is reachable only through the authenticated
    API. A regression that began passing a public-read ACL would add an ``ACL``
    key here and fail this test.
    """
    _user, token = await _make_user_and_token(factory_user, "acl")
    pdf = _build_pdf_bytes("ACL TEST RESUME")

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files=_multipart_file(filename="resume.pdf", data=pdf),
    )
    assert res.status_code == 201, res.text

    assert len(recording_storage.put_calls) == 1
    call = recording_storage.put_calls[0]
    # No ACL key at all -- not even "private" (matches Resume_Storage.put, whose
    # signature has no ACL parameter; see core/storage.py module docstring).
    assert "ACL" not in call
    assert set(call.keys()) == {"key", "data", "content_type"}
    # No public-read grant smuggled through any value either.
    assert "public-read" not in repr(call)


# ---------------------------------------------------------------------------
# POST /api/v1/resumes -- 413 payload_too_large (Requirement 2.2).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_over_max_bytes_413_payload_too_large(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """A file part over MATCHLAYER_RESUME_MAX_BYTES → 413, nothing persisted (2.2).

    The default ceiling is 5 MiB; a 5 MiB + 1 KiB payload trips the router's
    pre-service declared-length guard. Starlette spools file parts (not subject
    to its 1 MB in-memory field cap), so ``UploadFile.size`` carries the true
    length to the guard. No object is written and no Resume row is inserted.
    """
    user, token = await _make_user_and_token(factory_user, "toobig")
    oversize = b"%PDF-1.4\n" + b"0" * (get_settings().resume_max_bytes + 1024)

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files=_multipart_file(filename="huge.pdf", data=oversize),
    )

    assert res.status_code == 413
    assert res.json()["type"] == "payload_too_large"
    # Requirement 2.2: no object written, no row inserted, no audit row.
    assert recording_storage.put_calls == []
    assert await _count_audit(db_session, "resume_uploaded", user.id) == 0


# ---------------------------------------------------------------------------
# POST /api/v1/resumes -- 415 unsupported_media_type (Requirement 2.3).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_non_pdf_docx_bytes_415_unsupported_media_type(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """Plain-text bytes (with a .pdf name + pdf content-type) → 415 (2.3).

    The Mime_Validator sniffs magic bytes and ignores both the client
    ``Content-Type`` header and the ``.pdf`` extension, so a text payload masked
    as a PDF is rejected with 415 and nothing is stored.
    """
    user, token = await _make_user_and_token(factory_user, "badtype")
    not_a_pdf = b"This is plain text pretending to be a PDF resume, but it is not."

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files=_multipart_file(
            filename="resume.pdf", data=not_a_pdf, content_type="application/pdf"
        ),
    )

    assert res.status_code == 415
    assert res.json()["type"] == "unsupported_media_type"
    assert recording_storage.put_calls == []
    assert await _count_audit(db_session, "resume_uploaded", user.id) == 0


# ---------------------------------------------------------------------------
# POST /api/v1/resumes -- 422 malformed_upload (DOCX zip bomb, Requirement 2.4).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_docx_zip_bomb_422_malformed_upload(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """A DOCX with too many archive entries → 422 malformed_upload, no store (2.4).

    The bomb has more than MATCHLAYER_RESUME_MAX_ARCHIVE_ENTRIES (default 256)
    members, so the service's stdlib-``zipfile`` entry-count guard refuses it
    before any object is written -- the decompression-bomb defense from the
    security.md file-upload threat model.
    """
    user, token = await _make_user_and_token(factory_user, "zipbomb")
    bomb = _build_docx_zip_bomb_bytes(entries=300)

    res = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files=_multipart_file(filename="resume.docx", data=bomb),
    )

    assert res.status_code == 422
    assert res.json()["type"] == "malformed_upload"
    assert recording_storage.put_calls == []
    assert await _count_audit(db_session, "resume_uploaded", user.id) == 0


# ---------------------------------------------------------------------------
# List / get / delete lifecycle (Requirements 4.4, 4.5, 4.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_owned_resume_returns_safe_field_set(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume_row: ResumeRowFactory,
) -> None:
    """GET an owned resume → 200 with exactly the safe field set (4.4)."""
    user, token = await _make_user_and_token(factory_user, "getowned")
    resume = await factory_resume_row(user_id=user.id, original_filename="cv.pdf")

    res = await client_with_session.get(f"/api/v1/resumes/{resume.id}", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == _RESUME_RESPONSE_FIELDS
    for forbidden in _FORBIDDEN_RESUME_FIELDS:
        assert forbidden not in body
    assert body["id"] == str(resume.id)
    assert body["original_filename"] == "cv.pdf"


@pytest.mark.asyncio
async def test_list_resumes_returns_owned_rows_only(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    factory_resume_row: ResumeRowFactory,
) -> None:
    """GET list returns only the caller's non-deleted resumes, safe shape (4.4)."""
    user, token = await _make_user_and_token(factory_user, "listmine")
    other = await factory_user(email=unique_email("listother"))
    await factory_resume_row(user_id=user.id, original_filename="mine-1.pdf")
    await factory_resume_row(user_id=user.id, original_filename="mine-2.pdf")
    await factory_resume_row(user_id=other.id, original_filename="theirs.pdf")

    res = await client_with_session.get("/api/v1/resumes", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"items", "next_cursor"}
    assert len(body["items"]) == 2
    filenames = {item["original_filename"] for item in body["items"]}
    assert filenames == {"mine-1.pdf", "mine-2.pdf"}
    for item in body["items"]:
        assert set(item.keys()) == _RESUME_RESPONSE_FIELDS
        for forbidden in _FORBIDDEN_RESUME_FIELDS:
            assert forbidden not in item


@pytest.mark.asyncio
async def test_delete_resume_idempotent_204_single_audit_and_retained_bytes(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """Delete is idempotent (4.5, 4.6) and retains stored bytes + text (4.7).

    Uploads a real PDF (so a storage object and ``extracted_text`` exist), then:
    first DELETE → 204 + one ``resume_deleted`` audit row + the resume is gone
    from subsequent reads; second DELETE → 204 no-op with no additional audit
    row. Afterwards the stored object and the ``extracted_text`` column are
    still present, as Phase 1 retains bytes after a soft delete (Requirement
    4.7; hard deletion is deferred to Phase 7).
    """
    user, token = await _make_user_and_token(factory_user, "del")
    pdf = _build_pdf_bytes("DELETE LIFECYCLE RESUME TEXT")
    create = await client_with_session.post(
        "/api/v1/resumes",
        headers=_auth(token),
        files=_multipart_file(filename="resume.pdf", data=pdf),
    )
    assert create.status_code == 201, create.text
    resume_id = create.json()["id"]
    stored_key = recording_storage.put_calls[0]["key"]

    # First delete → 204 + audit row.
    res1 = await client_with_session.delete(f"/api/v1/resumes/{resume_id}", headers=_auth(token))
    assert res1.status_code == 204

    # The resume is gone from subsequent reads (Requirement 1.6).
    res_get = await client_with_session.get(f"/api/v1/resumes/{resume_id}", headers=_auth(token))
    assert res_get.status_code == 404

    # Second delete → still 204, idempotent (Requirement 4.6).
    res2 = await client_with_session.delete(f"/api/v1/resumes/{resume_id}", headers=_auth(token))
    assert res2.status_code == 204

    # Exactly one resume_deleted audit row (Requirement 4.5/4.6).
    assert await _count_audit(db_session, "resume_deleted", user.id) == 1

    # Requirement 4.7: stored object + extracted_text are retained after soft delete.
    assert stored_key in recording_storage.objects
    row = await db_session.get(Resume, uuid.UUID(resume_id))
    assert row is not None
    assert row.deleted_at is not None
    assert row.extracted_text is not None


# ---------------------------------------------------------------------------
# "Never logged" PII negatives (Requirements 2.6, 3.6, 3.7).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_never_logs_filename_bytes_or_extracted_text(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """No log event carries the filename, file bytes, or extracted text (2.6, 3.6, 3.7).

    Uses the ``structlog.testing.capture_logs`` pattern: the upload runs inside
    a capture context with three distinct, recognizable sentinels --
    * a filename sentinel (Restricted per Requirement 2.6),
    * a byte/text sentinel embedded in the PDF content stream (so it appears in
      both the raw file bytes and the extracted text -- Requirements 3.6/3.7).
    No captured structured log event may contain any sentinel. Defense in depth:
    the byte/text sentinel must also never appear in the 201 response body, and
    the ``resume_uploaded`` audit payload must carry only the internal id.
    """
    user, token = await _make_user_and_token(factory_user, "nolog")
    filename_sentinel = "ZZFILENAMESENTINEL1234567890ZZ"
    text_sentinel = "ZZRESUMETEXTSENTINEL0987654321ZZ"
    pdf = _build_pdf_bytes(text_sentinel)
    assert text_sentinel.encode("latin-1") in pdf  # sentinel really is in the bytes

    with structlog.testing.capture_logs() as captured:
        res = await client_with_session.post(
            "/api/v1/resumes",
            headers=_auth(token),
            files=_multipart_file(filename=f"{filename_sentinel}.pdf", data=pdf),
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["extraction_status"] == "succeeded"

    # No structured log event may contain any sentinel (Requirements 2.6, 3.6, 3.7).
    haystack = repr(captured)
    assert filename_sentinel not in haystack, "original_filename leaked into a log event"
    assert text_sentinel not in haystack, "file bytes / extracted text leaked into a log event"

    # Defense in depth: the parsed-text sentinel never appears in the response
    # body (the safe field set excludes extracted_text), and the audit payload
    # carries only the internal id (Requirements 2.6, 2.7).
    assert text_sentinel not in res.text
    result = await db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == "resume_uploaded",
            AuditEvent.user_id == user.id,
        )
    )
    audit = result.scalar_one()
    assert audit.payload == {"resume_id": body["id"]}
    assert filename_sentinel not in str(audit.payload)
    assert text_sentinel not in str(audit.payload)


@pytest.mark.asyncio
async def test_failed_extraction_logs_category_and_id_only(
    client_with_session: AsyncClient,
    factory_user: UserFactory,
    recording_storage: _RecordingStorage,
    db_session: AsyncSession,
) -> None:
    """A failed extraction logs the category + resume id, never bytes/text (3.7).

    A scanned/image-only PDF (valid PDF, no extractable text) yields a
    whitespace-only extraction → ``extraction_status='failed'``. The
    Resume_Service emits a structured ``resume_extraction_failed`` event naming
    the ``failure_category`` and the resume id only. The upload still returns
    201 (fail-soft, Requirement 3.5), and the byte sentinel embedded in the file
    must appear in no captured log event (Requirement 3.7).
    """
    _user, token = await _make_user_and_token(factory_user, "failext")
    byte_sentinel = "ZZIMAGEONLYPDFSENTINELZZ"
    # A structurally valid PDF whose only text-stream content is a comment, so
    # pypdf extracts no characters → empty_text failure category. The sentinel
    # lives in a PDF comment (never extracted as text) so it represents raw
    # file bytes that must not be logged.
    pdf = (
        b"%PDF-1.4\n%" + byte_sentinel.encode("latin-1") + b"\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )

    with structlog.testing.capture_logs() as captured:
        res = await client_with_session.post(
            "/api/v1/resumes",
            headers=_auth(token),
            files=_multipart_file(filename="scan.pdf", data=pdf),
        )

    # Fail-soft: upload still succeeds (Requirement 3.5).
    assert res.status_code == 201, res.text
    assert res.json()["extraction_status"] == "failed"

    # The structured failure event names the category + id only, never the
    # raw bytes (Requirement 3.7).
    events = [e for e in captured if e.get("event") == "resume_extraction_failed"]
    assert len(events) == 1
    failure_event = events[0]
    assert failure_event["resume_id"] == res.json()["id"]
    assert failure_event["failure_category"] in {
        "extraction_timeout",
        "corrupt_document",
        "empty_text",
    }
    # The raw-byte sentinel reached no captured log event.
    assert byte_sentinel not in repr(captured)


# ---------------------------------------------------------------------------
# Local helpers.
# ---------------------------------------------------------------------------


async def _count_audit(session: AsyncSession, event_type: str, user_id: object) -> int:
    """Count audit rows of ``event_type`` for *user_id* in the shared session."""
    result = await session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == event_type,
            AuditEvent.user_id == user_id,
        )
    )
    return len(result.scalars().all())
