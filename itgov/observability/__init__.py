"""OpenTelemetry observability bootstrap for itgov."""

from __future__ import annotations

from itgov.observability.metrics import get_meter, setup_metrics, shutdown_metrics
from itgov.observability.setup import configure_observability, get_tracer, shutdown

__all__ = [
    "configure_observability",
    "get_meter",
    "get_tracer",
    "setup_metrics",
    "shutdown",
    "shutdown_metrics",
]
