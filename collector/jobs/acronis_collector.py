"""Acronis Cyber Cloud collector — escreve KPIs no bucket governance_raw.

Métricas coletadas:
  gov_acronis_agents             — total, online, offline, outdated
  gov_acronis_version_compliance — total, outdated, compliant_pct
  gov_acronis_tenant_inventory   — total por tenant (tag: tenant)

Autenticação: OAuth2 client_credentials nativo (HTTP Basic Auth).
Paginação:    cursor-based via paging.cursors.after (itera até esgotar).
Retry:        Retry-After em respostas 429.
Schedule:     a cada 6h via APScheduler.
"""

from __future__ import annotations

import sys
import time
from base64 import b64encode
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings

log = structlog.get_logger("acronis_collector")

_TOKEN_EXPIRY_BUFFER = 60  # segundos de margem antes de renovar o token
_MAX_RETRIES = 4
_DEFAULT_PAGE_SIZE = 100


class AcronisCollector:
    """Coleta métricas do Acronis Cyber Cloud e persiste no InfluxDB.

    Args:
        base_url: URL base da API Acronis (ex.: https://cyber.opustech.com.br).
        client_id: Client ID da integração OAuth2.
        client_secret: Client secret — nunca logado.
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0

    # ── Autenticação ────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Retorna token válido; renova via client_credentials quando expirado."""
        now = time.monotonic()
        if self._cached_token and now < self._token_expires_at:
            return self._cached_token

        credentials = b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        resp = requests.post(
            f"{self._base_url}/api/2/idp/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()

        if "access_token" not in payload:
            raise RuntimeError(f"Acronis: token não retornado — {payload.get('error', 'desconhecido')}")

        self._cached_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = now + expires_in - _TOKEN_EXPIRY_BUFFER
        log.info("acronis_token_adquirido", expires_in=expires_in)
        return self._cached_token

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET com retry em 429 (Retry-After)."""
        url = f"{self._base_url}{path}"
        for tentativa in range(_MAX_RETRIES):
            headers = {"Authorization": f"Bearer {self._get_token()}"}
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                log.warning("rate_limited_acronis", url=url, retry_after=retry_after, tentativa=tentativa)
                time.sleep(retry_after)
                self._cached_token = None
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Acronis: tentativas esgotadas para {url}")

    def _paginar(self, path: str, params: dict[str, Any] | None = None) -> Generator[dict[str, Any], None, None]:
        """Itera sobre todas as páginas usando cursor pagination do Acronis.

        A API retorna o cursor em paging.cursors.after; a próxima página
        é requisitada com o parâmetro after=<cursor>.
        """
        params = dict(params or {})
        params.setdefault("limit", _DEFAULT_PAGE_SIZE)
        while True:
            pagina = self._get(path, params=params)
            itens = pagina.get("items", [])
            yield from itens
            cursor = pagina.get("paging", {}).get("cursors", {}).get("after")
            if not cursor or not itens:
                break
            params = {"after": cursor, "limit": params.get("limit", _DEFAULT_PAGE_SIZE)}

    # ── Coleta de métricas ──────────────────────────────────────────────────

    def _coletar_agentes(self) -> tuple[dict[str, int], dict[str, float], dict[str, int]]:
        """Itera uma única vez sobre todos os agentes e retorna os 3 datasets.

        Returns:
            Tupla com (contagens_agentes, compliance_versao, inventario_tenant).

        Campos confirmados na API (campo booleano, não string):
          - online: bool — true/false
          - installer_version.current.release_id — versão instalada
          - installer_version.latest.release_id  — versão disponível
          - tenant.name — identificador do tenant
        """
        agentes: dict[str, int] = {"total": 0, "online": 0, "offline": 0, "outdated": 0}
        inventario: dict[str, int] = {}

        for agente in self._paginar("/api/agent_manager/v2/agents"):
            agentes["total"] += 1

            # online é bool — não comparar com string
            if agente.get("online"):
                agentes["online"] += 1
            else:
                agentes["offline"] += 1

            inst = agente.get("installer_version", {})
            current_id = inst.get("current", {}).get("release_id", "")
            latest_id = inst.get("latest", {}).get("release_id", "")
            if current_id and latest_id and current_id != latest_id:
                agentes["outdated"] += 1

            tenant_nome = agente.get("tenant", {}).get("name", "desconhecido")
            inventario[tenant_nome] = inventario.get(tenant_nome, 0) + 1

        total = agentes["total"]
        outdated = agentes["outdated"]
        compliant_pct = round((total - outdated) / total * 100, 1) if total > 0 else 0.0

        compliance: dict[str, float] = {
            "total": float(total),
            "outdated": float(outdated),
            "compliant_pct": compliant_pct,
        }

        return agentes, compliance, inventario

    # ── Ciclo principal ─────────────────────────────────────────────────────

    def collect(self) -> None:
        """Executa a coleta completa e escreve os 3 measurements no InfluxDB."""
        log.info("acronis_coleta_iniciada")
        coletado_em = datetime.now(UTC)

        agentes, compliance, inventario = self._coletar_agentes()

        log.info(
            "acronis_metricas_coletadas",
            agentes=agentes,
            compliance=compliance,
            tenants=len(inventario),
        )

        pontos: list[Point] = [
            Point("gov_acronis_agents")
            .field("total", agentes["total"])
            .field("online", agentes["online"])
            .field("offline", agentes["offline"])
            .field("outdated", agentes["outdated"])
            .time(coletado_em, WritePrecision.S),
            Point("gov_acronis_version_compliance")
            .field("total", int(compliance["total"]))
            .field("outdated", int(compliance["outdated"]))
            .field("compliant_pct", compliance["compliant_pct"])
            .time(coletado_em, WritePrecision.S),
        ]

        for tenant, contagem in inventario.items():
            pontos.append(
                Point("gov_acronis_tenant_inventory")
                .tag("tenant", tenant)
                .field("total", contagem)
                .time(coletado_em, WritePrecision.S)
            )

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
            "acronis_metricas_escritas",
            measurements=["gov_acronis_agents", "gov_acronis_version_compliance", "gov_acronis_tenant_inventory"],
            total_agentes=agentes["total"],
            total_tenants=len(inventario),
        )


def run() -> None:
    """Entry point para o APScheduler."""
    try:
        AcronisCollector(
            base_url=settings.ACRONIS_BASE_URL,
            client_id=settings.ACRONIS_CLIENT_ID,
            client_secret=settings.ACRONIS_CLIENT_SECRET,
        ).collect()
    except Exception as exc:
        log.error("acronis_job_falhou", erro=str(exc))
