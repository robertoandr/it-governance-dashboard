"""OTel Metrics SDK bootstrap — MeterProvider + OTLP gRPC exporter."""

from __future__ import annotations

import structlog
from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

log = structlog.get_logger(__name__)

_meter_provider: SdkMeterProvider | None = None


def setup_metrics(
    service_name: str,
    *,
    service_version: str = "dev",
    environment: str = "development",
    otlp_endpoint: str = "localhost:4317",
    insecure: bool = True,
    export_interval_ms: int = 15_000,
) -> SdkMeterProvider:
    """Bootstrap MeterProvider with OTLP gRPC exporter and register it globally.

    Args:
        service_name: Value for the ``service.name`` resource attribute.
        service_version: Value for ``service.version``.
        environment: Value for ``deployment.environment``.
        otlp_endpoint: OTLP gRPC endpoint (host:port, no scheme).
        insecure: Use insecure gRPC channel.
        export_interval_ms: How often to push metrics to the collector.

    Returns:
        The configured ``MeterProvider``.
    """
    global _meter_provider

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=insecure)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=export_interval_ms)
    provider = SdkMeterProvider(resource=resource, metric_readers=[reader])
    otel_metrics.set_meter_provider(provider)
    _meter_provider = provider

    log.info(
        "metrics.configured",
        service=service_name,
        version=service_version,
        env=environment,
        endpoint=otlp_endpoint,
        interval_ms=export_interval_ms,
    )
    return provider


def get_meter(name: str) -> Meter:
    """Return a named Meter from the global MeterProvider."""
    return otel_metrics.get_meter(name)


def shutdown_metrics() -> None:
    """Flush pending metrics and shut down the provider."""
    if _meter_provider is not None:
        _meter_provider.shutdown()
        log.info("metrics.shutdown")
