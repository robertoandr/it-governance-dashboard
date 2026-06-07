"""Tests for collector/jobs/github_pats.py."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# Patch config before importing the module under test
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

from collector.jobs.github_pats import (  # noqa: E402
    _build_point,
    _expiring_within,
    collect_github_pats,
)

# ── _expiring_within ────────────────────────────────────────────────────────


def test_expiring_within_true():
    future = datetime(2026, 6, 10, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    with patch("collector.jobs.github_pats.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 7, tzinfo=UTC)
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat
        assert _expiring_within(future, 7) is True


def test_expiring_within_false_no_date():
    assert _expiring_within(None, 7) is False


def test_expiring_within_false_past():
    past = "2026-01-01T00:00:00Z"
    assert _expiring_within(past, 7) is False


# ── _build_point ────────────────────────────────────────────────────────────


def test_build_point_available():
    pats = [
        {"expires_at": None},
        {"expires_at": None},
    ]
    point = _build_point("myorg", pats, available=True)
    # Check field values by converting to line protocol
    lp = point.to_line_protocol()
    assert "gov_github_pat" in lp
    assert "available=true" in lp
    assert "total=2i" in lp
    assert "no_expiration=2i" in lp
    assert "with_expiration=0i" in lp


def test_build_point_unavailable():
    point = _build_point("myorg", [], available=False)
    lp = point.to_line_protocol()
    assert "available=false" in lp
    assert "total=0i" in lp


def test_build_point_org_tag():
    point = _build_point("acme", [], available=True)
    lp = point.to_line_protocol()
    assert "org=acme" in lp


# ── collect_github_pats ─────────────────────────────────────────────────────


def test_collect_github_pats_404(monkeypatch):
    """When org returns 404, writes zeros with available=false."""
    written = []

    def fake_fetch(org, token):
        return [], False

    def fake_write(point):
        written.append(point)

    monkeypatch.setattr("collector.jobs.github_pats._fetch_org_pats", fake_fetch)
    monkeypatch.setattr("collector.jobs.github_pats._write_point", fake_write)

    collect_github_pats()

    assert len(written) == 1
    lp = written[0].to_line_protocol()
    assert "available=false" in lp
    assert "total=0i" in lp


def test_collect_github_pats_with_data(monkeypatch):
    """When org returns PATs, writes correct counts."""
    pats = [
        {"expires_at": "2026-06-10T00:00:00Z"},
        {"expires_at": None},
        {"expires_at": None},
    ]
    written = []

    monkeypatch.setattr("collector.jobs.github_pats._fetch_org_pats", lambda o, t: (pats, True))
    monkeypatch.setattr("collector.jobs.github_pats._write_point", lambda p: written.append(p))

    collect_github_pats()

    lp = written[0].to_line_protocol()
    assert "available=true" in lp
    assert "total=3i" in lp
    assert "no_expiration=2i" in lp
    assert "with_expiration=1i" in lp
