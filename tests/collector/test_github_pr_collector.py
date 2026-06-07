"""Tests for jobs/github_pr_collector.py."""

from __future__ import annotations

# Patch config before importing the module under test
import sys
import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_config = types.ModuleType("config")
_mock_settings = MagicMock()
_mock_settings.GITHUB_TOKEN = "tok"
_mock_settings.GITHUB_ORG = "testorg"
_mock_settings.GITHUB_REPOS = '["testorg/repo"]'
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_mock_config.settings = _mock_settings
sys.modules.setdefault("config", _mock_config)

from collector.jobs.github_pr_collector import (  # noqa: E402
    GitHubPR,
    _write_points,
    pr_to_point,
    run,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_pr(**kwargs) -> GitHubPR:
    defaults = {
        "number": 1,
        "state": "closed",
        "created_at": datetime(2026, 5, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 2, tzinfo=UTC),
        "merged_at": datetime(2026, 5, 2, tzinfo=UTC),
        "title": "Test PR",
    }
    defaults.update(kwargs)
    return GitHubPR(**defaults)


# ── GitHubPR model ───────────────────────────────────────────────────────────


class TestGitHubPRModel:
    def test_resolved_state_merged(self) -> None:
        pr = _make_pr(merged_at=datetime(2026, 5, 2, tzinfo=UTC))
        assert pr.resolved_state == "merged"

    def test_resolved_state_closed(self) -> None:
        pr = _make_pr(state="closed", merged_at=None)
        assert pr.resolved_state == "closed"

    def test_resolved_state_open(self) -> None:
        pr = _make_pr(state="open", merged_at=None)
        assert pr.resolved_state == "open"

    def test_time_to_merge_seconds(self) -> None:
        pr = _make_pr(
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
            merged_at=datetime(2026, 5, 2, tzinfo=UTC),
        )
        assert pr.time_to_merge_seconds == pytest.approx(86400.0)

    def test_time_to_merge_none_when_not_merged(self) -> None:
        pr = _make_pr(state="open", merged_at=None)
        assert pr.time_to_merge_seconds is None

    def test_reference_timestamp_is_merged_at(self) -> None:
        merged = datetime(2026, 5, 2, tzinfo=UTC)
        pr = _make_pr(merged_at=merged)
        assert pr.reference_timestamp == merged

    def test_reference_timestamp_is_updated_at_when_not_merged(self) -> None:
        updated = datetime(2026, 5, 3, tzinfo=UTC)
        pr = _make_pr(state="open", merged_at=None, updated_at=updated)
        assert pr.reference_timestamp == updated


# ── pr_to_point ───────────────────────────────────────────────────────────────


class TestPrToPoint:
    def test_merged_pr_has_state_tag(self) -> None:
        pr = _make_pr()
        point = pr_to_point("org/repo", pr)
        lp = point.to_line_protocol()
        assert "state=merged" in lp

    def test_merged_pr_has_time_to_merge_field(self) -> None:
        pr = _make_pr()
        point = pr_to_point("org/repo", pr)
        lp = point.to_line_protocol()
        assert "time_to_merge_seconds=" in lp

    def test_open_pr_has_no_time_to_merge(self) -> None:
        pr = _make_pr(state="open", merged_at=None)
        point = pr_to_point("org/repo", pr)
        lp = point.to_line_protocol()
        assert "time_to_merge_seconds" not in lp

    def test_point_has_count_field(self) -> None:
        pr = _make_pr()
        point = pr_to_point("org/repo", pr)
        lp = point.to_line_protocol()
        assert "count=" in lp

    def test_point_measurement_is_gov_github_pr(self) -> None:
        pr = _make_pr()
        point = pr_to_point("org/repo", pr)
        lp = point.to_line_protocol()
        assert lp.startswith("gov_github_pr,")


# ── _write_points ─────────────────────────────────────────────────────────────


class TestWritePoints:
    def test_calls_influxdb_write_api(self) -> None:
        mock_client = MagicMock()
        mock_write_api = MagicMock()
        mock_client.write_api.return_value = mock_write_api

        with patch("collector.jobs.github_pr_collector.InfluxDBClient", return_value=mock_client):
            _write_points([MagicMock()])

        mock_write_api.write.assert_called_once()
        mock_client.close.assert_called_once()


# ── run() entry point ──────────────────────────────────────────────────────────


class TestRun:
    def test_run_calls_async_collect(self) -> None:
        with (
            patch(
                "collector.jobs.github_pr_collector._collect_repo", new_callable=AsyncMock, return_value=5
            ) as mock_collect,
            patch("collector.jobs.github_pr_collector._write_points"),
        ):
            run()

        mock_collect.assert_called_once()
