"""OTel span tests for graph_client — verifies span names and attributes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import itgov.observability.setup as obs_setup
from itgov.services.graph_client import (
    GraphAuthError,
    GraphClient,
    GraphRateLimitError,
    _fetch_token,
    _url_host,
    _url_path,
)

pytestmark = pytest.mark.asyncio

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def span_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    orig_provider = otel_trace._TRACER_PROVIDER  # type: ignore[attr-defined]
    orig_done = otel_trace._TRACER_PROVIDER_SET_ONCE._done  # type: ignore[attr-defined]
    orig_module = obs_setup._provider

    otel_trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    otel_trace.set_tracer_provider(provider)
    obs_setup._provider = None

    yield exporter

    exporter.clear()
    otel_trace._TRACER_PROVIDER = orig_provider  # type: ignore[attr-defined]
    otel_trace._TRACER_PROVIDER_SET_ONCE._done = orig_done  # type: ignore[attr-defined]
    obs_setup._provider = orig_module


def _spans_by_name(exporter: InMemorySpanExporter, name: str):
    return [s for s in exporter.get_finished_spans() if s.name == name]


# ── _url_path / _url_host helpers (sync — no asyncio mark needed) ─────────────


class TestUrlHelpers:
    def test_url_path_strips_query_string(self):
        url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=abc"
        assert _url_path(url) == "/v1.0/servicePrincipals/delta"

    def test_url_host_returns_hostname(self):
        url = "https://graph.microsoft.com/v1.0/servicePrincipals"
        assert _url_host(url) == "graph.microsoft.com"

    def test_url_host_fallback_on_relative(self):
        assert _url_host("/relative/path") == "graph.microsoft.com"


# ── _fetch_token spans ────────────────────────────────────────────────────────


@patch("itgov.services.graph_client._get_credentials", return_value=("t-id", "c-id", "c-secret"))
async def test_fetch_token_emits_span(mock_creds, span_exporter: InMemorySpanExporter):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "tok123"}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_resp

    await _fetch_token(mock_client)

    spans = _spans_by_name(span_exporter, "graph.auth.token_fetch")
    assert len(spans) == 1


@patch("itgov.services.graph_client._get_credentials", return_value=("t-id", "c-id", "c-secret"))
async def test_fetch_token_span_attributes(mock_creds, span_exporter: InMemorySpanExporter):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "tok"}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_resp

    await _fetch_token(mock_client)

    span = _spans_by_name(span_exporter, "graph.auth.token_fetch")[0]
    attrs = span.attributes or {}
    assert attrs.get("graph.auth.method") == "client_credentials"
    assert attrs.get("graph.tenant_id") == "t-id"
    assert attrs.get("http.request.method") == "POST"
    assert attrs.get("server.address") == "login.microsoftonline.com"
    assert attrs.get("http.response.status_code") == 200


@patch("itgov.services.graph_client._get_credentials", return_value=("t-id", "c-id", "c-secret"))
async def test_fetch_token_error_sets_span_status(mock_creds, span_exporter: InMemorySpanExporter):
    mock_resp = MagicMock()
    mock_resp.status_code = 401

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_resp

    with pytest.raises(GraphAuthError):
        await _fetch_token(mock_client)

    span = _spans_by_name(span_exporter, "graph.auth.token_fetch")[0]
    attrs = span.attributes or {}
    assert attrs.get("graph.error.type") == "auth"
    assert attrs.get("http.response.status_code") == 401
    from opentelemetry.trace import StatusCode

    assert span.status.status_code == StatusCode.ERROR


# ── get_service_principals_delta spans ────────────────────────────────────────


def _make_response(items: list, *, next_link: str = "", delta_link: str = "") -> dict:
    resp: dict = {"value": items}
    if next_link:
        resp["@odata.nextLink"] = next_link
    if delta_link:
        resp["@odata.deltaLink"] = delta_link
    return resp


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch("itgov.services.graph_client._get", new_callable=AsyncMock)
async def test_graph_api_get_span_emitted_per_page(mock_get, mock_token, span_exporter: InMemorySpanExporter):
    page1_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta"
    page2_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$skiptoken=p2"
    delta_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=end"

    mock_get.side_effect = [
        _make_response([{"id": "sp1"}], next_link=page2_url),
        _make_response([{"id": "sp2"}], delta_link=delta_url),
    ]

    client = GraphClient()
    sps = [sp async for sp in client.get_service_principals_delta("tenant-x", delta_link=page1_url)]

    assert len(sps) == 2
    spans = _spans_by_name(span_exporter, "graph.api.get")
    assert len(spans) == 2


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch("itgov.services.graph_client._get", new_callable=AsyncMock)
async def test_graph_api_get_span_attributes_page1(mock_get, mock_token, span_exporter: InMemorySpanExporter):
    page_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$select=id"
    delta_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=end"

    mock_get.return_value = _make_response([{"id": "sp1"}, {"id": "sp2"}], delta_link=delta_url)

    client = GraphClient()
    _ = [sp async for sp in client.get_service_principals_delta("tenant-y", delta_link=page_url)]

    span = _spans_by_name(span_exporter, "graph.api.get")[0]
    attrs = span.attributes or {}
    assert attrs.get("http.request.method") == "GET"
    assert attrs.get("server.address") == "graph.microsoft.com"
    assert attrs.get("url.path") == "/v1.0/servicePrincipals/delta"
    assert attrs.get("graph.page_number") == 1
    assert attrs.get("http.response.status_code") == 200
    assert attrs.get("graph.has_next_page") is False
    assert attrs.get("graph.delta_link_present") is True


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch("itgov.services.graph_client._get", new_callable=AsyncMock)
async def test_graph_api_get_has_next_page_true(mock_get, mock_token, span_exporter: InMemorySpanExporter):
    next_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$skiptoken=more"
    delta_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=end"

    mock_get.side_effect = [
        _make_response([{"id": "sp1"}], next_link=next_url),
        _make_response([], delta_link=delta_url),
    ]

    client = GraphClient()
    _ = [sp async for sp in client.get_service_principals_delta("tenant-z", delta_link=next_url)]

    spans = _spans_by_name(span_exporter, "graph.api.get")
    assert spans[0].attributes and spans[0].attributes.get("graph.has_next_page") is True
    assert spans[1].attributes and spans[1].attributes.get("graph.has_next_page") is False


@patch("itgov.services.graph_client._fetch_token", new_callable=AsyncMock, return_value="tok")
@patch(
    "itgov.services.graph_client._get",
    new_callable=AsyncMock,
    side_effect=GraphRateLimitError(retry_after=30),
)
async def test_graph_api_get_rate_limit_span(mock_get, mock_token, span_exporter: InMemorySpanExporter):
    page_url = "https://graph.microsoft.com/v1.0/servicePrincipals/delta"

    client = GraphClient()
    with pytest.raises(GraphRateLimitError):
        _ = [sp async for sp in client.get_service_principals_delta("t", delta_link=page_url)]

    span = _spans_by_name(span_exporter, "graph.api.get")[0]
    attrs = span.attributes or {}
    assert attrs.get("graph.error.type") == "rate_limit"
    assert attrs.get("graph.retry_after_seconds") == 30
    from opentelemetry.trace import StatusCode

    assert span.status.status_code == StatusCode.ERROR
