"""Metric emission tests for sp_orphans_collector."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import opentelemetry.metrics._internal as _otel_m
import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import itgov.observability.metrics as obs_metrics
from itgov.services.sp_orphans_collector import SPOrphansCollector

pytestmark = pytest.mark.asyncio

# ── fixtures ──────────────────────────────────────────────────────────────────

_SAMPLE_SP = {
    "id": "sp-1",
    "displayName": "TestApp",
    "servicePrincipalType": "Application",
    "appRoles": [],
    "passwordCredentials": [],
    "keyCredentials": [],
    "signInActivity": {"lastSignInDateTime": "2024-01-01T00:00:00Z"},
    "createdDateTime": "2023-01-01T00:00:00Z",
}

_CRITICAL_SP = {
    "id": "sp-crit",
    "displayName": "DangerApp",
    "servicePrincipalType": "Application",
    "appRoles": [
        {"value": "Application.ReadWrite.All"},
        {"value": "Directory.ReadWrite.All"},
    ],
    "passwordCredentials": [],
    "keyCredentials": [],
    "signInActivity": None,
    "createdDateTime": "2020-01-01T00:00:00Z",
}


@pytest.fixture()
def metric_reader() -> InMemoryMetricReader:  # type: ignore[misc]
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(metric_readers=[reader])

    orig_provider = _otel_m._METER_PROVIDER  # type: ignore[attr-defined]
    orig_done = _otel_m._METER_PROVIDER_SET_ONCE._done  # type: ignore[attr-defined]
    orig_module = obs_metrics._meter_provider

    _otel_m._METER_PROVIDER = None  # type: ignore[attr-defined]
    _otel_m._METER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    otel_metrics.set_meter_provider(provider)
    obs_metrics._meter_provider = provider

    yield reader

    _otel_m._METER_PROVIDER = orig_provider  # type: ignore[attr-defined]
    _otel_m._METER_PROVIDER_SET_ONCE._done = orig_done  # type: ignore[attr-defined]
    obs_metrics._meter_provider = orig_module


def _collect(reader: InMemoryMetricReader) -> dict[str, list]:
    data = reader.get_metrics_data()
    if data is None:
        return {}
    result: dict[str, list] = {}
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                result[m.name] = list(m.data.data_points)
    return result


def _dp_by_attrs(dps: list, **attrs) -> object | None:
    for dp in dps:
        if all(dp.attributes.get(k) == v for k, v in attrs.items()):
            return dp
    return None


def _make_graph_mock(sps: list[dict], delta_link: str = "https://graph.microsoft.com/v1.0/delta"):
    async def _iter(*args, **kwargs):
        for sp in sps:
            yield sp

    graph = MagicMock()
    graph.get_service_principals_delta = _iter
    graph.last_delta_link = delta_link
    return graph


def _make_store_mock(existing_token: str | None = None):
    store = AsyncMock()
    store.get.return_value = existing_token
    return store


# ── m365_collections_total ────────────────────────────────────────────────────


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collections_counter_success(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_collections_total", [])
    dp = _dp_by_attrs(dps, tenant="t1", status="success")
    assert dp is not None
    assert dp.value == 1  # type: ignore[union-attr]


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collections_counter_error(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("Graph exploded")
        yield  # makes it an async generator

    graph = MagicMock()
    graph.get_service_principals_delta = _boom
    graph.last_delta_link = None

    collector = SPOrphansCollector(graph=graph, store=_make_store_mock())
    with pytest.raises(RuntimeError):
        await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_collections_total", [])
    dp = _dp_by_attrs(dps, tenant="t1", status="error")
    assert dp is not None
    assert dp.value == 1  # type: ignore[union-attr]


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collections_counter_emitted_on_error_not_success(
    mock_snap, mock_risk, metric_reader: InMemoryMetricReader
) -> None:
    """Error path emits status=error, NOT status=success."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("fail")
        yield

    graph = MagicMock()
    graph.get_service_principals_delta = _boom
    graph.last_delta_link = None

    collector = SPOrphansCollector(graph=graph, store=_make_store_mock())
    with pytest.raises(RuntimeError):
        await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_collections_total", [])
    assert not any(dp.attributes.get("status") == "success" for dp in dps)


# ── m365_collection_duration_seconds ──────────────────────────────────────────


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_duration_histogram_emitted(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_collection_duration_seconds", [])
    assert len(dps) >= 1
    dp = dps[0]
    assert dp.count == 1  # type: ignore[union-attr]
    assert dp.sum >= 0  # type: ignore[union-attr]


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_duration_histogram_emitted_on_error(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    """Duration is recorded even when collection fails (try/finally)."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("fail")
        yield

    graph = MagicMock()
    graph.get_service_principals_delta = _boom
    graph.last_delta_link = None

    collector = SPOrphansCollector(graph=graph, store=_make_store_mock())
    with pytest.raises(RuntimeError):
        await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_collection_duration_seconds", [])
    assert len(dps) >= 1


# ── m365_sps_risk_count + m365_sps_orphans_actionable ─────────────────────────


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_risk_count_gauge_all_levels(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP, _CRITICAL_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_sps_risk_count", [])
    levels = {dp.attributes.get("risk_level") for dp in dps if dp.attributes.get("tenant") == "t1"}
    assert {"critical", "high", "medium", "low"}.issubset(levels)


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_risk_count_critical_value(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_CRITICAL_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_sps_risk_count", [])
    dp = _dp_by_attrs(dps, tenant="t1", risk_level="critical")
    assert dp is not None
    assert dp.value == 1  # type: ignore[union-attr]


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_orphans_actionable_gauge(mock_snap, mock_risk, metric_reader: InMemoryMetricReader) -> None:
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP, _CRITICAL_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("t1")

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_sps_orphans_actionable", [])
    dp = _dp_by_attrs(dps, tenant="t1")
    assert dp is not None
    # critical=1 + high=0 for this dataset
    assert dp.value >= 1  # type: ignore[union-attr]
