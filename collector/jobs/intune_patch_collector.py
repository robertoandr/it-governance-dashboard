"""Intune patch compliance collector — writes gov_intune_patch_compliance to InfluxDB.

Collects complianceState for all managed devices and writes one Point per OS type.

Counting rules (intentional — do not simplify):
  compliant        → numerator + denominator
  inGracePeriod    → excluded from pct entirely; written as in_grace field for visibility
  noncompliant     → denominator only (counted as non-compliant)
  conflict/error/unknown → denominator only (conservative: doubt = risk)
  notApplicable    → excluded from denominator (no policy = not meaningful)

compliance_pct = compliant / (compliant + noncompliant) per os_type

Permission required: DeviceManagementManagedDevices.Read.All (Application)
Schedule: every 6 hours via APScheduler.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from base_oauth_collector import BaseOAuthCollector

from config import settings

log = structlog.get_logger("intune_patch_collector")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]

_NONCOMPLIANT_STATES = frozenset({"noncompliant", "conflict", "error", "unknown"})


def _os_bucket(raw: str) -> str:
    s = (raw or "").lower()
    if "windows" in s:
        return "Windows"
    if "mac" in s or "darwin" in s:
        return "macOS"
    if "android" in s:
        return "Android"
    if "ios" in s or "iphone" in s:
        return "iOS"
    return "Other"


class IntunePatchCollector(BaseOAuthCollector):
    """Collects Intune device compliance state and writes per-OS breakdown to InfluxDB."""

    def __init__(self) -> None:
        if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
            raise RuntimeError("AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET must be set")
        super().__init__(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
            scopes=_SCOPES,
        )

    def _fetch_devices(self) -> list[dict]:
        return list(
            self._paginate(
                f"{_GRAPH_BASE}/deviceManagement/managedDevices",
                params={"$select": "complianceState,operatingSystem", "$top": "999"},
            )
        )

    def collect(self) -> None:
        log.info("intune_collection_started")
        collected_at = datetime.now(UTC)

        devices = self._fetch_devices()
        if not devices:
            log.warning("intune_no_devices_returned")
            return

        # Group by normalised OS bucket
        by_os: dict[str, Counter] = {}
        for d in devices:
            os_type = _os_bucket(d.get("operatingSystem", ""))
            state = d.get("complianceState", "unknown")
            by_os.setdefault(os_type, Counter())[state] += 1

        points: list[Point] = []
        for os_type, counts in by_os.items():
            compliant = counts.get("compliant", 0)
            in_grace = counts.get("inGracePeriod", 0)
            not_applicable = counts.get("notApplicable", 0)
            noncompliant = sum(counts.get(s, 0) for s in _NONCOMPLIANT_STATES)

            # denominator excludes notApplicable and inGracePeriod
            total_evaluated = compliant + noncompliant
            compliance_pct = round(compliant / total_evaluated * 100, 1) if total_evaluated else 0.0

            log.info(
                "intune_os_metrics",
                os_type=os_type,
                compliant=compliant,
                noncompliant=noncompliant,
                in_grace=in_grace,
                not_applicable=not_applicable,
                total_evaluated=total_evaluated,
                compliance_pct=compliance_pct,
            )

            points.append(
                Point("gov_intune_patch_compliance")
                .tag("os_type", os_type)
                .field("compliant", compliant)
                .field("noncompliant", noncompliant)
                .field("in_grace", in_grace)
                .field("not_applicable", not_applicable)
                .field("total_evaluated", total_evaluated)
                .field("compliance_pct", compliance_pct)
                .time(collected_at, WritePrecision.SECONDS)
            )

        try:
            with InfluxDBClient(
                url=settings.INFLUX_URL,
                token=settings.INFLUX_TOKEN,
                org=settings.INFLUX_ORG,
            ) as client:
                client.write_api(write_options=SYNCHRONOUS).write(
                    bucket=settings.INFLUX_BUCKET_RAW,
                    record=points,
                )
        except Exception:
            # log.exception captura traceback completo — gap no dashboard sem isso
            # é invisível até a auditoria. Re-raise para o APScheduler registrar também.
            log.exception(
                "intune_write_failed",
                measurement="gov_intune_patch_compliance",
                os_buckets=list(by_os.keys()),
                total_devices=len(devices),
            )
            raise

        log.info(
            "intune_metrics_written",
            measurement="gov_intune_patch_compliance",
            os_buckets=list(by_os.keys()),
            total_devices=len(devices),
        )


def run() -> None:
    """Entry point for APScheduler."""
    try:
        IntunePatchCollector().collect()
    except Exception:
        log.exception("intune_job_failed")
