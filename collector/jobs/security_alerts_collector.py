"""Coletor de alertas de segurança do Microsoft Defender — escreve gov_security_alerts.

KPI-END-01: conta alertas do Defender abertos há mais de 24h.

Measurement: gov_security_alerts
  fields: total_open (int), high (int), medium (int), low (int), older_than_24h (int)

Requer permissão: SecurityAlert.Read.All
Se a permissão não estiver concedida (HTTP 403), o coletor loga warning e retorna sem raise.

Schedule: a cada 1 hora via APScheduler (CronTrigger hour="*/1").
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from base_oauth_collector import BaseOAuthCollector

from config import settings

log = structlog.get_logger("security_alerts_collector")

_MEASUREMENT = "gov_security_alerts"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]


class SecurityAlertsCollector(BaseOAuthCollector):
    """Coleta alertas de segurança do Microsoft Defender via Graph API."""

    def __init__(self) -> None:
        super().__init__(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
            scopes=_GRAPH_SCOPES,
        )

    def _get_alerts_summary(self) -> dict:
        """Retorna resumo de alertas: total, por severidade, abertos > 24h."""
        url = (
            f"{_GRAPH_BASE}/security/alerts_v2"
            "?$filter=status eq 'new'"
            "&$top=999"
            "&$select=id,title,severity,status,createdDateTime,category"
        )
        alerts = list(self._paginate(url))

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        total_open = len(alerts)
        high = sum(1 for a in alerts if a.get("severity", "").lower() == "high")
        medium = sum(1 for a in alerts if a.get("severity", "").lower() == "medium")
        low = sum(1 for a in alerts if a.get("severity", "").lower() == "low")
        older_than_24h = 0
        for alert in alerts:
            created_str = alert.get("createdDateTime", "")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if created < cutoff:
                        older_than_24h += 1
                except ValueError:
                    pass

        log.info(
            "security_alerts.summary",
            total_open=total_open,
            high=high,
            medium=medium,
            low=low,
            older_than_24h=older_than_24h,
        )
        return {
            "total_open": total_open,
            "high": high,
            "medium": medium,
            "low": low,
            "older_than_24h": older_than_24h,
        }

    def collect(self) -> None:
        """Coleta alertas e grava no InfluxDB."""
        log.info("security_alerts_collection_started")
        collected_at = datetime.now(UTC)

        try:
            summary = self._get_alerts_summary()
        except Exception as exc:
            err_str = str(exc)
            if "403" in err_str or "Forbidden" in err_str:
                log.warning(
                    "security_alerts_permission_denied",
                    error=err_str,
                    hint="Conceda SecurityAlert.Read.All ao app registration",
                )
                return
            log.error("security_alerts_collection_failed", error=err_str)
            raise

        point = (
            Point(_MEASUREMENT)
            .field("total_open", int(summary["total_open"]))
            .field("high", int(summary["high"]))
            .field("medium", int(summary["medium"]))
            .field("low", int(summary["low"]))
            .field("older_than_24h", int(summary["older_than_24h"]))
            .time(collected_at, WritePrecision.S)
        )

        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=settings.INFLUX_BUCKET_RAW,
                record=point,
            )

        log.info(
            "security_alerts_written",
            measurement=_MEASUREMENT,
            total_open=summary["total_open"],
            older_than_24h=summary["older_than_24h"],
        )


def run() -> None:
    """Entry point para o APScheduler."""
    if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
        log.warning("security_alerts_ignorado", motivo="AZURE_* env vars nao configuradas")
        return
    try:
        SecurityAlertsCollector().collect()
    except Exception as exc:
        log.error("security_alerts_job_failed", error=str(exc))
