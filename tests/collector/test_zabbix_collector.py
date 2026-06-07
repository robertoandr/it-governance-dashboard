"""Tests for jobs/zabbix_collector.py."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# Mock config before importing the module under test
_mock_config = types.ModuleType("config")
_mock_settings = MagicMock()
_mock_settings.ZABBIX_URL = "http://zabbix.test/api_jsonrpc.php"
_mock_settings.ZABBIX_TOKEN = "test-token-abc"
_mock_settings.INFLUX_URL = "http://influxdb:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_mock_config.settings = _mock_settings
sys.modules.setdefault("config", _mock_config)

from collector.jobs.zabbix_collector import (  # noqa: E402
    _build_problem_points,
    _build_summary_point,
    _classify_group,
    collect_zabbix_ops,
)

_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)

# ── _classify_group ───────────────────────────────────────────────────────────


class TestClassifyGroup:
    def test_cftv_keyword_matches(self) -> None:
        assert _classify_group(["CFTV", "Outro"]) == "cftv"

    def test_dvr_matches_cftv(self) -> None:
        assert _classify_group(["DVR-1"]) == "cftv"

    def test_m365_matches(self) -> None:
        assert _classify_group(["Microsoft 365"]) == "m365"

    def test_365_shorthand_matches_m365(self) -> None:
        assert _classify_group(["Office 365 Users"]) == "m365"

    def test_unknown_group_returns_other(self) -> None:
        assert _classify_group(["Zabbix servers"]) == "other"

    def test_empty_groups_returns_other(self) -> None:
        assert _classify_group([]) == "other"

    def test_cftv_takes_priority_over_other(self) -> None:
        assert _classify_group(["CFTV", "Some Group"]) == "cftv"


# ── _build_problem_points ─────────────────────────────────────────────────────


class TestBuildProblemPoints:
    def test_returns_four_points_for_dashboard_severities(self) -> None:
        points = _build_problem_points([], available=True, now=_NOW)
        assert len(points) == 4

    def test_all_zero_when_no_problems(self) -> None:
        points = _build_problem_points([], available=True, now=_NOW)
        for p in points:
            lp = p.to_line_protocol()
            assert "count=0i" in lp

    def test_disaster_count_reflects_problems(self) -> None:
        problems = [{"severity": "5"}, {"severity": "5"}]
        points = _build_problem_points(problems, available=True, now=_NOW)
        disaster_pts = [p for p in points if "severity=disaster" in p.to_line_protocol()]
        assert len(disaster_pts) == 1
        assert "count=2i" in disaster_pts[0].to_line_protocol()

    def test_available_tag_propagated(self) -> None:
        points = _build_problem_points([], available=False, now=_NOW)
        for p in points:
            assert "available=false" in p.to_line_protocol()

    def test_measurement_name(self) -> None:
        points = _build_problem_points([], available=True, now=_NOW)
        for p in points:
            assert p.to_line_protocol().startswith("gov_zabbix_problem,")

    def test_mixed_severities_counted_correctly(self) -> None:
        problems = [
            {"severity": "5"},  # disaster
            {"severity": "4"},  # high
            {"severity": "4"},  # high
            {"severity": "2"},  # warning
        ]
        points = _build_problem_points(problems, available=True, now=_NOW)
        lp_map = {}
        for p in points:
            lp = p.to_line_protocol()
            for sev in ("disaster", "high", "average", "warning"):
                if f"severity={sev}" in lp:
                    lp_map[sev] = lp
        assert "count=1i" in lp_map["disaster"]
        assert "count=2i" in lp_map["high"]
        assert "count=0i" in lp_map["average"]
        assert "count=1i" in lp_map["warning"]


# ── _build_summary_point ──────────────────────────────────────────────────────


class TestBuildSummaryPoint:
    def test_measurement_name(self) -> None:
        p = _build_summary_point([], {}, available=True, now=_NOW)
        assert p.to_line_protocol().startswith("gov_zabbix_summary,")

    def test_zero_problems_total_when_empty(self) -> None:
        p = _build_summary_point([], {}, available=True, now=_NOW)
        assert "problems_total=0i" in p.to_line_protocol()

    def test_host_counts_from_group_map(self) -> None:
        host_group = {
            "h1": "cftv",
            "h2": "cftv",
            "h3": "m365",
            "h4": "other",
        }
        p = _build_summary_point([], host_group, available=True, now=_NOW)
        lp = p.to_line_protocol()
        assert "hosts_total=4i" in lp
        assert "hosts_cftv=2i" in lp
        assert "hosts_m365=1i" in lp

    def test_disaster_count_in_summary(self) -> None:
        problems = [{"severity": "5"}, {"severity": "5"}]
        p = _build_summary_point(problems, {}, available=True, now=_NOW)
        assert "problems_disaster=2i" in p.to_line_protocol()

    def test_available_false_tag(self) -> None:
        p = _build_summary_point([], {}, available=False, now=_NOW)
        assert "available=false" in p.to_line_protocol()


# ── collect_zabbix_ops (integration) ─────────────────────────────────────────


class TestCollectZabbixOps:
    def test_writes_points_on_success(self) -> None:
        mock_problems = [{"severity": "5"}, {"severity": "4"}]
        mock_hosts = [
            {"hostid": "1", "groups": [{"name": "CFTV"}]},
            {"hostid": "2", "groups": [{"name": "Microsoft 365"}]},
        ]

        def fake_rpc(method: str, params: dict):
            if method == "problem.get":
                return mock_problems
            if method == "host.get":
                return mock_hosts
            return []

        with (
            patch("collector.jobs.zabbix_collector._zabbix_rpc", side_effect=fake_rpc),
            patch("collector.jobs.zabbix_collector._write_points") as mock_write,
        ):
            collect_zabbix_ops()

        mock_write.assert_called_once()
        points = mock_write.call_args[0][0]
        # 4 severity points + 1 summary point
        assert len(points) == 5

    def test_writes_unavailable_on_rpc_error(self) -> None:
        with (
            patch(
                "collector.jobs.zabbix_collector._zabbix_rpc",
                side_effect=RuntimeError("connection refused"),
            ),
            patch("collector.jobs.zabbix_collector._write_points") as mock_write,
        ):
            collect_zabbix_ops()

        mock_write.assert_called_once()
        points = mock_write.call_args[0][0]
        for p in points:
            assert "available=false" in p.to_line_protocol()

    def test_no_write_skipped_when_token_missing(self) -> None:
        _mock_settings.ZABBIX_TOKEN = ""
        with (
            patch("collector.jobs.zabbix_collector._write_points") as mock_write,
        ):
            collect_zabbix_ops()
        _mock_settings.ZABBIX_TOKEN = "test-token-abc"

        mock_write.assert_called_once()
        points = mock_write.call_args[0][0]
        for p in points:
            assert "available=false" in p.to_line_protocol()
