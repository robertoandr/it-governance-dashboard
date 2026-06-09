"""Tests for collector/jobs/gitleaks_scan.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import config as _cfg

# Patch config before importing the module under test
_mock_settings = MagicMock()
_mock_settings.GITHUB_TOKEN = "tok"
_mock_settings.GITHUB_ORG = "testorg"
_mock_settings.GITHUB_REPOS = '["testorg/repo-a"]'
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.gitleaks_scan import (  # noqa: E402
    _alert_to_severity,
    _build_github_points,
    _count_severities,
    _severity_from_rule,
    run_gitleaks_scan,
)

# ── severity helpers ────────────────────────────────────────────────────────


def test_severity_from_rule_critical():
    assert _severity_from_rule("github-pat") == "critical"
    assert _severity_from_rule("aws-access-key") == "critical"
    assert _severity_from_rule("private-key") == "critical"


def test_severity_from_rule_high():
    assert _severity_from_rule("generic-api-key") == "high"


def test_alert_to_severity_critical():
    alert = {"secret_type": "github_personal_access_token"}
    assert _alert_to_severity(alert) == "critical"


def test_alert_to_severity_high():
    alert = {"secret_type": "generic_api_key"}
    assert _alert_to_severity(alert) == "high"


# ── _count_severities ───────────────────────────────────────────────────────


def test_count_severities_mixed():
    alerts = [
        {"secret_type": "github_personal_access_token"},  # critical
        {"secret_type": "aws_access_key_id"},  # critical
        {"secret_type": "generic_api_key"},  # high
    ]
    counts = _count_severities(alerts)
    assert counts["critical"] == 2
    assert counts["high"] == 1
    assert counts["medium"] == 0
    assert counts["low"] == 0


def test_count_severities_empty():
    counts = _count_severities([])
    assert counts == {"critical": 0, "high": 0, "medium": 0, "low": 0}


# ── _build_github_points ────────────────────────────────────────────────────


def test_build_github_points_schema():
    alerts = [
        {
            "secret_type": "github_personal_access_token",
            "created_at": "2026-06-01T12:00:00Z",
        }
    ]
    points = _build_github_points("testorg/repo-a", alerts)
    assert len(points) == 1
    lp = points[0].to_line_protocol()
    assert "gov_gitleaks_finding" in lp
    assert "repo=testorg/repo-a" in lp
    assert "severity=critical" in lp
    assert "source=github_secret_scanning" in lp
    assert "count=1i" in lp


def test_build_github_points_empty():
    assert _build_github_points("testorg/repo-a", []) == []


# ── run_gitleaks_scan (integration) ─────────────────────────────────────────


def test_run_gitleaks_scan_no_alerts(monkeypatch):
    """When secret scanning returns no alerts, summary is written with zeros."""
    written = []

    monkeypatch.setattr(
        "collector.jobs.gitleaks_scan._fetch_secret_scanning_alerts",
        lambda repo, token: ([], False),
    )
    monkeypatch.setattr(
        "collector.jobs.gitleaks_scan._write_points",
        lambda pts: written.extend(pts),
    )

    run_gitleaks_scan()

    # Expect at least the summary point
    assert len(written) >= 1
    summary_lps = [p.to_line_protocol() for p in written if "gov_gitleaks_summary" in p.to_line_protocol()]
    assert len(summary_lps) == 1
    assert "total=0i" in summary_lps[0]
    assert "available=false" in summary_lps[0]


def test_run_gitleaks_scan_with_alerts(monkeypatch):
    """When alerts exist, one finding point + summary are written."""
    alerts = [{"secret_type": "github_pat", "created_at": "2026-06-01T00:00:00Z"}]
    written = []

    monkeypatch.setattr(
        "collector.jobs.gitleaks_scan._fetch_secret_scanning_alerts",
        lambda repo, token: (alerts, True),
    )
    monkeypatch.setattr(
        "collector.jobs.gitleaks_scan._write_points",
        lambda pts: written.extend(pts),
    )

    run_gitleaks_scan()

    finding_lps = [p.to_line_protocol() for p in written if "gov_gitleaks_finding" in p.to_line_protocol()]
    summary_lps = [p.to_line_protocol() for p in written if "gov_gitleaks_summary" in p.to_line_protocol()]

    assert len(finding_lps) == 1
    assert len(summary_lps) == 1
    assert "total=1i" in summary_lps[0]
    assert "available=true" in summary_lps[0]
