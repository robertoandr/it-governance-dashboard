"""Coletor Microsoft Secure Score — escreve gov_m365_secure_score no bucket governance_raw.

Métricas coletadas via Graph API /security/secureScores (Microsoft Graph v1.0):
  gov_m365_secure_score
    fields: current_score (float), max_score (float), pct (float),
            control_scores_count (int), active_user_count (int)

Score de governança:
  pct = current_score / max_score × 100
  Meta: ≥ 60% → score respeitável; ≥ 85% → OPERACIONAL no pilar Riscos.

Auth: client_credentials via MSAL — herda BaseOAuthCollector (token cache + retry 429).
Schedule: a cada 6 horas (dados mudam lentamente no M365).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from base_oauth_collector import BaseOAuthCollector

from config import settings

log = structlog.get_logger("secure_score_collector")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]
_MEASUREMENT = "gov_m365_secure_score"


class SecureScoreCollector(BaseOAuthCollector):
    """Coleta Microsoft Secure Score e grava no InfluxDB."""

    def __init__(self) -> None:
        if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
            raise RuntimeError("AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET devem estar configurados")
        super().__init__(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
            scopes=_SCOPES,
        )

    def _fetch_secure_score(self) -> dict:
        """Busca o score mais recente via /security/secureScores?$top=1."""
        data = self._get(
            f"{_GRAPH_BASE}/security/secureScores",
            params={"$top": "1"},
        )
        scores = data.get("value", [])
        if not scores:
            raise RuntimeError("secureScores retornou lista vazia — tenant sem dados de segurança")
        return scores[0]

    def collect(self) -> None:
        """Coleta o Secure Score e grava no InfluxDB."""
        log.info("secure_score_coleta_iniciada")
        collected_at = datetime.now(UTC)

        raw = self._fetch_secure_score()

        current = float(raw.get("currentScore", 0.0))
        maximum = float(raw.get("maxScore", 0.0)) or 100.0
        pct = round(current / maximum * 100, 1)
        controls = raw.get("controlScores", [])
        active_users = int(raw.get("activeUserCount", 0))

        log.info(
            "secure_score_metricas_coletadas",
            current_score=current,
            max_score=maximum,
            pct=pct,
            control_scores_count=len(controls),
            active_user_count=active_users,
        )

        ponto = (
            Point(_MEASUREMENT)
            .field("current_score", float(current))
            .field("max_score", float(maximum))
            .field("pct", float(pct))
            .field("control_scores_count", len(controls))
            .field("active_user_count", int(active_users))
            .time(collected_at, WritePrecision.S)
        )

        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=settings.INFLUX_BUCKET_RAW,
                record=ponto,
            )

        log.info(
            "secure_score_escrito_influxdb",
            measurement=_MEASUREMENT,
            pct=pct,
            current_score=current,
            max_score=maximum,
        )


def run() -> None:
    """Entry point para o APScheduler."""
    if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
        log.warning("secure_score_ignorado", motivo="AZURE_* env vars não configuradas")
        return
    try:
        SecureScoreCollector().collect()
    except Exception as exc:
        log.error("secure_score_job_falhou", erro=str(exc))
