"""OTel span tests for sp_orphans_collector — verifies span names and attributes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import itgov.observability.setup as obs_setup
from itgov.services.sp_orphans_collector import SPOrphansCollector, run_collection

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
    "appRoles": [{"value": "Application.ReadWrite.All"}, {"value": "Directory.ReadWrite.All"}],
    "passwordCredentials": [],
    "keyCredentials": [],
    "signInActivity": None,
    "createdDateTime": "2020-01-01T00:00:00Z",
}


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


# ── m365.sp_orphans.collect ───────────────────────────────────────────────────


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collect_emits_span(mock_snap, mock_risk, span_exporter: InMemorySpanExporter):
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("tenant-abc")

    spans = _spans_by_name(span_exporter, "m365.sp_orphans.collect")
    assert len(spans) == 1


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collect_span_tenant_and_delta_attrs(mock_snap, mock_risk, span_exporter: InMemorySpanExporter):
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP]),
        store=_make_store_mock(existing_token="prev-token"),
    )
    await collector.collect("tenant-xyz")

    span = _spans_by_name(span_exporter, "m365.sp_orphans.collect")[0]
    attrs = span.attributes or {}
    assert attrs.get("m365.tenant_id") == "tenant-xyz"
    assert attrs.get("m365.delta.token_used") is True


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collect_span_first_run_delta_false(mock_snap, mock_risk, span_exporter: InMemorySpanExporter):
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP]),
        store=_make_store_mock(existing_token=None),
    )
    await collector.collect("tenant-new")

    span = _spans_by_name(span_exporter, "m365.sp_orphans.collect")[0]
    assert (span.attributes or {}).get("m365.delta.token_used") is False


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collect_span_counter_attributes(mock_snap, mock_risk, span_exporter: InMemorySpanExporter):
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP, _CRITICAL_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("tenant-counts")

    span = _spans_by_name(span_exporter, "m365.sp_orphans.collect")[0]
    attrs = span.attributes or {}
    assert attrs.get("m365.sps.total_scanned") == 2
    # _CRITICAL_SP has critical permissions + never signed in → CRITICAL
    assert attrs.get("m365.sps.risk_critical") == 1
    # _SAMPLE_SP with old sign-in date → non-critical (OK or MEDIUM/HIGH)
    assert attrs.get("m365.sps.total_scanned") == 2
    # orphans_actionable = critical + high
    actionable = (attrs.get("m365.sps.risk_critical") or 0) + (attrs.get("m365.sps.risk_high") or 0)
    assert attrs.get("m365.sps.orphans_actionable") == actionable


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collect_span_duration_ms_present(mock_snap, mock_risk, span_exporter: InMemorySpanExporter):
    collector = SPOrphansCollector(
        graph=_make_graph_mock([_SAMPLE_SP]),
        store=_make_store_mock(),
    )
    await collector.collect("tenant-dur")

    span = _spans_by_name(span_exporter, "m365.sp_orphans.collect")[0]
    duration_ms = (span.attributes or {}).get("m365.duration_ms")
    assert isinstance(duration_ms, int)
    assert duration_ms >= 0


@patch("itgov.services.sp_orphans_collector.write_sp_risk")
@patch("itgov.services.sp_orphans_collector.write_summary_snapshot")
async def test_collect_span_error_on_exception(mock_snap, mock_risk, span_exporter: InMemorySpanExporter):
    async def _boom(*args, **kwargs):
        raise RuntimeError("graph exploded")
        yield  # makes it an async generator

    graph = MagicMock()
    graph.get_service_principals_delta = _boom
    graph.last_delta_link = None

    collector = SPOrphansCollector(graph=graph, store=_make_store_mock())

    with pytest.raises(RuntimeError):
        await collector.collect("tenant-err")

    span = _spans_by_name(span_exporter, "m365.sp_orphans.collect")[0]
    assert span.status.status_code == StatusCode.ERROR


# ── m365.run_collection ───────────────────────────────────────────────────────


@patch("itgov.services.sp_orphans_collector.SPOrphansCollector")
async def test_run_collection_emits_root_span(mock_cls: MagicMock, span_exporter: InMemorySpanExporter):
    from itgov.services.sp_orphans_collector import CollectionResult

    mock_instance = AsyncMock()
    mock_instance.collect.return_value = CollectionResult(
        mode="full", count=5, tenant_id="t", critical=1, high=2, medium=1, ok=1
    )
    mock_cls.return_value = mock_instance

    await run_collection("tenant-root")

    spans = _spans_by_name(span_exporter, "m365.run_collection")
    assert len(spans) == 1


@patch("itgov.services.sp_orphans_collector.SPOrphansCollector")
async def test_run_collection_span_attributes(mock_cls: MagicMock, span_exporter: InMemorySpanExporter):
    from itgov.services.sp_orphans_collector import CollectionResult

    mock_instance = AsyncMock()
    mock_instance.collect.return_value = CollectionResult(
        mode="full", count=3, tenant_id="t", critical=0, high=1, medium=1, ok=1
    )
    mock_cls.return_value = mock_instance

    await run_collection("my-tenant")

    span = _spans_by_name(span_exporter, "m365.run_collection")[0]
    attrs = span.attributes or {}
    assert attrs.get("m365.tenant_id") == "my-tenant"
    assert attrs.get("m365.collector") == "sp_orphans"


@patch("itgov.services.sp_orphans_collector.SPOrphansCollector")
async def test_run_collection_is_parent_of_collect(mock_cls: MagicMock, span_exporter: InMemorySpanExporter):
    """m365.run_collection must be the parent span of m365.sp_orphans.collect."""

    # Use real SPOrphansCollector with mocked deps so both spans fire
    mock_cls.side_effect = None  # reset side_effect from previous test
    mock_cls.return_value = mock_cls  # we'll patch SPOrphansCollector directly below

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
    ):
        real_collector = SPOrphansCollector(
            graph=_make_graph_mock([_SAMPLE_SP]),
            store=_make_store_mock(),
        )
        mock_cls.return_value = real_collector
        await run_collection("tenant-tree")

    root_spans = _spans_by_name(span_exporter, "m365.run_collection")
    child_spans = _spans_by_name(span_exporter, "m365.sp_orphans.collect")
    assert len(root_spans) == 1
    assert len(child_spans) == 1
    assert child_spans[0].parent is not None
    assert child_spans[0].parent.span_id == root_spans[0].context.span_id
