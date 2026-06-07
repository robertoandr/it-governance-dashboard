"""Secret findings collector — writes gov_gitleaks_finding to InfluxDB governance_raw.

Data sources (in order of preference):
  1. GitHub Secret Scanning API (/repos/{owner}/{repo}/secret-scanning/alerts)
  2. Gitleaks binary scan of local /data/scan-targets (if binary present)

Schema:
  measurement: gov_gitleaks_finding
  tags:        repo, severity, source=github_secret_scanning|gitleaks
  fields:      count=1 (one point per finding)
  timestamp:   created_at of the finding (or scan time for gitleaks)

  measurement: gov_gitleaks_summary
  tags:        repo, source
  fields:      total, critical, high, medium, low
  timestamp:   scan time
"""

from __future__ import annotations

import json
import subprocess  # nosec B404
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from config import settings

log = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_GITLEAKS_TARGETS = ["/data/scan-targets"]

_SEVERITY_MAP: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _severity_from_rule(rule_id: str) -> str:
    rule_lower = rule_id.lower()
    critical_patterns = ["aws", "gcp", "azure", "github-pat", "private-key", "stripe"]
    if any(p in rule_lower for p in critical_patterns):
        return "critical"
    return "high"


def _fetch_secret_scanning_alerts(repo: str, token: str) -> tuple[list[dict], bool]:
    """Fetch GitHub Secret Scanning alerts for a repo.

    Returns (alerts, available). available=False when API is inaccessible.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{_GITHUB_API}/repos/{repo}/secret-scanning/alerts"
    alerts: list[dict] = []

    try:
        with httpx.Client(timeout=30) as client:
            params: dict[str, Any] = {"per_page": 100, "state": "open"}
            page = 1
            while url:
                params["page"] = page
                resp = client.get(url, headers=headers, params=params)
                if resp.status_code in (403, 404):
                    log.warning(
                        "secret_scanning_unavailable",
                        repo=repo,
                        status=resp.status_code,
                    )
                    return [], False
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                alerts.extend(batch)
                if "next" not in resp.links:
                    break
                page += 1
    except httpx.RequestError as exc:
        log.error("secret_scanning_request_error", repo=repo, error=str(exc))
        return [], False

    return alerts, True


def _alert_to_severity(alert: dict) -> str:
    secret_type = alert.get("secret_type", "").lower()
    critical_patterns = ["aws", "gcp", "azure", "github", "private", "stripe"]
    if any(p in secret_type for p in critical_patterns):
        return "critical"
    return "high"


def _build_github_points(repo: str, alerts: list[dict]) -> list[Point]:
    points: list[Point] = []
    for alert in alerts:
        severity = _alert_to_severity(alert)
        created_raw = alert.get("created_at", "")
        try:
            ts = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(UTC)

        points.append(
            Point("gov_gitleaks_finding")
            .tag("repo", repo)
            .tag("severity", severity)
            .tag("source", "github_secret_scanning")
            .field("count", 1)
            .time(ts, WritePrecision.S)
        )
    return points


def _run_gitleaks(target: str) -> list[dict]:
    """Run gitleaks binary against a local path. Returns findings list."""
    _, tmp_path = tempfile.mkstemp(suffix=".json", prefix="gitleaks-")
    output_file = Path(tmp_path)

    cmd = [
        "gitleaks",
        "detect",
        "--source",
        target,
        "--report-format",
        "json",
        "--report-path",
        str(output_file),
        "--no-git",
        "--exit-code",
        "0",
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)  # noqa: S603
    except FileNotFoundError:
        log.warning("gitleaks_not_installed")
        return []
    except subprocess.CalledProcessError as exc:
        log.error("gitleaks_failed", stderr=exc.stderr.decode(errors="replace"))
        return []

    if not output_file.exists():
        return []

    try:
        return json.loads(output_file.read_text())
    finally:
        output_file.unlink(missing_ok=True)


def _build_gitleaks_points(target: str, findings: list[dict]) -> list[Point]:
    now = datetime.now(UTC)
    points: list[Point] = []
    for f in findings:
        severity = _severity_from_rule(f.get("RuleID", ""))
        points.append(
            Point("gov_gitleaks_finding")
            .tag("repo", target)
            .tag("severity", severity)
            .tag("source", "gitleaks")
            .field("count", 1)
            .time(now, WritePrecision.S)
        )
    return points


def _write_points(points: list[Point]) -> None:
    if not points:
        return
    client = InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    )
    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(
            bucket=settings.INFLUX_BUCKET_RAW,
            org=settings.INFLUX_ORG,
            record=points,
        )
    finally:
        client.close()


def _count_severities(alerts: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for alert in alerts:
        sev = _alert_to_severity(alert)
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def _collect_github_secret_scanning(repos: list[str]) -> list[Point]:
    all_points: list[Point] = []
    for repo in repos:
        alerts, available = _fetch_secret_scanning_alerts(repo, settings.GITHUB_TOKEN)
        log.info(
            "secret_scanning_fetched",
            repo=repo,
            count=len(alerts),
            available=available,
        )
        counts = _count_severities(alerts)
        now = datetime.now(UTC)

        summary = (
            Point("gov_gitleaks_summary")
            .tag("repo", repo)
            .tag("source", "github_secret_scanning")
            .tag("available", str(available).lower())
            .field("total", len(alerts))
            .field("critical", counts["critical"])
            .field("high", counts["high"])
            .field("medium", counts["medium"])
            .field("low", counts["low"])
            .time(now, WritePrecision.S)
        )
        all_points.extend(_build_github_points(repo, alerts))
        all_points.append(summary)

    return all_points


def _collect_gitleaks_local() -> list[Point]:
    all_points: list[Point] = []
    for target in _GITLEAKS_TARGETS:
        if not Path(target).exists():
            log.debug("gitleaks_target_missing", target=target)
            continue

        findings = _run_gitleaks(target)
        log.info("gitleaks_scan_done", target=target, findings=len(findings))
        all_points.extend(_build_gitleaks_points(target, findings))

    return all_points


def run_gitleaks_scan() -> None:
    """Collect secret findings from GitHub Secret Scanning + gitleaks and write to InfluxDB."""
    repos: list[str] = []
    if settings.GITHUB_REPOS:
        try:
            repos = json.loads(settings.GITHUB_REPOS)
        except (json.JSONDecodeError, TypeError):
            repos = [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]
    if not repos and settings.GITHUB_ORG:
        repos = [f"{settings.GITHUB_ORG}/it-governance-dashboard"]

    log.info("gitleaks_collector_start", repos=repos)

    points: list[Point] = []
    points.extend(_collect_github_secret_scanning(repos))
    points.extend(_collect_gitleaks_local())

    _write_points(points)
    log.info("gitleaks_collector_done", total_points=len(points))
