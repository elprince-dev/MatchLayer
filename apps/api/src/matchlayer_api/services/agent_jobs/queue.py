"""JobQueue — async SQS client for the Agent_Job queue (``Job_Queue``).

This is the ONLY module in the API that imports ``aioboto3`` (the sync
``boto3`` stays confined to ``core/storage.py`` for S3 — see
``apps/api/pyproject.toml``). It is also the only module that reads the
SQS ``Settings`` fields: queue URL, region, endpoint override, and the
AWS credential pair come exclusively from
:class:`~matchlayer_api.config.Settings` (Requirement 11.1; design §5).
Note on credentials: the repo maintains a single Settings-sourced AWS
credential set (``s3_access_key_id`` / ``s3_secret_access_key`` — see
``core/storage.py``: "this spec introduces no second credential set"),
so the SQS client signs with the same pair. LocalStack accepts any
value locally; production points both S3 and SQS at the same account.

Four operations, mirroring the design §5 interface:

* :meth:`JobQueue.enqueue` — sends a :class:`JobMessage` whose body
  carries **identifiers only** (job id, match id, user id) — never
  Resume text or Job_Description text (Requirement 11.1, ``security.md``
  Restricted classification). The current trace context is attached as
  SQS ``MessageAttributes`` via
  :func:`~matchlayer_api.core.tracing.inject_trace_context`
  (Requirement 13.4). Failures propagate: the analyze endpoint maps an
  enqueue failure to 503 + job ``failed`` (Requirement 11.6).
* :meth:`JobQueue.receive` — long-poll consume (worker only). Returns
  raw :class:`ReceivedMessage` values; body parsing/validation is the
  worker's job so a malformed body can be handled as a poison message
  (Requirement 11.7) rather than raising here.
* :meth:`JobQueue.delete` — acknowledges a message by receipt handle.
  The worker calls this only after the terminal Job_Status transition
  is committed (Requirement 11.4).
* :meth:`JobQueue.healthcheck` — ``GetQueueAttributes`` under a short
  ``asyncio.wait_for`` timeout, returning ``True``/``False`` and never
  raising. Backs the ``/healthz`` ``agents`` field (Requirement 16.1);
  the structured warning on failure carries the exception type only —
  never the queue URL, endpoint address, or credentials
  (Requirement 16.6).

Design deviation note: design §5 sketches
``enqueue(message, trace_headers)``; the trace headers are instead
captured internally via :func:`inject_trace_context` at send time, so
every enqueue carries the caller's current trace context by
construction and no call site can forget it (Requirement 13.4).

Design reference: phase-4-agentic design §5.
Requirements covered: 11.1, 11.3, 13.4, 16.1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import aioboto3  # type: ignore[import-untyped]  # aioboto3 ships no py.typed / stubs
import structlog
from pydantic import BaseModel, ConfigDict

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.tracing import inject_trace_context

__all__ = [
    "JobMessage",
    "JobQueue",
    "ReceivedMessage",
    "get_job_queue",
]

_log = structlog.get_logger(__name__)

# Long-poll wait for the worker's receive loop, in seconds. SQS caps
# WaitTimeSeconds at 20; 10 keeps worker shutdown reasonably responsive
# while still eliminating empty-receive busy-polling.
_DEFAULT_WAIT_TIME_SECONDS = 10

# Messages fetched per receive. The worker executes one Agent_Job at a
# time (one graph run per message), so batching would only grow the
# redelivery window for messages sitting unprocessed in memory.
_DEFAULT_MAX_MESSAGES = 1

# Wall-clock bound on the whole healthcheck round trip (client
# construction + GetQueueAttributes). Short by design (design §7:
# "short timeout") so a dead LocalStack/SQS cannot stall /healthz.
_DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS = 2.0

# The factory type each JobQueue method calls to obtain an SQS client:
# every call yields a fresh async context manager whose ``__aenter__``
# produces the client (aioboto3's native usage shape). ``Any`` justified:
# aioboto3 ships no type information, so the client object is untyped at
# this boundary — the same containment approach as ``core/storage.py``.
SqsClientFactory = Callable[[], AbstractAsyncContextManager[Any]]


class JobMessage(BaseModel):
    """The Job_Queue message body — identifiers only, never content.

    Carries exactly the three identifiers the worker needs to load job
    context from the database (Requirement 11.1): the Agent_Job id, the
    Match_Result id, and the owning user id, each a UUIDv7 exposed as a
    string per ``conventions.md``. Resume text and Job_Description text
    are Restricted (``security.md``) and structurally cannot appear —
    ``extra="forbid"`` rejects any additional field at construction and
    at worker-side parse time alike.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    match_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class ReceivedMessage:
    """One raw message from :meth:`JobQueue.receive`.

    The body is deliberately the raw string, not a parsed
    :class:`JobMessage`: parsing/validation belongs to the worker so a
    malformed body becomes a logged-and-deleted poison message
    (Requirement 11.7) instead of an exception inside the receive loop.

    ``message_attributes`` is the raw SQS attribute mapping in the shape
    :func:`~matchlayer_api.core.tracing.extract_trace_context` accepts
    (``{name: {"DataType": ..., "StringValue": ...}}``); empty when the
    message carries none.
    """

    body: str
    receipt_handle: str
    message_attributes: dict[str, object] = field(default_factory=dict)


class JobQueue:
    """Thin async wrapper over aioboto3 SQS for the Agent_Job queue.

    The SQS client factory is injected so tests supply fakes without any
    network or LocalStack dependency — the same injection shape as
    :class:`~matchlayer_api.core.storage.Resume_Storage` (client injected,
    cached production factory below). All connection parameters flow in
    from :class:`~matchlayer_api.config.Settings` via
    :func:`get_job_queue`; this class itself never touches configuration
    or the environment.
    """

    def __init__(
        self,
        client_factory: SqsClientFactory,
        *,
        queue_url: str,
        wait_time_seconds: int = _DEFAULT_WAIT_TIME_SECONDS,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        healthcheck_timeout_seconds: float = _DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS,
    ) -> None:
        """Store the injected client factory and queue parameters.

        Args:
            client_factory: Zero-argument callable returning an async
                context manager that yields an SQS client (aioboto3's
                ``session.client("sqs", ...)`` shape, or a test double).
            queue_url: The full queue URL (``Settings.sqs_queue_url``).
            wait_time_seconds: Long-poll wait for :meth:`receive` (0-20).
            max_messages: Max messages per :meth:`receive` call (1-10).
            healthcheck_timeout_seconds: Wall-clock bound on
                :meth:`healthcheck`'s full round trip.
        """
        self._client_factory = client_factory
        self._queue_url = queue_url
        self._wait_time_seconds = wait_time_seconds
        self._max_messages = max_messages
        self._healthcheck_timeout_seconds = healthcheck_timeout_seconds

    async def enqueue(self, message: JobMessage) -> None:
        """Send ``message`` with the current trace context attached.

        The body is the JSON-serialized :class:`JobMessage` — identifiers
        only (Requirement 11.1). Trace context is captured here, at send
        time, via :func:`inject_trace_context` and attached as SQS
        ``MessageAttributes`` (W3C ``traceparent``/``tracestate`` headers
        — identifiers, never Restricted content; Requirement 13.4). With
        no active span the helper returns an empty mapping and the
        attributes are omitted entirely, which the worker treats as
        "start a new trace" (Requirement 13.6).

        Failures propagate to the caller: the analyze endpoint converts
        an unreachable queue into a 503 RFC 7807 response and a ``failed``
        job so no orphaned ``queued`` row remains (Requirement 11.6).
        """
        trace_attributes = inject_trace_context()
        send_kwargs: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MessageBody": message.model_dump_json(),
        }
        if trace_attributes:
            send_kwargs["MessageAttributes"] = trace_attributes
        async with self._client_factory() as client:
            await client.send_message(**send_kwargs)

    async def receive(self) -> list[ReceivedMessage]:
        """Long-poll the queue; return raw messages (worker only).

        Requests ``MessageAttributeNames=["All"]`` so the trace-context
        attributes injected at enqueue travel through to
        :func:`~matchlayer_api.core.tracing.extract_trace_context`
        (Requirement 13.4). An empty poll returns an empty list.

        Transport failures propagate — the worker's consume loop owns
        retry/backoff policy, not this client.
        """
        async with self._client_factory() as client:
            response = await client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=self._max_messages,
                WaitTimeSeconds=self._wait_time_seconds,
                MessageAttributeNames=["All"],
            )
        raw_messages = response.get("Messages", [])
        received: list[ReceivedMessage] = []
        for raw in raw_messages:
            received.append(
                ReceivedMessage(
                    body=raw.get("Body", ""),
                    receipt_handle=raw.get("ReceiptHandle", ""),
                    message_attributes=dict(raw.get("MessageAttributes") or {}),
                )
            )
        return received

    async def delete(self, receipt_handle: str) -> None:
        """Delete (acknowledge) a message by receipt handle.

        The worker calls this only after the job's terminal Job_Status
        transition is committed (Requirement 11.4), or immediately for
        poison messages (Requirement 11.7). Failures propagate: a failed
        delete means SQS will redeliver, and the worker's persisted
        attempt counter makes that safe (Requirement 11.5).
        """
        async with self._client_factory() as client:
            await client.delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )

    async def healthcheck(self) -> bool:
        """Return whether the queue is reachable right now.

        Issues ``GetQueueAttributes`` under a short ``asyncio.wait_for``
        bound covering the whole round trip (client construction
        included), so a dead or hanging endpoint resolves to ``False``
        within ``healthcheck_timeout_seconds`` instead of stalling
        ``/healthz`` (Requirement 16.1; design §7 "short timeout").

        Never raises. On failure one structured warning is emitted
        carrying the exception type only — never the queue URL, the
        endpoint address, or credentials (Requirement 16.6).
        """

        async def _probe() -> None:
            async with self._client_factory() as client:
                await client.get_queue_attributes(
                    QueueUrl=self._queue_url,
                    AttributeNames=["ApproximateNumberOfMessages"],
                )

        try:
            await asyncio.wait_for(_probe(), timeout=self._healthcheck_timeout_seconds)
        except TimeoutError:
            _log.warning("job_queue_healthcheck_failed", reason="timeout")
            return False
        except Exception as exc:
            # Exception type only — the message could embed the endpoint
            # or queue URL, which must never leak (Requirement 16.6).
            _log.warning("job_queue_healthcheck_failed", reason=type(exc).__name__)
            return False
        return True


def _build_sqs_client_factory(settings: Settings) -> SqsClientFactory:
    """Build the production SQS client factory from ``Settings``.

    Every connection parameter comes from :class:`Settings` — queue
    location (``sqs_region``, ``sqs_endpoint_url``) and the single
    Settings-sourced AWS credential pair (see module docstring). The
    secret is read via :meth:`SecretStr.get_secret_value` only inside
    the returned closure, at client-construction time, mirroring
    ``core/storage.py``. ``sqs_endpoint_url`` is ``None`` in production
    so aioboto3 targets real AWS SQS; LocalStack supplies a non-AWS URL
    locally (Requirement 11.3 — the two environments differ only in
    these configuration values).
    """
    session = aioboto3.Session()

    def _factory() -> AbstractAsyncContextManager[Any]:
        # ``Any`` justified: aioboto3 is untyped (no py.typed / stubs);
        # the untyped client surface stays contained to this module.
        client_cm: AbstractAsyncContextManager[Any] = session.client(
            "sqs",
            region_name=settings.sqs_region,
            endpoint_url=settings.sqs_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        )
        return client_cm

    return _factory


@lru_cache(maxsize=1)
def get_job_queue() -> JobQueue:
    """Return the process-wide :class:`JobQueue`.

    Cached like :func:`~matchlayer_api.config.get_settings` and
    :func:`~matchlayer_api.core.storage.get_resume_storage`: the aioboto3
    session is built once and reused (sessions are cheap, thread-safe
    factories; the per-call client context managers own connection
    lifecycle). Tests construct :class:`JobQueue` directly around fake
    client factories rather than mutating this cache.
    """
    settings = get_settings()
    return JobQueue(
        _build_sqs_client_factory(settings),
        queue_url=settings.sqs_queue_url,
    )
