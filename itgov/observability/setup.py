"""OpenTelemetry SDK bootstrap — TracerProvider + OTLP gRPC exporter."""

from __future__ import annotations

import structlog
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

log = structlog.get_logger(__name__)

_provider: TracerProvider | None = None


def configure_observability(
    service_name: str,
    *,
    service_version: str = "dev",
    environment: str = "development",
    otlp_endpoint: str = "localhost:4317",
    insecure: bool = True,
) -> TracerProvider:
    """Bootstrap TracerProvider with OTLP gRPC exporter and register it globally.

    Idempotent: calling again replaces the global provider.
    """
    global _provider

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    _provider = provider

    log.info(
        "observability.configured",
        service=service_name,
        version=service_version,
        env=environment,
        endpoint=otlp_endpoint,
    )
    return provider


def get_tracer(name: str) -> Tracer:
    """Return a named tracer from the global TracerProvider."""
    return otel_trace.get_tracer(name)


def shutdown() -> None:
    """Flush pending spans and shut down the provider."""
    if _provider is not None:
        _provider.shutdown()
        log.info("observability.shutdown")
