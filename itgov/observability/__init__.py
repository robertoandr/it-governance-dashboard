"""OpenTelemetry observability bootstrap for itgov."""

from __future__ import annotations

from itgov.observability.setup import configure_observability, get_tracer, shutdown

__all__ = ["configure_observability", "get_tracer", "shutdown"]
