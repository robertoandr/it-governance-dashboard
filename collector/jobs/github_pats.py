"""GitHub PAT inventory collector — writes gov_github_pat to InfluxDB governance_raw.

Schema:
  measurement: gov_github_pat
  tags:        org=<github_org>, available=true|false
  fields:      total, with_expiration, no_expiration,
               expiring_7d, expiring_30d
  timestamp:   collection time (now)

Note: fine-grained PAT listing requires GitHub org with PAT policy enabled.
Personal accounts and orgs without the policy return 404 → fields written as 0
with tag available=false so the dashboard reflects the gap explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from config import settings

log = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"


def _expiring_within(expires_at_str: str | None, days: int) -> bool:
    if not expires_at_str:
        return False
    try:
        exp = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        delta = exp - datetime.now(UTC)
        return 0 <= delta.days <= days
    except (ValueError, TypeError):
        return False


def _fetch_org_pats(org: str, token: str) -> tuple[list[dict], bool]:
    """Fetch fine-grained PATs for a GitHub org.

    Returns (pats, available). available=False when org doesn't support the API.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{_GITHUB_API}/orgs/{org}/personal-access-tokens"
    pats: list[dict] = []

    try:
        with httpx.Client(timeout=30) as client:
            while url:
                resp = client.get(url, headers=headers)
                if resp.status_code in (404, 403):
                    log.warning(
                        "github_pat_api_unavailable",
                        org=org,
                        status=resp.status_code,
                        reason="org_policy_not_enabled_or_personal_account",
                    )
                    return [], False
                resp.raise_for_status()
                pats.extend(resp.json())
                url = resp.links.get("next", {}).get("url")
    except httpx.RequestError as exc:
        log.error("github_pat_request_error", org=org, error=str(exc))
        return [], False

    return pats, True


def _build_point(org: str, pats: list[dict], available: bool) -> Point:
    now = datetime.now(UTC)
    total = len(pats)
    with_exp = sum(1 for p in pats if p.get("expires_at"))
    no_exp = total - with_exp
    exp_7d = sum(1 for p in pats if _expiring_within(p.get("expires_at"), 7))
    exp_30d = sum(1 for p in pats if _expiring_within(p.get("expires_at"), 30))

    return (
        Point("gov_github_pat")
        .tag("org", org)
        .tag("available", str(available).lower())
        .field("total", total)
        .field("with_expiration", with_exp)
        .field("no_expiration", no_exp)
        .field("expiring_7d", exp_7d)
        .field("expiring_30d", exp_30d)
        .time(now, WritePrecision.S)
    )


def _write_point(point: Point) -> None:
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
            record=[point],
        )
    finally:
        client.close()


def collect_github_pats() -> None:
    """Collect PAT inventory for the configured GitHub org and write to InfluxDB."""
    org = settings.GITHUB_ORG
    log.info("github_pat_collector_start", org=org)

    pats, available = _fetch_org_pats(org, settings.GITHUB_TOKEN)

    point = _build_point(org, pats, available)
    _write_point(point)

    log.info(
        "github_pat_collector_done",
        org=org,
        total=len(pats),
        available=available,
    )
