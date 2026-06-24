"""Coletor de alertas de segurança do Microsoft Defender — escreve gov_security_alerts.

KPI-END-01: conta alertas do Defender abertos há mais de 24h.

Measurement: gov_security_alerts
  fields: total_open (int), high (int), medium (int), low (int), older_than_24h (int)

Requer permissão: SecurityAlert.Read.All
Se a permissão não estiver concedida (HTTP 403), o coletor loga warning e retorna sem raise.

Schedule: a cada 1 hora via APScheduler (CronTrigger hour="*/1").
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings

log = structlog.get_logger("security_alerts_collector")

_MEASUREMENT = "gov_security_alerts"


class SecurityAlertsCollector:
    """Coleta alertas de segurança do Microsoft Defender via Graph API."""

    def collect(self) -> None:
        """Coleta alertas e grava no InfluxDB."""
        log.info("security_alerts_collection_started")
        collected_at = datetime.now(UTC)

        try:
            from itgov.services.security_alerts_graph_client import SecurityAlertsGraphClient

            summary = asyncio.run(SecurityAlertsGraphClient().get_alerts_summary())
        except Exception as exc:
            err_str = str(exc)
            # Tratar 403 graciosamente — permissão não concedida
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
            .field("total_open", int(summary.get("total_open", 0)))
            .field("high", int(summary.get("high", 0)))
            .field("medium", int(summary.get("medium", 0)))
            .field("low", int(summary.get("low", 0)))
            .field("older_than_24h", int(summary.get("older_than_24h", 0)))
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
            total_open=summary.get("total_open", 0),
            older_than_24h=summary.get("older_than_24h", 0),
        )


def run() -> None:
    """Entry point para o APScheduler."""
    if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
        log.warning("security_alerts_ignorado", motivo="AZURE_* env vars não configuradas")
        return
    try:
        SecurityAlertsCollector().collect()
    except Exception as exc:
        log.error("security_alerts_job_failed", error=str(exc))
