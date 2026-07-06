"""Coletor Microsoft Secure Score Controls — escreve m365_secure_score_controls
no bucket governance_raw.

Measurement exclusiva (não compartilhada com gov_m365_secure_score, o coletor
do score agregado) — um ponto por controle efetivamente avaliado para o tenant.

Join necessário entre dois recursos do Graph API v1.0:
  /security/secureScoreControlProfiles — catálogo genérico de controles
    (maxScore, categoria, remediation); chave estável: ``id``. Não tem o
    score real do tenant, e ``controlName`` vem vazio aqui.
  /security/secureScores?$top=1 — score real do tenant por controle
    (controlScores[]: score, scoreInPercentage); referencia o controle via
    ``controlName``, que contém o mesmo valor do ``id`` do catálogo acima.

  m365_secure_score_controls
    tags: control_name, category
    fields: max_score (float), current_score (float), on (bool, scoreInPercentage >= 100)

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
    f"{_GRAPH_BASE}/security/secureScoreControlProfiles?$top=999&$select=id,controlCategory,maxScore"
)
_SECURE_SCORE_URL = f"{_GRAPH_BASE}/security/secureScores?$top=1&$select=controlScores"


def _build_point(perfil: dict, control_score: dict, collected_at: datetime) -> Point:
    """Constrói o Point de um controle a partir do join profile (catálogo) + control_score (tenant)."""
    control_name = control_score.get("controlName") or "unknown"
    categoria = perfil.get("controlCategory") or control_score.get("controlCategory") or "Outros"
    max_score = float(perfil.get("maxScore") or 0.0)
    score = float(control_score.get("score") or 0.0)
    pct = control_score.get("scoreInPercentage")
    implementado = pct is not None and pct >= 100.0

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
    """Coleta os control scores do tenant e junta com o catálogo de controles."""

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
        """Busca o catálogo de controles (maxScore, categoria), paginando via @odata.nextLink."""
        return list(self._paginate(_CONTROL_PROFILES_URL))

    def _fetch_control_scores(self) -> list[dict]:
        """Busca o score real do tenant por controle (secureScores.controlScores)."""
        data = self._get(_SECURE_SCORE_URL)
        valores = data.get("value", [])
        if not valores:
            return []
        return valores[0].get("controlScores", [])

    def collect(self) -> None:
        """Faz o join profiles×scores e grava um ponto por controle avaliado no InfluxDB."""
        log.info("secure_score_controls_coleta_iniciada")
        collected_at = datetime.now(UTC)

        perfis = self._fetch_control_profiles()
        control_scores = self._fetch_control_scores()
        if not control_scores:
            log.warning("secure_score_controls_sem_control_scores")
            return

        perfis_por_id = {p.get("id"): p for p in perfis if p.get("id")}
        pontos = [_build_point(perfis_por_id.get(cs.get("controlName"), {}), cs, collected_at) for cs in control_scores]

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
