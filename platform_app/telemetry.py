"""OpenTelemetry tracing with content-free attributes and durable queue context."""

from __future__ import annotations

from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from platform_app.config import settings

tracer = trace.get_tracer("ai_engineering_platform", "0.1.0")
_propagator = TraceContextTextMapPropagator()
_configured = False


def configure_telemetry() -> None:
    """Export only when an operator configures an OTLP trace endpoint."""
    global _configured
    if _configured:
        return
    endpoint = settings().otlp_traces_endpoint
    if not endpoint:
        return
    parsed = urlsplit(endpoint)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (settings().environment == "development" and local):
        raise ValueError("OTLP trace endpoint must use HTTPS outside local development")
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("OTLP trace endpoint must not embed credentials")

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "ai-engineering-platform"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True


def inject_trace() -> dict[str, str]:
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return {key: value for key, value in carrier.items() if key in {"traceparent", "tracestate"}}


def extract_trace(carrier: dict):
    safe = {
        key: value
        for key, value in carrier.items()
        if key in {"traceparent", "tracestate"} and isinstance(value, str) and len(value) < 512
    }
    return _propagator.extract(safe)


def set_safe_attributes(**values) -> None:
    span = trace.get_current_span()
    for key, value in values.items():
        if isinstance(value, str | int | float | bool):
            span.set_attribute(f"aip.{key}", value)
