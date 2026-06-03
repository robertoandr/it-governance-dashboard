"""Smoke test — emits a test trace to the configured OTEL collector.

Run:
    python -m itgov.observability.smoke_test [otlp_endpoint]

Default endpoint: localhost:4317
The trace will appear in Tempo under service.name=itgov-smoke-test.
"""

from __future__ import annotations

import sys
import time

from itgov.observability.setup import configure_observability, get_tracer


def run(otlp_endpoint: str = "localhost:4317") -> None:
    provider = configure_observability(
        service_name="itgov-smoke-test",
        service_version="smoke",
        environment="test",
        otlp_endpoint=otlp_endpoint,
        insecure=True,
    )

    tracer = get_tracer("itgov.smoke")

    with tracer.start_as_current_span("smoke-test") as root:
        root.set_attribute("smoke.ok", True)
        root.set_attribute("smoke.endpoint", otlp_endpoint)

        with tracer.start_as_current_span("db-check"):
            time.sleep(0.010)

        with tracer.start_as_current_span("http-check"):
            time.sleep(0.010)

    provider.shutdown()
    print(f"smoke_test: trace exported → service.name=itgov-smoke-test  endpoint={otlp_endpoint}")


if __name__ == "__main__":
    endpoint = sys.argv[1] if len(sys.argv) > 1 else "localhost:4317"
    run(endpoint)
