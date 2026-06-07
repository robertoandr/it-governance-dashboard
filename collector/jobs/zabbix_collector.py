"""Zabbix Ops collector — writes gov_zabbix_problem and gov_zabbix_summary to InfluxDB.

Schema:
  gov_zabbix_problem:
    tags:   severity=disaster|high|average|warning, available=true|false
    fields: count (int — active problems of this severity)

  gov_zabbix_summary:
    tags:   available=true|false
    fields: problems_total, problems_disaster, problems_high,
            problems_average, problems_warning,
            hosts_total, hosts_cftv, hosts_m365
"""

from __future__ import annotations

from datetime import UTC, datetime

import requests
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from config import settings

log = structlog.get_logger(__name__)

_SEVERITY_LABELS: dict[str, str] = {
    "0": "not_classified",
    "1": "information",
    "2": "warning",
    "3": "average",
    "4": "high",
    "5": "disaster",
}

_DASHBOARD_SEVERITIES = ("disaster", "high", "average", "warning")

_CFTV_KEYWORDS = frozenset(["CFTV", "DVR", "NVR", "Loja", "Facial", "Câmera", "Camera", "Antena"])


def _classify_group(group_names: list[str]) -> str:
    for name in group_names:
        if any(kw in name for kw in _CFTV_KEYWORDS):
            return "cftv"
        if "365" in name or "Microsoft" in name:
            return "m365"
    return "other"


def _zabbix_rpc(method: str, params: dict) -> list | dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.ZABBIX_TOKEN}",
    }
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    resp = requests.post(settings.ZABBIX_URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix RPC error {data['error']['code']}: {data['error']['data']}")
    return data["result"]


def _fetch_problems() -> list[dict]:
    return _zabbix_rpc(
        "problem.get",
        {
            "output": ["eventid", "severity"],
            "recent": True,
            "suppressed": False,
        },
    )


def _fetch_host_group_map() -> dict[str, str]:
    """Returns {hostid: group_label} for all enabled hosts."""
    hosts = _zabbix_rpc(
        "host.get",
        {
            "output": ["hostid"],
            "filter": {"status": "0"},  # enabled only
            "selectGroups": ["groupid", "name"],
        },
    )
    mapping: dict[str, str] = {}
    for host in hosts:
        names = [g["name"] for g in host.get("groups", [])]
        mapping[host["hostid"]] = _classify_group(names)
    return mapping


def _build_problem_points(
    problems: list[dict],
    available: bool,
    now: datetime,
) -> list[Point]:
    sev_counts: dict[str, int] = dict.fromkeys(_DASHBOARD_SEVERITIES, 0)
    for p in problems:
        sev = _SEVERITY_LABELS.get(str(p.get("severity", "0")), "not_classified")
        if sev in sev_counts:
            sev_counts[sev] += 1

    return [
        Point("gov_zabbix_problem")
        .tag("severity", sev)
        .tag("available", str(available).lower())
        .field("count", sev_counts[sev])
        .time(now, WritePrecision.S)
        for sev in _DASHBOARD_SEVERITIES
    ]


def _build_summary_point(
    problems: list[dict],
    host_group: dict[str, str],
    available: bool,
    now: datetime,
) -> Point:
    sev_counts: dict[str, int] = dict.fromkeys(_DASHBOARD_SEVERITIES, 0)
    for p in problems:
        sev = _SEVERITY_LABELS.get(str(p.get("severity", "0")), "not_classified")
        if sev in sev_counts:
            sev_counts[sev] += 1

    group_counts = {"cftv": 0, "m365": 0, "other": 0}
    for group in host_group.values():
        if group in group_counts:
            group_counts[group] += 1

    return (
        Point("gov_zabbix_summary")
        .tag("available", str(available).lower())
        .field("problems_total", len(problems))
        .field("problems_disaster", sev_counts["disaster"])
        .field("problems_high", sev_counts["high"])
        .field("problems_average", sev_counts["average"])
        .field("problems_warning", sev_counts["warning"])
        .field("hosts_total", len(host_group))
        .field("hosts_cftv", group_counts["cftv"])
        .field("hosts_m365", group_counts["m365"])
        .time(now, WritePrecision.S)
    )


def _write_points(points: list[Point]) -> None:
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


def collect_zabbix_ops() -> None:
    """Collect Zabbix active problems and host group stats, write to InfluxDB."""
    now = datetime.now(UTC)
    available = True
    problems: list[dict] = []
    host_group: dict[str, str] = {}

    try:
        if not settings.ZABBIX_TOKEN:
            raise RuntimeError("ZABBIX_TOKEN not configured")
        problems = _fetch_problems()
        host_group = _fetch_host_group_map()
    except Exception as exc:
        log.error("zabbix_collect_failed", error=str(exc))
        available = False

    points: list[Point] = []
    points.extend(_build_problem_points(problems, available, now))
    points.append(_build_summary_point(problems, host_group, available, now))

    _write_points(points)
    log.info(
        "zabbix_ops_collected",
        available=available,
        problems=len(problems),
        hosts=len(host_group),
        points=len(points),
    )
