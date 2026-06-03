"""Unit tests for itgov.observability.setup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

import itgov.observability.setup as obs_setup
from itgov.observability.setup import configure_observability, get_tracer, shutdown


@pytest.fixture(autouse=True)
def reset_otel_state() -> None:  # type: ignore[misc]
    """Reset OTel global singleton and module state around each test."""
    # Save original state
    orig_provider = otel_trace._TRACER_PROVIDER  # type: ignore[attr-defined]
    orig_done = otel_trace._TRACER_PROVIDER_SET_ONCE._done  # type: ignore[attr-defined]
    orig_module_provider = obs_setup._provider

    # Reset before test
    otel_trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    obs_setup._provider = None

    yield

    # Restore after test
    otel_trace._TRACER_PROVIDER = orig_provider  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = orig_done  # type: ignore[attr-defined]
    obs_setup._provider = orig_module_provider


class TestConfigureObservability:
    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_returns_tracer_provider(self, mock_exporter_cls: MagicMock) -> None:
        provider = configure_observability("svc-test")
        assert isinstance(provider, TracerProvider)

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_sets_global_tracer_provider(self, mock_exporter_cls: MagicMock) -> None:
        provider = configure_observability("svc-global")
        assert otel_trace.get_tracer_provider() is provider

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_passes_endpoint_to_exporter(self, mock_exporter_cls: MagicMock) -> None:
        configure_observability("svc", otlp_endpoint="otel-collector:4317")
        mock_exporter_cls.assert_called_once_with(endpoint="otel-collector:4317", insecure=True)

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_resource_contains_service_name(self, mock_exporter_cls: MagicMock) -> None:
        provider = configure_observability("my-service", service_version="1.2.3")
        resource = provider.resource
        assert resource.attributes.get("service.name") == "my-service"
        assert resource.attributes.get("service.version") == "1.2.3"

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_resource_contains_environment(self, mock_exporter_cls: MagicMock) -> None:
        provider = configure_observability("svc", environment="staging")
        assert provider.resource.attributes.get("deployment.environment") == "staging"

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_stores_provider_in_module_global(self, mock_exporter_cls: MagicMock) -> None:
        provider = configure_observability("svc-stored")
        assert obs_setup._provider is provider

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_idempotent_replaces_provider(self, mock_exporter_cls: MagicMock) -> None:
        p1 = configure_observability("svc-a")
        p2 = configure_observability("svc-b")
        assert p1 is not p2
        assert obs_setup._provider is p2


class TestGetTracer:
    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_returns_tracer(self, mock_exporter_cls: MagicMock) -> None:
        configure_observability("svc")
        tracer = get_tracer("itgov.test")
        assert tracer is not None

    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_tracer_can_start_span(self, mock_exporter_cls: MagicMock) -> None:
        configure_observability("svc")
        tracer = get_tracer("itgov.test")
        with tracer.start_as_current_span("test-span") as span:
            assert span is not None


class TestShutdown:
    @patch("itgov.observability.setup.OTLPSpanExporter")
    def test_shutdown_calls_provider_shutdown(self, mock_exporter_cls: MagicMock) -> None:
        provider = configure_observability("svc-shutdown")
        mock_shutdown = MagicMock()
        provider.shutdown = mock_shutdown  # type: ignore[method-assign]

        shutdown()

        mock_shutdown.assert_called_once()

    def test_shutdown_is_noop_when_no_provider(self) -> None:
        obs_setup._provider = None
        shutdown()  # must not raise
