"""OpenTelemetry tracing setup for the API and the Agent_Worker.

Exposes :func:`configure_tracing`, the single entry point that wires the
OpenTelemetry SDK at process startup (API app factory and worker main),
plus the W3C trace-context inject/extract helpers used to propagate the
current trace across the SQS Job_Queue as message attributes.

Behaviour follows the phase-4-agentic design (§9) and Requirement 13:

* **No-op when unconfigured.** If ``MATCHLAYER_OTEL_EXPORTER_OTLP_ENDPOINT``
  is empty/unset, :func:`configure_tracing` leaves OpenTelemetry's default
  no-op tracer in place. Every span operation becomes a cheap no-op, so a
  traced and an untraced run produce identical request outcomes,
  Job_Statuses, and Analysis_Results (Requirement 13.5).
* **Best-effort export.** When an endpoint is configured, a
  ``TracerProvider`` with an OTLP exporter behind a ``BatchSpanProcessor``
  is installed. Span export happens on a background thread and a failed
  export can never alter a request outcome; each export failure produces
  at most one structured warning log (Requirement 13.7).
* **Context propagation over SQS.** :func:`inject_trace_context` renders
  the current trace context into SQS ``MessageAttributes`` shape
  (``traceparent``/``tracestate`` headers only — identifiers, never
  Restricted content), and :func:`extract_trace_context` recovers it in
  the worker. Missing or invalid context yields a fresh empty context so
  the worker starts a new trace and processes the message normally
  (Requirements 13.4, 13.6).
* **PII rule.** Nothing in this module writes span names, attributes, or
  events; it only wires plumbing. Span-emitting call sites (the agent
  base class and the worker) carry identifiers and hashes only, per
  ``security.md`` and Requirement 13.3.

Design reference: phase-4-agentic design §9.
Requirements covered: 13.3, 13.4, 13.5, 13.7.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import structlog
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from matchlayer_api.config import Settings

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan

__all__ = [
    "configure_tracing",
    "extract_trace_context",
    "inject_trace_context",
]

_log = structlog.get_logger(__name__)

# One shared propagator instance: the W3C Trace Context standard
# (``traceparent`` / ``tracestate`` headers). Stateless and thread-safe.
_PROPAGATOR = TraceContextTextMapPropagator()

# The OTLP/HTTP signal path appended to a base collector endpoint, per the
# OTLP spec. Mirrors what the SDK itself does with the
# ``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variable so operators can
# configure the familiar base URL (e.g. ``http://otel-collector:4318``).
_OTLP_TRACES_PATH = "/v1/traces"


class _WarnOnFailureExporter(SpanExporter):
    """Span-exporter decorator that makes export failures loud but harmless.

    The OTLP exporter can raise (misconfiguration, serialization edge
    cases) or return :attr:`SpanExportResult.FAILURE` (unreachable
    collector after retries). Either way the failure must never propagate
    into request/job handling — ``BatchSpanProcessor`` already runs
    exports on a background thread, and this wrapper guarantees that a
    raising delegate is converted into a ``FAILURE`` result while emitting
    exactly one structured warning per failed export batch
    (Requirement 13.7). The warning carries the failure reason only —
    span payloads (which are PII-free by the Requirement 13.3 discipline
    anyway) are never logged.
    """

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Delegate export; convert any failure into one warning + FAILURE."""
        try:
            result = self._delegate.export(spans)
        except Exception as exc:
            _log.warning(
                "otel_span_export_failed",
                reason=f"{type(exc).__name__}: {exc}",
                span_count=len(spans),
            )
            return SpanExportResult.FAILURE
        if result is SpanExportResult.FAILURE:
            _log.warning(
                "otel_span_export_failed",
                reason="exporter returned FAILURE",
                span_count=len(spans),
            )
        return result

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def _traces_endpoint(endpoint: str) -> str:
    """Return the full OTLP/HTTP traces URL for a configured endpoint.

    Accepts either the base collector URL (``http://otel:4318``) or an
    already-complete traces URL (``http://otel:4318/v1/traces``) and
    returns the latter, matching the SDK's own base-URL semantics for
    ``OTEL_EXPORTER_OTLP_ENDPOINT``.
    """
    trimmed = endpoint.rstrip("/")
    if trimmed.endswith(_OTLP_TRACES_PATH):
        return trimmed
    return trimmed + _OTLP_TRACES_PATH


def build_tracer_provider(endpoint: str, service_name: str) -> TracerProvider | None:
    """Build the SDK ``TracerProvider`` for ``endpoint``, or ``None`` when unset.

    Pure construction — no global state is touched, which keeps this
    testable without polluting the process-wide tracer provider (the
    OpenTelemetry global provider is set-once by design).

    Args:
        endpoint: The OTLP exporter endpoint. Empty/whitespace means
            tracing stays a no-op (Requirement 13.5).
        service_name: The ``service.name`` resource attribute
            (``matchlayer-api`` for the API process, ``matchlayer-worker``
            for the Agent_Worker).

    Returns:
        A configured :class:`TracerProvider`, or ``None`` when no endpoint
        is configured.
    """
    stripped = endpoint.strip()
    if not stripped:
        return None
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=_traces_endpoint(stripped))
    provider.add_span_processor(BatchSpanProcessor(_WarnOnFailureExporter(exporter)))
    return provider


def configure_tracing(settings: Settings) -> None:
    """Configure OpenTelemetry for this process. Called once at startup.

    When ``settings.otel_exporter_otlp_endpoint`` is empty, this returns
    without touching the global tracer provider: OpenTelemetry's default
    proxy provider hands out no-op tracers, so all span creation and
    context propagation remain valid, side-effect-free calls
    (Requirement 13.5 — outcomes identical with or without an exporter).

    When an endpoint is configured, the global provider is set to a
    ``TracerProvider`` (resource ``service.name`` from
    ``settings.otel_service_name``) exporting via OTLP through a
    ``BatchSpanProcessor``. Export runs on a background thread; failures
    are absorbed by :class:`_WarnOnFailureExporter` and can never alter a
    request outcome, Job_Status, or Analysis_Result (Requirement 13.7).

    Note: OpenTelemetry's global provider is set-once — a second call
    with a configured endpoint logs an SDK-internal notice and keeps the
    first provider. Startup paths call this exactly once per process.

    Args:
        settings: The validated :class:`~matchlayer_api.config.Settings`
            instance (Requirement 13.5 — exporter configurable via
            ``pydantic-settings`` only).
    """
    provider = build_tracer_provider(
        settings.otel_exporter_otlp_endpoint,
        settings.otel_service_name,
    )
    if provider is None:
        return
    trace.set_tracer_provider(provider)


def inject_trace_context() -> dict[str, dict[str, str]]:
    """Render the current trace context as SQS ``MessageAttributes``.

    Called by the Job_Queue client at enqueue time so the analyze
    request's trace continues into the worker (Requirement 13.4). The
    returned mapping carries W3C trace-context headers only —
    ``traceparent`` and, when present, ``tracestate`` — i.e. identifiers,
    never Resume or Job_Description text.

    With no active span (or the no-op tracer), the propagator injects
    nothing and an empty dict is returned, which is a valid (absent)
    ``MessageAttributes`` value — the worker then starts a new trace per
    Requirement 13.6.

    Returns:
        A dict in the SQS ``MessageAttributes`` request shape:
        ``{name: {"DataType": "String", "StringValue": value}}``.
    """
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return {name: {"DataType": "String", "StringValue": value} for name, value in carrier.items()}


def extract_trace_context(message_attributes: Mapping[str, object] | None) -> Context:
    """Recover the trace context from received SQS message attributes.

    Called by the Agent_Worker for each consumed message. Accepts the
    attribute shape ``aioboto3``'s ``receive_message`` returns
    (``{name: {"DataType": ..., "StringValue": ...}}``) and, leniently,
    plain string values.

    Missing, malformed, or invalid trace context never raises: the
    returned context is then empty, so spans started under it begin a
    fresh trace and message processing continues normally
    (Requirement 13.6).

    Args:
        message_attributes: The message's ``MessageAttributes`` mapping,
            or ``None`` when the message carries none.

    Returns:
        The extracted :class:`Context` when a valid ``traceparent`` is
        present; otherwise an empty :class:`Context` (new trace).
    """
    if not message_attributes:
        return Context()
    try:
        carrier: dict[str, str] = {}
        for name, value in message_attributes.items():
            if isinstance(value, str):
                carrier[name.lower()] = value
            elif isinstance(value, Mapping):
                string_value = value.get("StringValue")
                if isinstance(string_value, str):
                    carrier[name.lower()] = string_value
        return _PROPAGATOR.extract(carrier, context=Context())
    except Exception as exc:
        # Defensive: a hostile or corrupt attributes payload must never
        # fail or skip message processing (Requirement 13.6).
        _log.warning(
            "otel_trace_context_extract_failed",
            reason=f"{type(exc).__name__}: {exc}",
        )
        return Context()
