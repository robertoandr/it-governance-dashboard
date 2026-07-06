"""Coletor Microsoft Secure Score Control Profiles — escreve m365_secure_score_controls
no bucket governance_raw.

Measurement exclusiva (não compartilhada com gov_m365_secure_score, o coletor
do score agregado) — um ponto por controle, a cada coleta.

Métricas coletadas via Graph API /security/secureScoreControlProfiles (v1.0):
  m365_secure_score_controls
    tags: control_name, category
    fields: max_score (float), current_score (float), on (bool, controle implementado)

Auth: client_credentials via MSAL — herda BaseOAuthCollector (token cache + retry 429).
Schedule: a cada 6 horas (mesma cadência do secure_score_collector).
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

log = structlog.get_logger("m365_secure_score_controls_collector")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]
_MEASUREMENT = "m365_secure_score_controls"
_CONTROL_PROFILES_URL = (
    f"{_GRAPH_BASE}/security/secureScoreControlProfiles"
    "?$top=999&$select=controlName,controlCategory,implementationStatus,score,maxScore"
)


def _build_point(perfil: dict, collected_at: datetime) -> Point:
    """Constrói o Point de um único controle a partir do perfil retornado pelo Graph."""
    control_name = perfil.get("controlName") or "unknown"
    categoria = perfil.get("controlCategory") or "Outros"
    max_score = float(perfil.get("maxScore") or 0.0)
    score = float(perfil.get("score") or 0.0)
    implementado = perfil.get("implementationStatus") == "implemented"

    return (
        Point(_MEASUREMENT)
        .tag("control_name", control_name)
        .tag("category", categoria)
        .field("max_score", max_score)
        .field("current_score", score)
        .field("on", implementado)
        .time(collected_at, WritePrecision.S)
    )


class SecureScoreControlsCollector(BaseOAuthCollector):
    """Coleta os perfis de controle do Secure Score e grava no InfluxDB."""

    def __init__(self) -> None:
        if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
            raise RuntimeError("AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET devem estar configurados")
        super().__init__(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
            scopes=_SCOPES,
        )

    def _fetch_control_profiles(self) -> list[dict]:
        """Busca todos os perfis de controle, paginando via @odata.nextLink."""
        return list(self._paginate(_CONTROL_PROFILES_URL))

    def collect(self) -> None:
        """Coleta os perfis de controle e grava um ponto por controle no InfluxDB."""
        log.info("secure_score_controls_coleta_iniciada")
        collected_at = datetime.now(UTC)

        perfis = self._fetch_control_profiles()
        if not perfis:
            log.warning("secure_score_controls_lista_vazia")
            return

        pontos = [_build_point(perfil, collected_at) for perfil in perfis]

        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=settings.INFLUX_BUCKET_RAW,
                record=pontos,
            )

        log.info(
            "secure_score_controls_escrito_influxdb",
            measurement=_MEASUREMENT,
            controles_gravados=len(pontos),
        )


def run() -> None:
    """Entry point para o APScheduler."""
    if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
        log.warning("secure_score_controls_ignorado", motivo="AZURE_* env vars não configuradas")
        return
    try:
        SecureScoreControlsCollector().collect()
    except Exception as exc:
        log.error("secure_score_controls_job_falhou", erro=str(exc))
