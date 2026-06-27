"""Dados Acronis Cyber Cloud para o dashboard de Backup/Proteção.

Todos os dados são lidos do InfluxDB (coletados pelo acronis_collector e
acronis_risk_collector). Nenhuma chamada direta à API Acronis é feita aqui.

Measurements usados:
  gov_acronis_agents        — totais: online, offline, outdated
  gov_acronis_risk_summary  — offline_gt_20d, sem_plano, incidents_total, license_issues
  gov_acronis_machines      — por máquina: offline_days, has_active_plan, open_incidents
  gov_acronis_incidents     — incidentes individuais com tipo e severidade
"""

from __future__ import annotations

import threading
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 300

_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _query(flux: str) -> list[dict[str, Any]]:
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    return InfluxDBMetricsProvider()._query(flux)


def _get_bucket() -> str:
    from app.config import get_settings

    return get_settings().influx.bucket_raw


def _buscar_dados() -> dict:
    bucket = _get_bucket()

    # ── 1. Totais de agentes ────────────────────────────────────────────────
    rows_agents = _query(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_acronis_agents")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")
    agents_row = rows_agents[-1] if rows_agents else {}

    # ── 2. Resumo de riscos ────────────────────────────────────────────────
    rows_risk = _query(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_acronis_risk_summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")
    risk_row = rows_risk[-1] if rows_risk else {}

    # ── 3. Por máquina (offline_days, has_active_plan, incidents) ──────────
    rows_machines = _query(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_acronis_machines")
  |> filter(fn: (r) =>
      r._field == "offline_days" or
      r._field == "offline_gt_20d" or
      r._field == "has_active_plan" or
      r._field == "open_incidents" or
      r._field == "edr_incidents")
  |> last()
  |> pivot(
      rowKey: ["tenant", "resource_name", "protection_status"],
      columnKey: ["_field"],
      valueColumn: "_value")
""")

    # ── 4. Incidentes individuais (últimas 48h) ────────────────────────────
    rows_incidents = _query(f"""
from(bucket: "{bucket}")
  |> range(start: -48h)
  |> filter(fn: (r) => r._measurement == "gov_acronis_incidents")
  |> filter(fn: (r) => r._field == "count")
  |> last()
""")

    # ── 5. Último login (audit log coletado pelo acronis_risk_collector) ───
    rows_login = _query(f"""
from(bucket: "{bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "gov_acronis_last_login")
  |> filter(fn: (r) => r._field == "user_email")
  |> last()
""")
    login_row = rows_login[-1] if rows_login else {}
    last_login_user: str | None = login_row.get("_value") or None
    last_login_time = login_row.get("_time") or None

    # ── Processar máquinas ─────────────────────────────────────────────────
    offline_gt20: list[dict] = []
    sem_plano: list[dict] = []

    for row in rows_machines:
        name = str(row.get("resource_name", "")).strip()
        tenant = str(row.get("tenant", "")).strip()
        status = str(row.get("protection_status", "")).strip()
        offline_days = int(row.get("offline_days", 0) or 0)
        has_plan = int(row.get("has_active_plan", 0) or 0)
        offline_f = int(row.get("offline_gt_20d", 0) or 0)
        incidents = int(row.get("open_incidents", 0) or 0)

        if offline_f:
            offline_gt20.append(
                {"name": name, "tenant": tenant, "offline_days": offline_days, "open_incidents": incidents}
            )
        if not has_plan:
            sem_plano.append({"name": name, "tenant": tenant, "status": status, "open_incidents": incidents})

    offline_gt20.sort(key=lambda x: x["offline_days"], reverse=True)
    sem_plano.sort(key=lambda x: x["name"])

    # ── Processar incidentes ───────────────────────────────────────────────
    incidentes: list[dict] = []
    for row in rows_incidents:
        incidentes.append(
            {
                "resource_name": str(row.get("resource_name", "—")),
                "tenant": str(row.get("tenant", "—")),
                "alert_type": str(row.get("alert_type", "—")),
                "severity": str(row.get("severity", "—")),
                "time": row.get("_time"),
            }
        )
    incidentes.sort(key=lambda x: str(x.get("time") or ""), reverse=True)

    total = int(agents_row.get("total", 0) or 0)
    online = int(agents_row.get("online", 0) or 0)
    offline = int(agents_row.get("offline", 0) or 0)
    protected = total - int(risk_row.get("sem_plano", 0) or 0)

    # Formatar timestamp do último login para exibição
    last_login_fmt: str | None = None
    if last_login_time:
        try:
            from datetime import UTC, datetime

            lt = (
                last_login_time
                if hasattr(last_login_time, "strftime")
                else datetime.fromisoformat(str(last_login_time).replace("Z", "+00:00"))
            )
            last_login_fmt = lt.astimezone(UTC).strftime("%d/%m/%Y %H:%M")
        except Exception:
            last_login_fmt = str(last_login_time)[:16]

    return {
        # KPIs
        "total_agents": total,
        "online": online,
        "offline": offline,
        "outdated": int(agents_row.get("outdated", 0) or 0),
        "protected": protected,
        "protected_pct": round(protected / total * 100, 1) if total else 0.0,
        "sem_plano_count": int(risk_row.get("sem_plano", 0) or 0),
        "offline_gt_20d_count": int(risk_row.get("offline_gt_20d", 0) or 0),
        "incidents_total": int(risk_row.get("incidents_total", 0) or 0),
        "license_issues": int(risk_row.get("license_issues", 0) or 0),
        "edr_total": int(risk_row.get("edr_total", 0) or 0),
        "last_login_user": last_login_user,
        "last_login_fmt": last_login_fmt,
        # Listas
        "offline_gt20": offline_gt20,
        "sem_plano": sem_plano,
        "incidentes": incidentes,
    }


def get_cached_acronis_summary() -> dict:
    """Retorna dados Acronis do InfluxDB com cache TTL 5min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("acronis_backup.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("acronis_backup.cache.miss")
    dados = _buscar_dados()
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
