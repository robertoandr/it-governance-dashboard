"""Unit tests for itgov.observability.smoke_test."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from itgov.observability.smoke_test import run


class TestSmokeTestRun:
    @patch("itgov.observability.smoke_test.configure_observability")
    def test_configures_with_smoke_service_name(self, mock_configure: MagicMock) -> None:
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = mock_span
        mock_configure.return_value = mock_provider

        with patch("itgov.observability.smoke_test.get_tracer", return_value=mock_tracer):
            run("localhost:4317")

        mock_configure.assert_called_once_with(
            service_name="itgov-smoke-test",
            service_version="smoke",
            environment="test",
            otlp_endpoint="localhost:4317",
            insecure=True,
        )

    @patch("itgov.observability.smoke_test.configure_observability")
    def test_uses_custom_endpoint(self, mock_configure: MagicMock) -> None:
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = mock_span
        mock_configure.return_value = mock_provider

        with patch("itgov.observability.smoke_test.get_tracer", return_value=mock_tracer):
            run("otel-collector:4317")

        _, kwargs = mock_configure.call_args
        assert kwargs["otlp_endpoint"] == "otel-collector:4317"

    @patch("itgov.observability.smoke_test.configure_observability")
    def test_calls_provider_shutdown(self, mock_configure: MagicMock) -> None:
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = mock_span
        mock_configure.return_value = mock_provider

        with patch("itgov.observability.smoke_test.get_tracer", return_value=mock_tracer):
            run()

        mock_provider.shutdown.assert_called_once()

    @patch("itgov.observability.smoke_test.configure_observability")
    def test_emits_three_spans(self, mock_configure: MagicMock) -> None:
        mock_provider = MagicMock()
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = mock_span
        mock_configure.return_value = mock_provider

        with patch("itgov.observability.smoke_test.get_tracer", return_value=mock_tracer):
            run()

        assert mock_tracer.start_as_current_span.call_count == 3
        span_names = [c.args[0] for c in mock_tracer.start_as_current_span.call_args_list]
        assert "smoke-test" in span_names
        assert "db-check" in span_names
        assert "http-check" in span_names
