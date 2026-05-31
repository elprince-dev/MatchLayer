"""Object-storage abstraction for resume file bytes (``Resume_Storage``).

This is the ONLY module in the API that reads or writes resume file bytes,
and the ONLY module that imports ``boto3``. The import-boundary intent
mirrors the ``redis``-only-in-``rate_limit.py`` and ``jwt``-only-in-
``security/jwt.py`` rules already enforced by
``tests/unit/test_import_boundaries.py``.

The store is MinIO during local development and real AWS S3 in production;
both speak the S3 API, so a single ``boto3`` client serves both. The client
is built once from the existing :class:`~matchlayer_api.config.Settings` S3
fields (``s3_endpoint_url``, ``s3_region``, ``s3_access_key_id``,
``s3_secret_access_key``, ``s3_bucket``) -- this spec introduces **no** second
credential set.

Two security guarantees from ``security.md`` and the phase-1-matching
requirements are realized here:

* **No public-read access (Requirement 2.10).** Objects are written with no
  ACL argument at all, so they inherit the bucket's default-private
  visibility. We deliberately do **not** pass ``ACL="private"``: buckets
  configured with Object Ownership "Bucket owner enforced" (the Phase 6 AWS
  baseline -- ``security.md`` mandates account-level Block Public Access)
  disable ACLs entirely and reject *any* ``ACL`` parameter, even
  ``"private"``, with ``AccessControlListNotSupported``. Relying on the
  default keeps the write working on both ACL-enabled MinIO and
  ACL-disabled S3 while never granting public read.
* **Filename-free object keys (Requirement 2.5).** :func:`build_object_key`
  is the only sanctioned key source. A key is ``<uuidv7>.<ext>`` where
  ``<ext>`` is ``pdf`` or ``docx`` -- it never incorporates any part of the
  client-supplied filename, which is retained only in the display-only
  ``resumes.original_filename`` column.

``boto3`` is synchronous and its calls perform network I/O, so every call is
dispatched to a worker thread via :func:`fastapi.concurrency.run_in_threadpool`
to keep the asyncio event loop free (Design "Resume_Storage").

Design reference: Components and Interfaces -- Resume_Storage.
Requirements covered: 2.5, 2.10.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal, cast

import boto3  # type: ignore[import-untyped]  # boto3 ships no py.typed / stubs
from fastapi.concurrency import run_in_threadpool
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings, get_settings

__all__ = ["Resume_Storage", "build_object_key", "get_resume_storage"]

# The two file kinds the upload surface accepts. The literal extension is
# what lands in the object key; it is chosen by the Mime_Validator's
# magic-byte verdict, never by the client filename or Content-Type header.
ResumeKind = Literal["pdf", "docx"]


def build_object_key(extension: ResumeKind) -> str:
    """Return a fresh, filename-free object key ``<uuidv7>.<extension>``.

    This is the only sanctioned way to mint a resume storage key. The key's
    stem is a freshly generated UUIDv7 (time-ordered, matching the PK scheme
    in ``db/models.py``), so the key is globally unique and reveals nothing
    about the uploaded file's original name -- satisfying Requirement 2.5's
    rule that the client filename never reaches the object key or any
    filesystem path.

    Args:
        extension: ``"pdf"`` or ``"docx"`` -- the validated file kind, as
            decided by the Mime_Validator from the file's leading bytes.

    Returns:
        A key of the form ``<uuidv7>.<ext>`` (for example
        ``"018f4e1c-...-89ab.pdf"``).
    """
    return f"{uuid7()}.{extension}"


# ``Resume_Storage`` keeps the underscored design/glossary component name; the
# project standardizes the N801 waiver on these design-named classes (see
# ``Auth_Service``, ``Audit_Service``, ``Skill_Lexicon``).
class Resume_Storage:  # noqa: N801 -- design uses the underscored component name.
    """Thin async wrapper over an S3-compatible client for resume bytes.

    The class does not own its client's lifecycle: the client is injected so
    tests can supply a fake or a ``moto``-backed stub, and the cached
    :func:`get_resume_storage` factory builds the production client. This is
    the same injection shape :class:`~matchlayer_api.core.rate_limit.RateLimiter`
    uses for its Redis client.

    All blocking ``boto3`` calls run inside
    :func:`~fastapi.concurrency.run_in_threadpool`, so a slow object store
    never stalls the event loop.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        """Store the injected S3 client and target bucket.

        Args:
            client: A ``boto3`` S3 client (or a compatible test double).
                Typed ``Any`` because ``boto3`` ships no type information;
                every method we call (``put_object`` / ``get_object``) is a
                runtime attribute on that client.
            bucket: The destination bucket name (``Settings.s3_bucket``).
        """
        # ``Any`` justified: boto3 has no py.typed marker or bundled stubs,
        # so the client and its methods are untyped at the boundary. We keep
        # the untyped surface contained to this one wrapper.
        self._client: Any = client
        self._bucket = bucket

    async def put(self, *, key: str, data: bytes, content_type: str) -> None:
        """Write ``data`` to the bucket under ``key`` with no public-read.

        No ``ACL`` argument is passed, so the object inherits the bucket's
        default-private visibility (Requirement 2.10). See the module
        docstring for why an explicit ``ACL="private"`` is intentionally
        avoided.

        Args:
            key: The object key. Callers MUST obtain this from
                :func:`build_object_key` so the filename-free guarantee
                (Requirement 2.5) holds.
            data: The raw file bytes to store.
            content_type: The validated MIME type to stamp on the object
                (``application/pdf`` or the DOCX OOXML type).
        """

        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        await run_in_threadpool(_put)

    async def get(self, *, key: str) -> bytes:
        """Read and return the object bytes stored under ``key``.

        The ``get_object`` request and the streaming-body read both touch the
        network, so the whole read is performed in a single worker-thread hop
        rather than two.

        Args:
            key: The object key previously produced by :func:`build_object_key`.

        Returns:
            The object's raw bytes.
        """

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            # ``Body`` is a botocore ``StreamingBody``; ``.read()`` returns
            # ``Any`` through the untyped client, so cast to the concrete
            # return type to keep ``warn_return_any`` satisfied.
            return cast(bytes, response["Body"].read())

        return await run_in_threadpool(_get)


def _build_s3_client(settings: Settings) -> Any:
    """Construct a ``boto3`` S3 client from the S3 ``Settings`` fields.

    ``s3_endpoint_url`` is ``None`` in production so ``boto3`` targets real
    AWS S3; MinIO supplies a non-AWS URL locally. The secret is read via
    :meth:`SecretStr.get_secret_value` only here, at client-construction
    time, so the plaintext secret never lingers on the storage instance or
    in any ``repr``.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )


@lru_cache(maxsize=1)
def get_resume_storage() -> Resume_Storage:
    """Return the process-wide :class:`Resume_Storage`.

    Cached like :func:`~matchlayer_api.config.get_settings`: the underlying
    ``boto3`` S3 client is built once and reused across requests and worker
    threads (low-level boto3 clients are safe to share across threads for
    issuing calls), avoiding per-request client construction. Tests override
    the storage via FastAPI's ``dependency_overrides`` mapping (or by
    constructing :class:`Resume_Storage` directly with a fake client) rather
    than mutating this cache.
    """
    settings = get_settings()
    return Resume_Storage(_build_s3_client(settings), settings.s3_bucket)
