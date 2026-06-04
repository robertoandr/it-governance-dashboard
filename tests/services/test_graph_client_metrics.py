"""Metric emission tests for graph_client — verifies counters and histograms."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import opentelemetry.metrics._internal as _otel_m
import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import itgov.observability.metrics as obs_metrics
from itgov.services.graph_client import (
    GraphAuthError,
    GraphClient,
    GraphRateLimitError,
    _fetch_token,
)

pytestmark = pytest.mark.asyncio

# ── fixtures ──────────────────────────────────────────────────────────────────


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


# ── _fetch_token metrics ───────────────────────────────────────────────────────


@patch("itgov.services.graph_client._get_credentials", return_value=("t1", "c", "s"))
async def test_fetch_token_auth_error_increments_errors(mock_creds, metric_reader: InMemoryMetricReader) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_resp

    with pytest.raises(GraphAuthError):
        await _fetch_token(mock_client)

    metrics = _collect(metric_reader)
    assert "m365_graph_errors_total" in metrics
    dp = _dp_by_attrs(metrics["m365_graph_errors_total"], tenant="t1", error_type="auth")
    assert dp is not None
    assert dp.value == 1  # type: ignore[union-attr]


@patch("itgov.services.graph_client._get_credentials", return_value=("t1", "c", "s"))
async def test_fetch_token_success_emits_no_error(mock_creds, metric_reader: InMemoryMetricReader) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "tok"}
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_resp

    await _fetch_token(mock_client)

    metrics = _collect(metric_reader)
    error_dps = metrics.get("m365_graph_errors_total", [])
    assert not any(dp.attributes.get("error_type") == "auth" for dp in error_dps)


# ── get_service_principals_delta metrics ──────────────────────────────────────


def _make_resp(items: list, *, next_link: str = "", delta_link: str = "") -> dict:
    r: dict = {"value": items}
    if next_link:
        r["@odata.nextLink"] = next_link
    if delta_link:
        r["@odata.deltaLink"] = delta_link
    return r


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch("itgov.services.graph_client._get", new_callable=AsyncMock)
async def test_requests_counter_increments_per_page(mock_get, mock_token, metric_reader: InMemoryMetricReader) -> None:
    delta_url = "https://graph.microsoft.com/v1.0/sps/delta?$deltatoken=end"
    page2 = "https://graph.microsoft.com/v1.0/sps/delta?$skiptoken=p2"
    mock_get.side_effect = [
        _make_resp([{"id": "s1"}], next_link=page2),
        _make_resp([{"id": "s2"}], delta_link=delta_url),
    ]

    start = "https://graph.microsoft.com/v1.0/sps/delta"
    client = GraphClient()
    _ = [s async for s in client.get_service_principals_delta("ten1", delta_link=start)]

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_graph_requests_total", [])
    ok_dps = [dp for dp in dps if dp.attributes.get("status_code") == "200"]
    total = sum(dp.value for dp in ok_dps)
    assert total == 2  # 2 pages


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch("itgov.services.graph_client._get", new_callable=AsyncMock)
async def test_requests_counter_includes_tenant_and_endpoint(
    mock_get, mock_token, metric_reader: InMemoryMetricReader
) -> None:
    delta_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=end"
    url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta"
    mock_get.return_value = _make_resp([{"id": "s1"}], delta_link=delta_url)

    client = GraphClient()
    _ = [s async for s in client.get_service_principals_delta("my-tenant", delta_link=url)]

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_graph_requests_total", [])
    dp = _dp_by_attrs(
        dps,
        tenant="my-tenant",
        endpoint="/v1.0/servicePrincipals/delta",
        status_code="200",
    )
    assert dp is not None
    assert dp.value == 1  # type: ignore[union-attr]


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch("itgov.services.graph_client._get", new_callable=AsyncMock)
async def test_duration_histogram_recorded(mock_get, mock_token, metric_reader: InMemoryMetricReader) -> None:
    delta_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=end"
    url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta"
    mock_get.return_value = _make_resp([{"id": "s1"}], delta_link=delta_url)

    client = GraphClient()
    _ = [s async for s in client.get_service_principals_delta("ten1", delta_link=url)]

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_graph_request_duration_seconds", [])
    assert len(dps) >= 1
    dp = dps[0]
    assert dp.count == 1
    assert dp.sum >= 0


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch(
    "itgov.services.graph_client._get",
    new_callable=AsyncMock,
    side_effect=GraphRateLimitError(retry_after=30),
)
async def test_rate_limit_increments_error_counter(mock_get, mock_token, metric_reader: InMemoryMetricReader) -> None:
    url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta"
    client = GraphClient()
    with pytest.raises(GraphRateLimitError):
        _ = [s async for s in client.get_service_principals_delta("ten1", delta_link=url)]

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_graph_errors_total", [])
    dp = _dp_by_attrs(dps, tenant="ten1", error_type="rate_limit")
    assert dp is not None
    assert dp.value >= 1  # type: ignore[union-attr]


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch(
    "itgov.services.graph_client._get",
    new_callable=AsyncMock,
    side_effect=GraphRateLimitError(retry_after=30),
)
async def test_rate_limit_request_counter_429(mock_get, mock_token, metric_reader: InMemoryMetricReader) -> None:
    url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta"
    client = GraphClient()
    with pytest.raises(GraphRateLimitError):
        _ = [s async for s in client.get_service_principals_delta("ten1", delta_link=url)]

    metrics = _collect(metric_reader)
    dps = metrics.get("m365_graph_requests_total", [])
    dp = _dp_by_attrs(dps, status_code="429")
    assert dp is not None
    assert dp.value >= 1  # type: ignore[union-attr]
