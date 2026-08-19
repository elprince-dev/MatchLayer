"""Unit tests for the JobQueue SQS client in ``services/agent_jobs/queue.py``.

Covers phase-4-agentic task 9.1 (Requirements 11.1, 11.3, 13.4, 16.1):

* ``JobMessage`` carries identifiers only and structurally rejects any
  extra field (Requirement 11.1);
* ``enqueue`` serializes the identifiers-only body and attaches the
  current trace context as SQS ``MessageAttributes`` (Requirement 13.4),
  omitting the attributes entirely when no span is active;
* ``receive`` long-polls with ``MessageAttributeNames=["All"]`` and
  returns raw :class:`ReceivedMessage` values;
* ``delete`` acknowledges by receipt handle;
* ``healthcheck`` returns ``True``/``False``, never raises, resolves a
  hanging endpoint within its short timeout, and logs failures with the
  exception type only — never the queue URL (Requirements 16.1, 16.6).

Every test injects a fake client factory: no aioboto3 session, no
LocalStack, no network. Connection parameters are plain constructor
arguments, mirroring how ``get_job_queue`` feeds them from Settings.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pydantic
import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider

from matchlayer_api.services.agent_jobs.queue import (
    JobMessage,
    JobQueue,
    ReceivedMessage,
)

_QUEUE_URL = "http://localstack:4566/000000000000/matchlayer-agent-jobs"


class _FakeSqsClient:
    """Records every call; configurable responses and failure modes."""

    def __init__(
        self,
        *,
        receive_response: dict[str, Any] | None = None,
        raise_exc: Exception | None = None,
        hang_seconds: float = 0.0,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._receive_response = receive_response or {}
        self._raise_exc = raise_exc
        self._hang_seconds = hang_seconds

    async def _maybe_fail(self) -> None:
        if self._hang_seconds:
            await asyncio.sleep(self._hang_seconds)
        if self._raise_exc is not None:
            raise self._raise_exc

    async def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("send_message", kwargs))
        await self._maybe_fail()
        return {"MessageId": "fake"}

    async def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("receive_message", kwargs))
        await self._maybe_fail()
        return self._receive_response

    async def delete_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_message", kwargs))
        await self._maybe_fail()
        return {}

    async def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_queue_attributes", kwargs))
        await self._maybe_fail()
        return {"Attributes": {"ApproximateNumberOfMessages": "0"}}


def _queue_for(client: _FakeSqsClient, **kwargs: Any) -> JobQueue:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[_FakeSqsClient]:
        yield client

    return JobQueue(_factory, queue_url=_QUEUE_URL, **kwargs)


def _message() -> JobMessage:
    return JobMessage(
        job_id="0192aaaa-0000-7000-8000-000000000001",
        match_id="0192aaaa-0000-7000-8000-000000000002",
        user_id="0192aaaa-0000-7000-8000-000000000003",
    )


# ---------------------------------------------------------------------------
# JobMessage — identifiers only, structurally closed (Requirement 11.1)
# ---------------------------------------------------------------------------


def test_job_message_rejects_extra_fields() -> None:
    """Any field beyond the three identifiers is rejected at construction."""
    with pytest.raises(pydantic.ValidationError):
        JobMessage(
            job_id="j",
            match_id="m",
            user_id="u",
            resume_text="raw PII must never fit here",  # type: ignore[call-arg]
        )


def test_job_message_is_frozen() -> None:
    """Messages are immutable after construction."""
    message = _message()
    with pytest.raises(pydantic.ValidationError):
        message.job_id = "other"  # type: ignore[misc]


def test_job_message_serializes_identifiers_only() -> None:
    """The JSON body carries exactly the three identifier fields."""
    body = json.loads(_message().model_dump_json())
    assert set(body) == {"job_id", "match_id", "user_id"}


# ---------------------------------------------------------------------------
# enqueue — body + trace context as message attributes (11.1, 13.4)
# ---------------------------------------------------------------------------


async def test_enqueue_sends_identifiers_only_body_to_queue_url() -> None:
    """enqueue sends the JobMessage JSON body to the configured queue URL."""
    client = _FakeSqsClient()
    queue = _queue_for(client)
    message = _message()

    await queue.enqueue(message)

    assert len(client.calls) == 1
    name, kwargs = client.calls[0]
    assert name == "send_message"
    assert kwargs["QueueUrl"] == _QUEUE_URL
    assert json.loads(kwargs["MessageBody"]) == message.model_dump()


async def test_enqueue_without_active_span_omits_message_attributes() -> None:
    """With no active span the MessageAttributes key is absent entirely."""
    client = _FakeSqsClient()
    await _queue_for(client).enqueue(_message())

    _, kwargs = client.calls[0]
    assert "MessageAttributes" not in kwargs


async def test_enqueue_attaches_current_trace_context_as_attributes() -> None:
    """An active span's traceparent rides along as SQS MessageAttributes."""
    client = _FakeSqsClient()
    queue = _queue_for(client)
    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("analyze-request"):
        await queue.enqueue(_message())
    provider.shutdown()

    _, kwargs = client.calls[0]
    attributes = kwargs["MessageAttributes"]
    assert "traceparent" in attributes
    assert attributes["traceparent"]["DataType"] == "String"
    assert isinstance(attributes["traceparent"]["StringValue"], str)


# ---------------------------------------------------------------------------
# receive — long poll, raw messages out (11.1; trace attrs travel per 13.4)
# ---------------------------------------------------------------------------


async def test_receive_long_polls_with_all_message_attributes() -> None:
    """receive requests long-poll wait and every message attribute."""
    client = _FakeSqsClient()
    queue = _queue_for(client, wait_time_seconds=10, max_messages=1)

    assert await queue.receive() == []

    name, kwargs = client.calls[0]
    assert name == "receive_message"
    assert kwargs["QueueUrl"] == _QUEUE_URL
    assert kwargs["WaitTimeSeconds"] == 10
    assert kwargs["MaxNumberOfMessages"] == 1
    assert kwargs["MessageAttributeNames"] == ["All"]


async def test_receive_returns_raw_messages_with_attributes() -> None:
    """Received messages surface body, receipt handle, and raw attributes."""
    trace_attr = {"DataType": "String", "StringValue": "00-abc-def-01"}
    client = _FakeSqsClient(
        receive_response={
            "Messages": [
                {
                    "Body": '{"job_id": "j", "match_id": "m", "user_id": "u"}',
                    "ReceiptHandle": "rh-1",
                    "MessageAttributes": {"traceparent": trace_attr},
                },
                {"Body": "not json at all", "ReceiptHandle": "rh-2"},
            ]
        }
    )
    queue = _queue_for(client)

    received = await queue.receive()

    assert received == [
        ReceivedMessage(
            body='{"job_id": "j", "match_id": "m", "user_id": "u"}',
            receipt_handle="rh-1",
            message_attributes={"traceparent": trace_attr},
        ),
        # Malformed bodies come back raw — poison handling is the worker's.
        ReceivedMessage(body="not json at all", receipt_handle="rh-2"),
    ]


# ---------------------------------------------------------------------------
# delete — acknowledge by receipt handle (11.1)
# ---------------------------------------------------------------------------


async def test_delete_acknowledges_by_receipt_handle() -> None:
    """delete forwards the receipt handle to DeleteMessage."""
    client = _FakeSqsClient()
    await _queue_for(client).delete("rh-42")

    assert client.calls == [("delete_message", {"QueueUrl": _QUEUE_URL, "ReceiptHandle": "rh-42"})]


# ---------------------------------------------------------------------------
# healthcheck — GetQueueAttributes under a short timeout (16.1, 16.6)
# ---------------------------------------------------------------------------


async def test_healthcheck_returns_true_when_queue_reachable() -> None:
    """A successful GetQueueAttributes round trip reports healthy."""
    client = _FakeSqsClient()
    assert await _queue_for(client).healthcheck() is True
    name, kwargs = client.calls[0]
    assert name == "get_queue_attributes"
    assert kwargs["QueueUrl"] == _QUEUE_URL


async def test_healthcheck_returns_false_on_error_without_raising() -> None:
    """A failing client yields False plus one warning — never an exception."""
    client = _FakeSqsClient(raise_exc=ConnectionError("http://secret-endpoint:4566"))
    queue = _queue_for(client)

    with structlog.testing.capture_logs() as logs:
        assert await queue.healthcheck() is False

    warnings = [e for e in logs if e["event"] == "job_queue_healthcheck_failed"]
    assert len(warnings) == 1
    assert warnings[0]["reason"] == "ConnectionError"
    # Exception type only: neither the queue URL nor the endpoint leaks.
    assert _QUEUE_URL not in str(warnings[0])
    assert "secret-endpoint" not in str(warnings[0])


async def test_healthcheck_times_out_hanging_endpoint() -> None:
    """A hanging endpoint resolves to False within the short timeout."""
    client = _FakeSqsClient(hang_seconds=30.0)
    queue = _queue_for(client, healthcheck_timeout_seconds=0.05)

    with structlog.testing.capture_logs() as logs:
        assert await queue.healthcheck() is False

    warnings = [e for e in logs if e["event"] == "job_queue_healthcheck_failed"]
    assert len(warnings) == 1
    assert warnings[0]["reason"] == "timeout"
