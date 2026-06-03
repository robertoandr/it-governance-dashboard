"""Tests for SPOrphansCollector delta query integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from itgov.services.sp_orphans_collector import (
    SPOrphansCollector,
    _days_since,
    _extract_permissions,
    _has_expired_creds,
)

pytestmark = pytest.mark.asyncio

_SAMPLE_SP = {
    "id": "sp-object-id-1",
    "displayName": "TestApp",
    "servicePrincipalType": "Application",
    "appRoles": [],
    "passwordCredentials": [],
    "keyCredentials": [],
    "signInActivity": {"lastSignInDateTime": "2024-01-01T00:00:00Z"},
    "createdDateTime": "2023-01-01T00:00:00Z",
}

_DELTA_LINK_NEW = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=newtoken999"
_DELTA_LINK_OLD = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=oldtoken111"


def _make_graph_mock(sps: list[dict], delta_link: str = _DELTA_LINK_NEW):
    """Return a GraphClient mock that yields `sps` and sets last_delta_link."""

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


# ── mode detection ────────────────────────────────────────────────────────────


async def test_first_run_uses_full_mode(tmp_path):
    store = _make_store_mock(existing_token=None)
    graph = _make_graph_mock([_SAMPLE_SP])

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
    ):
        collector = SPOrphansCollector(graph=graph, store=store)
        result = await collector.collect("tenant-xyz")

    assert result.mode == "full"
    store.get.assert_awaited_once_with("servicePrincipals", "tenant-xyz")


async def test_subsequent_run_uses_delta_mode(tmp_path):
    store = _make_store_mock(existing_token=_DELTA_LINK_OLD)
    graph = _make_graph_mock([_SAMPLE_SP])

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
    ):
        collector = SPOrphansCollector(graph=graph, store=store)
        result = await collector.collect("tenant-xyz")

    assert result.mode == "delta"


# ── delta token persistence ───────────────────────────────────────────────────


async def test_token_persisted_after_successful_run():
    store = _make_store_mock(existing_token=None)
    graph = _make_graph_mock([_SAMPLE_SP], delta_link=_DELTA_LINK_NEW)

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
    ):
        collector = SPOrphansCollector(graph=graph, store=store)
        await collector.collect("tenant-xyz")

    store.set.assert_awaited_once_with("servicePrincipals", "tenant-xyz", _DELTA_LINK_NEW)


async def test_previous_token_preserved_on_failure():
    store = _make_store_mock(existing_token=_DELTA_LINK_OLD)

    async def _failing_iter(*args, **kwargs):
        yield _SAMPLE_SP
        raise RuntimeError("Graph API error mid-stream")

    graph = MagicMock()
    graph.get_service_principals_delta = _failing_iter
    graph.last_delta_link = None

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
        pytest.raises(RuntimeError),
    ):
        collector = SPOrphansCollector(graph=graph, store=store)
        await collector.collect("tenant-xyz")

    # set must NOT have been called — old token must remain intact
    store.set.assert_not_awaited()


async def test_clear_forces_next_run_to_full(tmp_path):
    store = _make_store_mock(existing_token=_DELTA_LINK_OLD)

    # Simulate a manual clear
    await store.clear("servicePrincipals", "tenant-xyz")
    store.get.return_value = None  # cleared

    graph = _make_graph_mock([_SAMPLE_SP])

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
    ):
        collector = SPOrphansCollector(graph=graph, store=store)
        result = await collector.collect("tenant-xyz")

    assert result.mode == "full"


# ── result statistics ─────────────────────────────────────────────────────────


async def test_collection_result_count_matches_sps():
    sps = [dict(_SAMPLE_SP, id=f"sp-{i}") for i in range(5)]
    store = _make_store_mock()
    graph = _make_graph_mock(sps)

    with (
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
    ):
        collector = SPOrphansCollector(graph=graph, store=store)
        result = await collector.collect("tenant-xyz")

    assert result.count == 5
    assert result.critical + result.high + result.medium + result.ok == 5


# ── helper unit tests (for coverage of parsing functions) ────────────────────


def test_days_since_returns_none_for_none():
    assert _days_since(None) is None


def test_days_since_returns_none_for_empty_string():
    assert _days_since("") is None


def test_days_since_returns_none_for_invalid_date():
    assert _days_since("not-a-date") is None


def test_days_since_returns_correct_days():
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    result = _days_since(past)
    assert result is not None
    assert 9 <= result <= 11  # allow 1 day drift for CI timing


def test_days_since_handles_Z_suffix():
    past = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = _days_since(past)
    assert result is not None
    assert 4 <= result <= 6


def test_has_expired_creds_true_for_expired_password():
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat() + "Z"
    sp = {"passwordCredentials": [{"endDateTime": past}], "keyCredentials": []}
    assert _has_expired_creds(sp) is True


def test_has_expired_creds_false_for_valid_password():
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat() + "Z"
    sp = {"passwordCredentials": [{"endDateTime": future}], "keyCredentials": []}
    assert _has_expired_creds(sp) is False


def test_has_expired_creds_false_when_no_creds():
    sp = {"passwordCredentials": [], "keyCredentials": []}
    assert _has_expired_creds(sp) is False


def test_has_expired_creds_skips_missing_end_date():
    sp = {"passwordCredentials": [{"endDateTime": None}], "keyCredentials": []}
    assert _has_expired_creds(sp) is False


def test_has_expired_creds_handles_invalid_date():
    sp = {"passwordCredentials": [{"endDateTime": "bad-date"}], "keyCredentials": []}
    assert _has_expired_creds(sp) is False


def test_extract_permissions_returns_values():
    sp = {"appRoles": [{"value": "User.Read"}, {"value": "Mail.Read"}]}
    assert _extract_permissions(sp) == ["User.Read", "Mail.Read"]


def test_extract_permissions_skips_empty_values():
    sp = {"appRoles": [{"value": ""}, {"value": "User.Read"}]}
    result = _extract_permissions(sp)
    assert result == ["User.Read"]


def test_extract_permissions_returns_empty_for_no_roles():
    sp = {"appRoles": []}
    assert _extract_permissions(sp) == []
