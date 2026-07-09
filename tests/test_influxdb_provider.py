"""Tests for InfluxDBMetricsProvider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.influxdb_provider import (
    InfluxDBMetricsProvider,
    _merge_time_score,
    _pr_velocity_score,
)
from app.services.mock_data import MockMetricsProvider
from itgov.models.zendesk import CSATSummary, SLAMetric

# ── Scoring helpers ─────────────────────────────────────────────────────────


class TestPrVelocityScore:
    def test_zero_prs(self) -> None:
        assert _pr_velocity_score(0) == 0.0

    def test_ideal_prs(self) -> None:
        assert _pr_velocity_score(40) == 100.0

    def test_over_ideal_capped(self) -> None:
        assert _pr_velocity_score(100) == 100.0

    def test_partial(self) -> None:
        score = _pr_velocity_score(20)
        assert 49.0 <= score <= 51.0


class TestMergeTimeScore:
    def test_under_ideal_is_100(self) -> None:
        assert _merge_time_score(4.0) == 100.0

    def test_at_ideal_is_100(self) -> None:
        assert _merge_time_score(8.0) == 100.0

    def test_very_slow_floored_at_zero(self) -> None:
        assert _merge_time_score(50.0) == 0.0

    def test_over_ideal_penalised(self) -> None:
        score = _merge_time_score(16.0)
        # 8h over ideal × 3 pts/h = 24 pts lost → 76
        assert score == pytest.approx(76.0, abs=1.0)


# ── Provider with mocked InfluxDB client ─────────────────────────────────────


def _make_influx_record(value: float) -> MagicMock:
    rec = MagicMock()
    rec.values = {"_value": value, "_field": "time_to_merge_seconds"}
    return rec


def _make_influx_table(values: list[float]) -> MagicMock:
    table = MagicMock()
    table.columns = [MagicMock(label="_value"), MagicMock(label="_field")]
    table.records = [_make_influx_record(v) for v in values]
    return table


@pytest.fixture
def provider() -> InfluxDBMetricsProvider:
    return InfluxDBMetricsProvider(
        url="http://localhost:8086",
        token="test-token",
        org="test-org",
        bucket_raw="governance_raw",
    )


def _patch_query(provider: InfluxDBMetricsProvider, return_rows: list[dict]):
    """Patch provider._query to return pre-built row dicts."""
    return patch.object(provider, "_query", return_value=return_rows)


def _make_rows(merge_seconds: list[float]) -> list[dict]:
    return [{"_value": v, "_field": "time_to_merge_seconds"} for v in merge_seconds]


class TestGetValueMetrics:
    def test_real_data_returns_pr_components(self, provider: InfluxDBMetricsProvider) -> None:
        rows = _make_rows([7200.0] * 10)
        with _patch_query(provider, rows):
            result = provider.get_value_metrics()

        ids = [c["id"] for c in result["components"]]
        assert "pr_velocity" in ids
        assert "pr_merge_time" in ids
        # project_delivery from mock should be removed
        assert "project_delivery" not in ids

    def test_real_data_scores_in_range(self, provider: InfluxDBMetricsProvider) -> None:
        rows = _make_rows([14400.0, 28800.0, 7200.0])
        with _patch_query(provider, rows):
            result = provider.get_value_metrics()

        for comp in result["components"]:
            assert 0.0 <= comp["value"] <= 100.0, f"Component {comp['id']} out of range"

    def test_no_data_returns_coming_soon(self, provider: InfluxDBMetricsProvider) -> None:
        with _patch_query(provider, []):
            result = provider.get_value_metrics()

        # Sprint 12: sem dados reais → coming_soon (sem fake numbers), não mock completo.
        assert result["_meta"]["data_source"] == "coming_soon"
        ids = {c["id"] for c in result["components"]}
        assert "ticket_resolution_rate" in ids
        assert "user_satisfaction" in ids

    def test_influxdb_error_returns_coming_soon(self, provider: InfluxDBMetricsProvider) -> None:
        # _query captura exceções internamente e retorna [] → coming_soon (sem fake numbers).
        with _patch_query(provider, []):
            result = provider.get_value_metrics()

        assert result["_meta"]["data_source"] == "coming_soon"
        ids = {c["id"] for c in result["components"]}
        assert "ticket_resolution_rate" in ids
        assert "user_satisfaction" in ids

    def test_source_is_github(self, provider: InfluxDBMetricsProvider) -> None:
        rows = _make_rows([7200.0, 14400.0])
        with _patch_query(provider, rows):
            result = provider.get_value_metrics()

        github_comps = [c for c in result["components"] if c["source"] == "github"]
        assert len(github_comps) == 2


class TestZendeskSlaStats:
    """_zendesk_sla_stats() deve instanciar ZendeskService com credenciais
    reais (subdomain/email/api_token) e usar get_csat_summary(), não
    get_satisfaction_ratings() (que retorna uma lista, não um resumo)."""

    def test_disabled_returns_empty_without_calling_service(self, provider: InfluxDBMetricsProvider) -> None:
        with patch("config.ZENDESK_ENABLED", False):
            result = provider._zendesk_sla_stats()

        assert result == {}

    def test_enabled_instantiates_service_with_credentials(self, provider: InfluxDBMetricsProvider) -> None:
        mock_sla = MagicMock(spec=SLAMetric, compliance_pct=91.4, total_tickets=50, breached=4)
        mock_csat = MagicMock(spec=CSATSummary, csat_pct=88.0, sample_size=12)
        mock_svc = MagicMock()
        mock_svc.get_sla_metrics.return_value = mock_sla
        mock_svc.get_csat_summary.return_value = mock_csat

        with (
            patch("config.ZENDESK_ENABLED", True),
            patch("config.ZENDESK_SUBDOMAIN", "grupogadens"),
            patch("config.ZENDESK_EMAIL", "roberto@grupogadens.com.br"),
            patch("config.ZENDESK_API_TOKEN", "fake-token"),
            patch("config.ZENDESK_GROUP_ID", 123),
            patch("itgov.services.zendesk_service.ZendeskService", return_value=mock_svc) as mock_cls,
        ):
            result = provider._zendesk_sla_stats()

        mock_cls.assert_called_once_with(
            subdomain="grupogadens",
            email="roberto@grupogadens.com.br",
            api_token="fake-token",
            group_id=123,
        )
        mock_svc.get_csat_summary.assert_called_once()
        assert result == {
            "compliance_pct": 91.4,
            "total_open": 50,
            "breached": 4,
            "csat_pct": 88.0,
            "csat_sample": 12,
        }

    def test_service_exception_returns_empty(self, provider: InfluxDBMetricsProvider) -> None:
        with (
            patch("config.ZENDESK_ENABLED", True),
            patch("config.ZENDESK_SUBDOMAIN", "grupogadens"),
            patch("config.ZENDESK_EMAIL", "roberto@grupogadens.com.br"),
            patch("config.ZENDESK_API_TOKEN", "fake-token"),
            patch("config.ZENDESK_GROUP_ID", 0),
            patch(
                "itgov.services.zendesk_service.ZendeskService",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = provider._zendesk_sla_stats()

        assert result == {}


class TestPassThroughPillars:
    """Non-value pillars delegate to MockMetricsProvider."""

    def test_strategic_is_mock(self, provider: InfluxDBMetricsProvider) -> None:
        mock = MockMetricsProvider()
        result = provider.get_strategic_metrics()
        assert {c["id"] for c in result["components"]} == {c["id"] for c in mock.get_strategic_metrics()["components"]}

    def test_risk_is_mock(self, provider: InfluxDBMetricsProvider) -> None:
        mock = MockMetricsProvider()
        result = provider.get_risk_metrics()
        assert {c["id"] for c in result["components"]} == {c["id"] for c in mock.get_risk_metrics()["components"]}

    def test_resource_is_mock(self, provider: InfluxDBMetricsProvider) -> None:
        mock = MockMetricsProvider()
        result = provider.get_resource_metrics()
        assert {c["id"] for c in result["components"]} == {c["id"] for c in mock.get_resource_metrics()["components"]}

    def test_performance_is_mock(self, provider: InfluxDBMetricsProvider) -> None:
        mock = MockMetricsProvider()
        result = provider.get_performance_metrics()
        assert {c["id"] for c in result["components"]} == {
            c["id"] for c in mock.get_performance_metrics()["components"]
        }


# ── _build_provider factory ───────────────────────────────────────────────────


class TestBuildProvider:
    def test_returns_mock_when_influxdb_disabled(self) -> None:
        from app.services.metrics_aggregator import _build_provider

        with patch("app.services.metrics_aggregator.get_settings") as mock_settings:
            mock_settings.return_value.influx.enabled = False
            provider = _build_provider()
        assert isinstance(provider, MockMetricsProvider)

    def test_returns_influxdb_when_enabled(self) -> None:
        from app.services.influxdb_provider import InfluxDBMetricsProvider
        from app.services.metrics_aggregator import _build_provider

        with patch("app.services.metrics_aggregator.get_settings") as mock_settings:
            mock_settings.return_value.influx.enabled = True
            mock_settings.return_value.influx.url = "http://localhost:8086"
            mock_settings.return_value.influx.token.get_secret_value.return_value = "tok"
            mock_settings.return_value.influx.org = "org"
            mock_settings.return_value.influx.bucket_raw = "governance_raw"
            provider = _build_provider()
        assert isinstance(provider, InfluxDBMetricsProvider)
