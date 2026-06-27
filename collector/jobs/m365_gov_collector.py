"""Coletor unificado de KPIs de Governança Microsoft 365.

Coleta 4 métricas via Microsoft Graph API (client_credentials / MSAL) e grava no InfluxDB.

Measurements:
  m365_secure_score        fields: current (float), max (float), percent (float)
  m365_mfa_percent         fields: percent (float), users_with_mfa (int), total_users (int)
  m365_critical_alerts_24h fields: count (int)
  m365_licenses            fields: enabled (int), consumed (int), utilization_pct (float)
                           tags:   skuPartNumber

Permissões Graph necessárias (Application permissions):
  SecurityEvents.Read.All   — /security/secureScores, /security/alerts_v2
  Reports.Read.All          — /reports/authenticationMethods/userRegistrationDetails
  AuditLog.Read.All         — leitura de registros de autenticação
  Directory.Read.All        — /subscribedSkus

Schedule: a cada 30 minutos via APScheduler (registrado em main.py).
Falha isolada: cada KPI tem try/except próprio — 1 KPI falho não derruba os outros 3.
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

log = structlog.get_logger("m365_gov_collector")

_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]
_BUCKET = settings.INFLUX_BUCKET_RAW  # governance_raw


class M365GovCollector(BaseOAuthCollector):
    """Coleta os 4 KPIs de Governança M365 em uma única execução."""

    def __init__(self) -> None:
        super().__init__(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
            scopes=_SCOPES,
        )
        self._influx = InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        )
        self._write = self._influx.write_api(write_options=SYNCHRONOUS)

    def _write_point(self, point: Point) -> None:
        self._write.write(bucket=_BUCKET, record=point)

    # ── KPI 1: Secure Score ──────────────────────────────────────────────────

    def collect_secure_score(self, ts: datetime) -> None:
        kpi = "secure_score"
        try:
            data = self._get(f"{_GRAPH}/security/secureScores", params={"$top": "1"})
            scores = data.get("value", [])
            if not scores:
                log.warning(f"{kpi}_sem_dados", motivo="lista vazia retornada pela API")
                return
            raw = scores[0]
            current = float(raw.get("currentScore", 0.0))
            maximum = float(raw.get("maxScore", 0.0)) or 100.0
            percent = round(current / maximum * 100, 1)
            level = "WARNING" if percent < 75 else "OK"
            point = (
                Point("m365_secure_score")
                .field("current", current)
                .field("max", maximum)
                .field("percent", percent)
                .time(ts, WritePrecision.S)
            )
            self._write_point(point)
            log.info(f"{kpi}_coletado", current=current, max=maximum, percent=percent, level=level)
        except Exception as exc:
            log.error(f"{kpi}_falhou", erro=str(exc))

    # ── KPI 2: % MFA habilitado ──────────────────────────────────────────────

    def collect_mfa_percent(self, ts: datetime) -> None:
        kpi = "mfa_percent"
        try:
            items = list(
                self._paginate(
                    f"{_GRAPH}/reports/authenticationMethods/userRegistrationDetails",
                    params={"$select": "isMfaRegistered", "$top": "999"},
                )
            )
            total = len(items)
            with_mfa = sum(1 for i in items if i.get("isMfaRegistered"))
            percent = round(with_mfa / total * 100, 2) if total else 0.0
            level = "OK" if percent >= 100 else "WARNING"
            point = (
                Point("m365_mfa_percent")
                .field("percent", percent)
                .field("users_with_mfa", with_mfa)
                .field("total_users", total)
                .time(ts, WritePrecision.S)
            )
            self._write_point(point)
            log.info(f"{kpi}_coletado", percent=percent, users_with_mfa=with_mfa, total_users=total, level=level)
        except Exception as exc:
            log.error(f"{kpi}_falhou", erro=str(exc))

    # ── KPI 3: Alertas críticos abertos > 24h ───────────────────────────────

    def collect_critical_alerts(self, ts: datetime) -> None:
        kpi = "critical_alerts_24h"
        try:
            threshold = (ts - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Filtro no servidor: severity high + status new
            # Filtro de criação > 24h feito no cliente (Graph não suporta gt em createdDateTime com $filter)
            alerts = list(
                self._paginate(
                    f"{_GRAPH}/security/alerts_v2",
                    params={
                        "$filter": "severity eq 'high' and status eq 'new'",
                        "$select": "id,createdDateTime,severity,status",
                        "$top": "999",
                    },
                )
            )
            count = sum(1 for a in alerts if (a.get("createdDateTime") or "") < threshold)
            # "menor" = criado ANTES do threshold = mais de 24h atrás
            level = "CRITICAL" if count > 0 else "OK"
            point = Point("m365_critical_alerts_24h").field("count", count).time(ts, WritePrecision.S)
            self._write_point(point)
            log.info(f"{kpi}_coletado", count=count, level=level)
        except Exception as exc:
            log.error(f"{kpi}_falhou", erro=str(exc))

    # ── KPI 4: Licenças ativas (uso vs contratado) ───────────────────────────

    def collect_licenses(self, ts: datetime) -> None:
        kpi = "licenses"
        try:
            data = self._get(f"{_GRAPH}/subscribedSkus")
            skus = data.get("value", [])
            points: list[Point] = []
            for sku in skus:
                part_number = sku.get("skuPartNumber", "UNKNOWN")
                enabled = int((sku.get("prepaidUnits") or {}).get("enabled", 0))
                consumed = int(sku.get("consumedUnits", 0))
                utilization_pct = round(consumed / enabled * 100, 1) if enabled > 0 else 0.0
                points.append(
                    Point("m365_licenses")
                    .tag("skuPartNumber", part_number)
                    .field("enabled", enabled)
                    .field("consumed", consumed)
                    .field("utilization_pct", utilization_pct)
                    .time(ts, WritePrecision.S)
                )
            self._write.write(bucket=_BUCKET, record=points)
            log.info(f"{kpi}_coletado", skus_count=len(points))
        except Exception as exc:
            log.error(f"{kpi}_falhou", erro=str(exc))

    # ── KPI 5: Service Health ────────────────────────────────────────────────

    def collect_service_health(self, ts: datetime) -> None:
        """Grava status de cada serviço M365 em m365_service_status.

        Requer permissão ServiceHealth.Read.All no App Registration.
        """
        kpi = "service_health"
        status_code_map = {
            "serviceOperational": 0,
            "serviceRestored": 0,
            "postIncidentReviewPublished": 0,
            "falsePositive": 0,
            "verifyingService": 1,
            "investigating": 1,
            "restoringService": 1,
            "extendedRecovery": 1,
            "serviceDegradation": 2,
            "serviceInterruption": 2,
        }
        try:
            data = self._get(f"{_GRAPH}/admin/serviceAnnouncement/healthOverviews")
            services = data.get("value", [])
            points: list[Point] = []
            for svc in services:
                status_raw = svc.get("status", "")
                points.append(
                    Point("m365_service_status")
                    .tag("service", svc.get("id", "unknown"))
                    .field("status_code", status_code_map.get(status_raw, 1))
                    .field("status_text", status_raw)
                    .field("display_name", svc.get("service", ""))
                    .time(ts, WritePrecision.S)
                )
            self._write.write(bucket=_BUCKET, record=points)
            log.info(f"{kpi}_coletado", services_count=len(points))
        except Exception as exc:
            log.error(f"{kpi}_falhou", erro=str(exc))

    # ── Orquestrador ─────────────────────────────────────────────────────────

    def collect(self) -> None:
        ts = datetime.now(UTC)
        log.info("m365_gov_coleta_iniciada", timestamp=ts.isoformat())
        self.collect_secure_score(ts)
        self.collect_mfa_percent(ts)
        self.collect_critical_alerts(ts)
        self.collect_licenses(ts)
        self.collect_service_health(ts)
        self._influx.close()
        log.info("m365_gov_coleta_concluida", timestamp=datetime.now(UTC).isoformat())


def run() -> None:
    """Entry point para o APScheduler."""
    if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
        log.warning("m365_gov_ignorado", motivo="AZURE_* env vars não configuradas")
        return
    try:
        M365GovCollector().collect()
    except Exception as exc:
        log.error("m365_gov_job_falhou", erro=str(exc))
