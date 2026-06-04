"""Unit tests for itgov.observability.metrics."""

from __future__ import annotations

import opentelemetry.metrics._internal as _otel_m
import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import itgov.observability.metrics as obs_metrics
from itgov.observability.metrics import get_meter, setup_metrics, shutdown_metrics


@pytest.fixture(autouse=True)
def reset_otel_metrics_state() -> None:  # type: ignore[misc]
    """Reset OTel metrics global singleton around each test."""
    orig_provider = _otel_m._METER_PROVIDER  # type: ignore[attr-defined]
    orig_done = _otel_m._METER_PROVIDER_SET_ONCE._done  # type: ignore[attr-defined]
    orig_module = obs_metrics._meter_provider

    _otel_m._METER_PROVIDER = None  # type: ignore[attr-defined]
    _otel_m._METER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    obs_metrics._meter_provider = None

    yield

    _otel_m._METER_PROVIDER = orig_provider  # type: ignore[attr-defined]
    _otel_m._METER_PROVIDER_SET_ONCE._done = orig_done  # type: ignore[attr-defined]
    obs_metrics._meter_provider = orig_module


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_in_memory_provider(service_name: str = "test-svc") -> tuple[SdkMeterProvider, InMemoryMetricReader]:
    """Return an SDK MeterProvider backed by InMemoryMetricReader."""
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(metric_readers=[reader])
    otel_metrics.set_meter_provider(provider)
    obs_metrics._meter_provider = provider
    return provider, reader


def _collect(reader: InMemoryMetricReader) -> dict[str, list]:
    """Collect finished metrics into {name: [data_points]} dict."""
    data = reader.get_metrics_data()
    result: dict[str, list] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                result[m.name] = list(m.data.data_points)
    return result


# ── setup_metrics ─────────────────────────────────────────────────────────────


class TestSetupMetrics:
    def test_returns_meter_provider(self) -> None:
        from unittest.mock import patch

        with patch("itgov.observability.metrics.OTLPMetricExporter"):
            provider = setup_metrics("test-svc")
        assert isinstance(provider, SdkMeterProvider)

    def test_sets_global_meter_provider(self) -> None:
        from unittest.mock import patch

        with patch("itgov.observability.metrics.OTLPMetricExporter"):
            provider = setup_metrics("test-svc")
        assert otel_metrics.get_meter_provider() is provider

    def test_stores_in_module_global(self) -> None:
        from unittest.mock import patch

        with patch("itgov.observability.metrics.OTLPMetricExporter"):
            provider = setup_metrics("test-svc")
        assert obs_metrics._meter_provider is provider

    def test_resource_service_name(self) -> None:
        from unittest.mock import patch

        with patch("itgov.observability.metrics.OTLPMetricExporter"):
            provider = setup_metrics("my-service", service_version="1.0")
        attrs = provider._sdk_config.resource.attributes  # type: ignore[attr-defined]
        assert attrs.get("service.name") == "my-service"
        assert attrs.get("service.version") == "1.0"

    def test_resource_environment(self) -> None:
        from unittest.mock import patch

        with patch("itgov.observability.metrics.OTLPMetricExporter"):
            provider = setup_metrics("svc", environment="staging")
        attrs = provider._sdk_config.resource.attributes  # type: ignore[attr-defined]
        assert attrs.get("deployment.environment") == "staging"

    def test_passes_endpoint_to_exporter(self) -> None:
        from unittest.mock import patch

        with patch("itgov.observability.metrics.OTLPMetricExporter") as mock_cls:
            setup_metrics("svc", otlp_endpoint="collector:4317")
        mock_cls.assert_called_once_with(endpoint="collector:4317", insecure=True)


# ── get_meter ──────────────────────────────────────────────────────────────────


class TestGetMeter:
    def test_returns_meter(self) -> None:
        _make_in_memory_provider()
        meter = get_meter("itgov.test")
        assert meter is not None

    def test_counter_increments(self) -> None:
        provider, reader = _make_in_memory_provider()
        meter = get_meter("itgov.test")
        counter = meter.create_counter("test_total")
        counter.add(3, {"env": "test"})
        provider.force_flush()

        metrics_map = _collect(reader)
        assert "test_total" in metrics_map
        dp = metrics_map["test_total"][0]
        assert dp.value == 3
        assert dict(dp.attributes) == {"env": "test"}

    def test_histogram_records(self) -> None:
        provider, reader = _make_in_memory_provider()
        meter = get_meter("itgov.test")
        hist = meter.create_histogram("test_duration_seconds", unit="s")
        hist.record(0.42, {"op": "fetch"})
        hist.record(1.1, {"op": "fetch"})
        provider.force_flush()

        metrics_map = _collect(reader)
        assert "test_duration_seconds" in metrics_map
        dp = metrics_map["test_duration_seconds"][0]
        assert dp.count == 2
        assert abs(dp.sum - 1.52) < 0.001

    def test_gauge_set(self) -> None:
        provider, reader = _make_in_memory_provider()
        meter = get_meter("itgov.test")
        gauge = meter.create_gauge("test_current")
        gauge.set(99, {"risk_level": "critical"})
        provider.force_flush()

        metrics_map = _collect(reader)
        assert "test_current" in metrics_map
        dp = metrics_map["test_current"][0]
        assert dp.value == 99

    def test_resource_attributes_present(self) -> None:
        from opentelemetry.sdk.resources import Resource

        reader = InMemoryMetricReader()
        resource = Resource.create({"service.name": "my-svc", "service.version": "2.0"})
        provider = SdkMeterProvider(resource=resource, metric_readers=[reader])
        otel_metrics.set_meter_provider(provider)
        obs_metrics._meter_provider = provider

        meter = get_meter("itgov.test")
        meter.create_counter("probe").add(1)
        provider.force_flush()

        data = reader.get_metrics_data()
        assert data.resource_metrics
        attrs = data.resource_metrics[0].resource.attributes
        assert attrs.get("service.name") == "my-svc"
        assert attrs.get("service.version") == "2.0"


# ── shutdown_metrics ──────────────────────────────────────────────────────────


class TestShutdownMetrics:
    def test_noop_when_no_provider(self) -> None:
        obs_metrics._meter_provider = None
        shutdown_metrics()  # must not raise

    def test_calls_provider_shutdown(self) -> None:
        from unittest.mock import MagicMock

        mock_provider = MagicMock(spec=SdkMeterProvider)
        obs_metrics._meter_provider = mock_provider  # type: ignore[assignment]
        shutdown_metrics()
        mock_provider.shutdown.assert_called_once()
