"""Dados Zabbix para o dashboard de monitoramento.

KPIs e score: lidos do InfluxDB (coletados pelo zabbix_risk/availability collectors).
Lista de problemas: chamada direta ao Zabbix API com Bearer token (real-time, cache 5min).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 300  # 5 minutos

_lock_summary = threading.Lock()
_cache_summary: dict | None = None
_cache_summary_ts: float = 0.0

_lock_problems = threading.Lock()
_cache_problems: list | None = None
_cache_problems_ts: float = 0.0


def _cache_valido(ts: float) -> bool:
    return (time.monotonic() - ts) < _CACHE_TTL


# ── InfluxDB helper ───────────────────────────────────────────────────────────


def _query_influx(flux: str) -> list[dict[str, Any]]:
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    provider = InfluxDBMetricsProvider()
    return provider._query(flux)


def _ler_influx() -> dict:
    """Lê gov_zabbix_disponibilidade e gov_zabbix_riscos do InfluxDB."""
    from app.config import get_settings

    bucket = get_settings().influx.bucket_raw

    flux_disp = f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_disponibilidade")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
    flux_riscos = f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_riscos")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""

    disp_rows = _query_influx(flux_disp)
    risk_rows = _query_influx(flux_riscos)

    disp = disp_rows[-1] if disp_rows else {}
    risk = risk_rows[-1] if risk_rows else {}

    return {
        # Disponibilidade
        "uptime_pct": float(disp.get("uptime_pct", 0.0)),
        "score_disp": float(disp.get("score", 0.0)),
        "hosts_up": int(disp.get("hosts_up", 0)),
        "hosts_down": int(disp.get("hosts_down", 0)),
        "hosts_unknown": int(disp.get("hosts_unknown", 0)),
        "total_monitorado": int(disp.get("total_monitorado", 0)),
        "top_host_down": str(disp.get("top_host_down", "—") or "—"),
        # Riscos
        "score_risco": float(risk.get("score", 0.0)),
        "criticos": int(risk.get("criticos", 0)),
        "altos": int(risk.get("altos", 0)),
        "medios": int(risk.get("medios", 0)),
        "avisos": int(risk.get("avisos", 0)),
        "total_problemas": int(risk.get("total_problemas", 0)),
        "top_incidente": str(risk.get("top_incidente", "—") or "—"),
        # Timestamps
        "_time_disp": disp.get("_time"),
        "_time_risk": risk.get("_time"),
    }


# ── Zabbix API helper ─────────────────────────────────────────────────────────


def _zbx(method: str, params: dict[str, Any]) -> Any:
    """Chama Zabbix JSON-RPC com Bearer token."""
    url = os.getenv("ZABBIX_URL", "")
    if not url:
        raise RuntimeError("ZABBIX_URL não configurado")
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"

    token = os.getenv("ZABBIX_TOKEN", "")
    resp = requests.post(
        url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {token}",
        },
        timeout=15,
        verify=False,  # nosec B501 -- Zabbix interno sem cert válido # noqa: S501
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def _buscar_problemas() -> list[dict]:
    """Busca problemas ativos com nomes dos hosts afetados."""
    problemas_raw = _zbx(
        "problem.get",
        {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged"],
            "recent": False,
            "suppressed": False,
        },
    )
    if not problemas_raw:
        return []

    # Busca hosts por trigger em lote
    trigger_ids = list({p["objectid"] for p in problemas_raw})
    triggers = _zbx(
        "trigger.get",
        {
            "output": ["triggerid"],
            "selectHosts": ["name"],
            "triggerids": trigger_ids,
        },
    )
    trigger_hosts: dict[str, list[str]] = {
        t["triggerid"]: [h["name"] for h in t.get("hosts", [])] for t in (triggers or [])
    }

    sev_label = {0: "not classified", 1: "information", 2: "warning", 3: "average", 4: "high", 5: "disaster"}

    from datetime import UTC, datetime

    resultado = []
    for p in sorted(problemas_raw, key=lambda x: int(x["severity"]), reverse=True):
        sev = int(p.get("severity", 0))
        ts = int(p["clock"])
        clock_fmt = datetime.fromtimestamp(ts, tz=UTC).strftime("%d/%m %H:%M") if ts else "—"
        resultado.append(
            {
                "eventid": p["eventid"],
                "name": p["name"],
                "severity": sev,
                "severity_label": sev_label.get(sev, "unknown"),
                "acknowledged": str(p.get("acknowledged", "0")) == "1",
                "clock_fmt": clock_fmt,
                "hosts": trigger_hosts.get(p["objectid"], []),
            }
        )

    return resultado


# ── Cache público ─────────────────────────────────────────────────────────────


def get_cached_zabbix_summary() -> dict:
    """Retorna KPIs Zabbix do InfluxDB com cache TTL 5min."""
    global _cache_summary, _cache_summary_ts
    with _lock_summary:
        if _cache_summary is not None and _cache_valido(_cache_summary_ts):
            log.debug("zabbix_monitoring.summary.cache.hit")
            return _cache_summary

    log.info("zabbix_monitoring.summary.cache.miss")
    dados = _ler_influx()
    with _lock_summary:
        _cache_summary = dados
        _cache_summary_ts = time.monotonic()
    return dados


def get_cached_problems() -> list[dict]:
    """Retorna lista de problemas ativos do Zabbix com cache TTL 5min."""
    global _cache_problems, _cache_problems_ts
    with _lock_problems:
        if _cache_problems is not None and _cache_valido(_cache_problems_ts):
            log.debug("zabbix_monitoring.problems.cache.hit")
            return _cache_problems

    log.info("zabbix_monitoring.problems.cache.miss")
    try:
        dados = _buscar_problemas()
    except Exception as exc:
        log.warning("zabbix_monitoring.problems.falhou", erro=str(exc))
        dados = []
    with _lock_problems:
        _cache_problems = dados
        _cache_problems_ts = time.monotonic()
    return dados
