"""Acronis risk posture collector — gov_acronis_machines por maquina.

Measurement: gov_acronis_machines
  tags:   tenant, resource_name, protection_status
  fields:
    offline_days     (int)  — 0 se online; proxy via meta.atp update_time se offline
    offline_gt_20d   (int)  — 0/1
    has_active_plan  (int)  — 0/1 ("activeProtection" in components)
    pending_updates  (int)  — alertas AcroinstReboot* por maquina
    open_incidents   (int)  — alertas criticos (excluindo BackupFailed)
    edr_incidents    (int)  — EDRIncidentDetected
    backup_noise     (int)  — BackupFailed (ruido conhecido, orfao)

JOIN alerta->agente: hostname (resourceId na API e hex curto, nao UUID).
Fallback se sem match: KPIs agregados preservados, join_fallback logado.
"""

from __future__ import annotations

import sys
import time
from base64 import b64encode
from collections import defaultdict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

log = structlog.get_logger("acronis_risk")

_MAX_RETRIES = 4
_PAGE_SIZE = 100
_TOKEN_EXPIRY_BUFFER = 60
_BACKUP_FAILED_TYPE = "BackupFailed"
_EDR_TYPE = "EDRIncidentDetected"
_OFFERING_INSUFFICIENT = "OfferingItemIsNotSufficient"
_REBOOT_PREFIX = "AcroinstReboot"


class AcronisRiskCollector:
    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        creds = b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        resp = requests.post(
            f"{self._base_url}/api/2/idp/token",
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = now + int(payload.get("expires_in", 3600)) - _TOKEN_EXPIRY_BUFFER
        return self._token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        for attempt in range(_MAX_RETRIES):
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self._get_token()}"},
                params=params,
                timeout=60,
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                log.warning("rate_limited", url=url, wait=wait, attempt=attempt)
                time.sleep(wait)
                self._token = None
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Acronis: tentativas esgotadas para {url}")

    def _paginar(self, path: str) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": _PAGE_SIZE}
        items: list[dict[str, Any]] = []
        while True:
            page = self._get(path, params)
            batch = page.get("items", [])
            items.extend(batch)
            cursor = page.get("paging", {}).get("cursors", {}).get("after")
            if not cursor or not batch:
                break
            params = {"after": cursor, "limit": _PAGE_SIZE}
        return items

    # ── Coleta ──────────────────────────────────────────────────────────────

    def _coletar_agentes(self) -> list[dict[str, Any]]:
        return self._paginar("/api/agent_manager/v2/agents")

    def _coletar_alertas(self) -> list[dict[str, Any]]:
        return self._paginar("/api/alert_manager/v1/alerts")

    # ── Calculos ─────────────────────────────────────────────────────────────

    @staticmethod
    def _offline_days(agente: dict[str, Any]) -> int:
        """Dias desde o ultimo update de componente. So chamado quando online=False."""
        comps = agente.get("meta", {}).get("atp", {}).get("components", [])
        latest: datetime | None = None
        for c in comps:
            ut = c.get("update_time")
            if not ut:
                continue
            try:
                dt = parsedate_to_datetime(ut)
            except Exception as exc:
                log.debug("offline_days_parse_error", value=ut, error=str(exc))
                continue
            if latest is None or dt > latest:
                latest = dt
        if latest is None:
            return 0
        return max(0, (datetime.now(UTC) - latest).days)

    @staticmethod
    def _build_hostname_index(agentes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Indice hostname (lower) -> agente. Inclui versao sem dominio."""
        idx: dict[str, dict[str, Any]] = {}
        for ag in agentes:
            hn = ag.get("hostname", "")
            if not hn:
                continue
            idx[hn.lower()] = ag
            short = hn.split(".")[0].lower()
            if short:
                idx[short] = ag
        return idx

    @staticmethod
    def _agrupar_alertas(
        alertas: list[dict[str, Any]],
        hn_idx: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, dict[str, int]], int, int, list[dict[str, Any]]]:
        """Agrupa contagens de alertas por hostname e coleta incidentes individuais.

        Returns:
            por_maquina:    hostname -> {open_incidents, edr_incidents, pending_updates, backup_noise}
            license_issues: count de OfferingItemIsNotSufficient (agregado)
            no_match:       alertas sem JOIN
            incidentes:     lista de dicts para escrita individual em gov_acronis_incidents
        """
        por_maquina: dict[str, dict[str, int]] = defaultdict(
            lambda: {"open_incidents": 0, "edr_incidents": 0, "pending_updates": 0, "backup_noise": 0}
        )
        license_issues = 0
        no_match = 0
        incidentes: list[dict[str, Any]] = []

        for al in alertas:
            atype = al.get("type", "")
            sev = al.get("severity", "")
            details = al.get("details", {})
            rname = details.get("resourceName", "")
            created_raw = al.get("createdAt", "")

            if atype == _OFFERING_INSUFFICIENT:
                license_issues += 1
                continue

            # Resolver hostname via JOIN por resourceName
            hn_key: str | None = None
            resolved_name = rname
            if rname:
                short = rname.split(".")[0].lower()
                if rname.lower() in hn_idx:
                    hn_key = rname.lower()
                    resolved_name = hn_idx[hn_key].get("hostname", rname)
                elif short in hn_idx:
                    hn_key = short
                    resolved_name = hn_idx[hn_key].get("hostname", rname)

            if hn_key is None:
                no_match += 1
                continue

            ag = hn_idx[hn_key]
            tenant = ag.get("tenant", {}).get("name", "desconhecido")
            hostname = ag.get("hostname", "") or resolved_name
            bucket = por_maquina[hn_key]

            if atype == _BACKUP_FAILED_TYPE:
                bucket["backup_noise"] += 1
            elif atype == _EDR_TYPE:
                bucket["edr_incidents"] += 1
                bucket["open_incidents"] += 1
                incidentes.append(
                    {
                        "resource_name": hostname,
                        "tenant": tenant,
                        "alert_type": atype,
                        "severity": sev,
                        "created_at": created_raw,
                    }
                )
            elif atype.startswith(_REBOOT_PREFIX):
                bucket["pending_updates"] += 1
            elif sev == "critical":
                bucket["open_incidents"] += 1
                incidentes.append(
                    {
                        "resource_name": hostname,
                        "tenant": tenant,
                        "alert_type": atype,
                        "severity": sev,
                        "created_at": created_raw,
                    }
                )

        return dict(por_maquina), license_issues, no_match, incidentes

    # ── Escrita ──────────────────────────────────────────────────────────────

    def collect(self) -> None:
        log.info("acronis_risk_coleta_iniciada")
        ts = datetime.now(UTC)

        agentes = self._coletar_agentes()
        alertas = self._coletar_alertas()
        log.info("acronis_risk_dados_coletados", agentes=len(agentes), alertas=len(alertas))

        hn_idx = self._build_hostname_index(agentes)
        por_maquina, license_issues, no_match, incidentes = self._agrupar_alertas(alertas, hn_idx)

        if no_match > 0:
            log.warning(
                "acronis_risk_join_fallback",
                alertas_sem_match=no_match,
                motivo="resourceName nao encontrado entre agentes — open_incidents agregados podem estar subestimados",
            )

        pontos: list[Point] = []
        stats = {
            "total": 0,
            "offline_gt_20d": 0,
            "sem_plano": 0,
            "edr_total": 0,
            "backup_noise_total": 0,
            "incidents_total": 0,
        }

        for ag in agentes:
            hostname = ag.get("hostname", "") or ag.get("name", "") or ag.get("id", "unknown")
            tenant = ag.get("tenant", {}).get("name", "desconhecido")
            online: bool = bool(ag.get("online"))
            comps: list[str] = ag.get("components", [])

            protection_status = "online" if online else "offline"
            has_active_plan = 1 if "activeProtection" in comps else 0
            offline_days = 0 if online else self._offline_days(ag)
            offline_gt_20d = 1 if offline_days > 20 else 0

            hn_key = hostname.lower()
            short_key = hostname.split(".")[0].lower()
            bucket = por_maquina.get(hn_key) or por_maquina.get(short_key) or {}

            open_incidents = bucket.get("open_incidents", 0)
            edr_incidents = bucket.get("edr_incidents", 0)
            pending_updates = bucket.get("pending_updates", 0)
            backup_noise = bucket.get("backup_noise", 0)

            stats["total"] += 1
            stats["offline_gt_20d"] += offline_gt_20d
            if not has_active_plan:
                stats["sem_plano"] += 1
            stats["edr_total"] += edr_incidents
            stats["backup_noise_total"] += backup_noise
            stats["incidents_total"] += open_incidents

            pontos.append(
                Point("gov_acronis_machines")
                .tag("tenant", tenant)
                .tag("resource_name", hostname)
                .tag("protection_status", protection_status)
                .field("offline_days", offline_days)
                .field("offline_gt_20d", offline_gt_20d)
                .field("has_active_plan", has_active_plan)
                .field("pending_updates", pending_updates)
                .field("open_incidents", open_incidents)
                .field("edr_incidents", edr_incidents)
                .field("backup_noise", backup_noise)
                .time(ts, WritePrecision.S)
            )

        # Incidentes individuais (excluindo BackupFailed) para tabela Grafana
        for inc in incidentes:
            try:
                inc_ts = datetime.fromisoformat(inc["created_at"].replace("Z", "+00:00"))
            except Exception:
                inc_ts = ts
            pontos.append(
                Point("gov_acronis_incidents")
                .tag("resource_name", inc["resource_name"])
                .tag("tenant", inc["tenant"])
                .tag("alert_type", inc["alert_type"])
                .tag("severity", inc["severity"])
                .field("count", 1)
                .time(inc_ts, WritePrecision.S)
            )

        # Ponto agregado para licenca insuficiente (nao e por maquina)
        pontos.append(
            Point("gov_acronis_risk_summary")
            .field("license_issues", license_issues)
            .field("offline_gt_20d", stats["offline_gt_20d"])
            .field("sem_plano", stats["sem_plano"])
            .field("edr_total", stats["edr_total"])
            .field("backup_noise_total", stats["backup_noise_total"])
            .field("incidents_total", stats["incidents_total"])
            .time(ts, WritePrecision.S)
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
            "acronis_risk_escrito",
            maquinas=stats["total"],
            offline_gt_20d=stats["offline_gt_20d"],
            edr_total=stats["edr_total"],
            backup_noise=stats["backup_noise_total"],
            license_issues=license_issues,
            incidentes_individuais=len(incidentes),
        )


def run() -> None:
    try:
        AcronisRiskCollector(
            base_url=settings.ACRONIS_BASE_URL,
            client_id=settings.ACRONIS_CLIENT_ID,
            client_secret=settings.ACRONIS_CLIENT_SECRET,
        ).collect()
    except Exception as exc:
        log.error("acronis_risk_job_falhou", erro=str(exc))
