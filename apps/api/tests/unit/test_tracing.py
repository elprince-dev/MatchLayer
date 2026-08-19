"""Unit tests for the OpenTelemetry setup in ``core/tracing.py``.

Covers phase-4-agentic task 9.2 (Requirements 13.3, 13.4, 13.5, 13.7):

* no-op behaviour when ``MATCHLAYER_OTEL_EXPORTER_OTLP_ENDPOINT`` is unset;
* TracerProvider construction (service.name resource) when it is set;
* SQS message-attribute inject/extract round-trip via the W3C
  ``TraceContextTextMapPropagator``;
* missing/invalid trace context yielding a fresh (new-trace) context;
* export failures producing at most one structured warning and never
  raising.

The global tracer provider is deliberately never mutated here — tests
drive the pure builder and helpers so the process-wide OpenTelemetry
state stays untouched for the rest of the suite.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from matchlayer_api.core.tracing import (
    _traces_endpoint,
    _WarnOnFailureExporter,
    build_tracer_provider,
    extract_trace_context,
    inject_trace_context,
)

# ---------------------------------------------------------------------------
# build_tracer_provider — Requirement 13.5
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ["", "   ", "\t\n"])
def test_build_tracer_provider_returns_none_when_endpoint_unset(endpoint: str) -> None:
    """Empty/whitespace endpoint leaves tracing a no-op (Requirement 13.5)."""
    assert build_tracer_provider(endpoint, "matchlayer-api") is None


def test_build_tracer_provider_sets_service_name_resource() -> None:
    """A configured endpoint yields a provider carrying the service name."""
    provider = build_tracer_provider("http://otel-collector:4318", "matchlayer-worker")
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes["service.name"] == "matchlayer-worker"
    provider.shutdown()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://otel:4318", "http://otel:4318/v1/traces"),
        ("http://otel:4318/", "http://otel:4318/v1/traces"),
        ("http://otel:4318/v1/traces", "http://otel:4318/v1/traces"),
        ("https://collector.example.com/v1/traces/", "https://collector.example.com/v1/traces"),
    ],
)
def test_traces_endpoint_normalization(configured: str, expected: str) -> None:
    """Base collector URLs gain the OTLP/HTTP traces path exactly once."""
    assert _traces_endpoint(configured) == expected


# ---------------------------------------------------------------------------
# inject / extract over SQS message attributes — Requirements 13.4, 13.6
# ---------------------------------------------------------------------------


def test_inject_and_extract_round_trip_preserves_trace_id() -> None:
    """traceparent injected at enqueue is recovered by the worker (13.4)."""
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("analyze-request") as span:
        attributes = inject_trace_context()
        sent_trace_id = span.get_span_context().trace_id

    assert "traceparent" in attributes
    assert attributes["traceparent"]["DataType"] == "String"
    assert isinstance(attributes["traceparent"]["StringValue"], str)

    context = extract_trace_context(attributes)
    extracted = trace.get_current_span(context).get_span_context()
    assert extracted.is_valid
    assert extracted.trace_id == sent_trace_id
    provider.shutdown()


def test_inject_without_active_span_returns_empty_attributes() -> None:
    """No active span (no-op tracer) injects nothing — a valid absence."""
    assert inject_trace_context() == {}


@pytest.mark.parametrize(
    "message_attributes",
    [
        None,
        {},
        {"traceparent": {"DataType": "String", "StringValue": "not-a-traceparent"}},
        {"traceparent": {"DataType": "Number", "BinaryValue": b"\x00"}},
        {"unrelated": {"DataType": "String", "StringValue": "x"}},
        {"traceparent": 12345},
    ],
)
def test_extract_missing_or_invalid_context_starts_new_trace(
    message_attributes: dict[str, object] | None,
) -> None:
    """Missing/invalid trace context yields a fresh context (13.6)."""
    context = extract_trace_context(message_attributes)
    span_context = trace.get_current_span(context).get_span_context()
    assert not span_context.is_valid


def test_extract_accepts_plain_string_attribute_values() -> None:
    """Lenient shape: a bare string traceparent still extracts."""
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    context = extract_trace_context({"traceparent": traceparent})
    span_context = trace.get_current_span(context).get_span_context()
    assert span_context.is_valid
    assert span_context.trace_id == 0x0AF7651916CD43DD8448EB211C80319C


# ---------------------------------------------------------------------------
# export failure handling — Requirement 13.7
# ---------------------------------------------------------------------------


class _RaisingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise ConnectionError("collector unreachable")

    def shutdown(self) -> None:  # pragma: no cover - interface completeness
        pass


class _FailingExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:  # pragma: no cover - interface completeness
        pass


def test_export_exception_is_absorbed_with_one_warning() -> None:
    """A raising exporter never propagates; exactly one warning fires (13.7)."""
    wrapper = _WarnOnFailureExporter(_RaisingExporter())
    with structlog.testing.capture_logs() as logs:
        result = wrapper.export([])
    assert result is SpanExportResult.FAILURE
    warnings = [entry for entry in logs if entry["event"] == "otel_span_export_failed"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert "ConnectionError" in str(warnings[0]["reason"])


def test_export_failure_result_logs_one_warning() -> None:
    """A FAILURE result from the delegate produces exactly one warning (13.7)."""
    wrapper = _WarnOnFailureExporter(_FailingExporter())
    with structlog.testing.capture_logs() as logs:
        result = wrapper.export([])
    assert result is SpanExportResult.FAILURE
    warnings = [entry for entry in logs if entry["event"] == "otel_span_export_failed"]
    assert len(warnings) == 1


def test_export_success_logs_nothing() -> None:
    """A successful export emits no warning."""

    class _OkExporter(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:  # pragma: no cover - interface completeness
            pass

    wrapper = _WarnOnFailureExporter(_OkExporter())
    with structlog.testing.capture_logs() as logs:
        result = wrapper.export([])
    assert result is SpanExportResult.SUCCESS
    assert [entry for entry in logs if entry["event"] == "otel_span_export_failed"] == []
