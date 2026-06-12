"""Acronis Cyber Cloud collector — escreve KPIs no bucket governance_raw.

Métricas coletadas:
  gov_acronis_agents      — online, offline, outdated, total
  gov_acronis_protection  — protected_machines, total_machines
  gov_acronis_storage     — used_bytes

Autenticação: OAuth2 client_credentials nativo (HTTP Basic Auth).
Paginação:    cursor-based via campo 'cursor' na resposta.
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
        base_url: URL base da API Acronis (ex.: https://eu2-cloud.acronis.com).
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
                # força renovação do token após espera
                self._cached_token = None
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Acronis: tentativas esgotadas para {url}")

    def _paginar(self, path: str, params: dict[str, Any] | None = None) -> Generator[dict[str, Any], None, None]:
        """Itera sobre todas as páginas usando cursor pagination do Acronis."""
        params = dict(params or {})
        params.setdefault("limit", _DEFAULT_PAGE_SIZE)
        while True:
            pagina = self._get(path, params=params)
            itens = pagina.get("items", [])
            yield from itens
            cursor = pagina.get("cursor")
            if not cursor or not itens:
                break
            params = {"cursor": cursor, "limit": params.get("limit", _DEFAULT_PAGE_SIZE)}

    # ── Coleta de métricas ──────────────────────────────────────────────────

    def _coletar_agentes(self) -> dict[str, int]:
        """Retorna contagens de agentes por status."""
        contagens: dict[str, int] = {"online": 0, "offline": 0, "outdated": 0, "total": 0}
        for agente in self._paginar("/api/agent_manager/v2/agents"):
            contagens["total"] += 1
            status = (agente.get("status") or "").lower()
            if status == "online":
                contagens["online"] += 1
            elif status == "offline":
                contagens["offline"] += 1
            # 'outdated' pode vir como flag separada ou status
            if agente.get("outdated") or status == "outdated":
                contagens["outdated"] += 1
        return contagens

    def _coletar_protecao(self) -> dict[str, int]:
        """Retorna máquinas protegidas vs total."""
        total = 0
        protegidas = 0
        for recurso in self._paginar("/api/resource_management/v4/resources", params={"type": "machine"}):
            total += 1
            protecao = recurso.get("protection", {})
            if isinstance(protecao, dict) and protecao.get("status") == "protected":
                protegidas += 1
        return {"protected_machines": protegidas, "total_machines": total}

    def _coletar_storage(self) -> dict[str, int]:
        """Retorna bytes usados no storage Acronis."""
        try:
            dados = self._get("/api/vault_manager/v1/vaults", params={"limit": 1000})
            total_bytes = sum(v.get("bytesUsed", 0) for v in dados.get("items", []))
            return {"used_bytes": total_bytes}
        except requests.HTTPError as exc:
            log.warning("acronis_storage_falhou", erro=str(exc))
            return {"used_bytes": 0}

    # ── Ciclo principal ─────────────────────────────────────────────────────

    def collect(self) -> None:
        """Executa a coleta completa e escreve os 3 measurements no InfluxDB."""
        log.info("acronis_coleta_iniciada")
        coletado_em = datetime.now(UTC)

        agentes = self._coletar_agentes()
        protecao = self._coletar_protecao()
        storage = self._coletar_storage()

        log.info(
            "acronis_metricas_coletadas",
            agentes=agentes,
            protecao=protecao,
            storage=storage,
        )

        pontos = [
            Point("gov_acronis_agents")
            .field("online", agentes["online"])
            .field("offline", agentes["offline"])
            .field("outdated", agentes["outdated"])
            .field("total", agentes["total"])
            .time(coletado_em, WritePrecision.S),
            Point("gov_acronis_protection")
            .field("protected_machines", protecao["protected_machines"])
            .field("total_machines", protecao["total_machines"])
            .time(coletado_em, WritePrecision.S),
            Point("gov_acronis_storage").field("used_bytes", storage["used_bytes"]).time(coletado_em, WritePrecision.S),
        ]

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
            measurements=["gov_acronis_agents", "gov_acronis_protection", "gov_acronis_storage"],
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
