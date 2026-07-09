"""Dados de Infraestrutura para o dashboard.

Combina:
- InfluxDB: gov_zabbix_summary (totais por categoria, problems by severity)
- Zabbix API Bearer token: hosts nao-CFTV em tempo real + problemas ativos

Exclui apenas os grupos CFTV/* (têm página própria).
Cache TTL 3min.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 180  # 3 minutos

_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0

_CATEGORY_MAP: dict[str, str] = {
    "Microsoft 365": "Microsoft 365",
    "Zabbix servers": "Monitoramento",
    "Servidores": "Servidores",
    "Linux servers": "Servidores",
    "Virtual machines": "Servidores",
    "Hypervisors": "Hypervisors",
    "Databases": "Bancos de Dados",
    "Firewall": "Firewall / Rede",
    "Cloud Services": "Cloud",
    "Applications": "Aplicacoes",
}


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _zbx(method: str, params: dict[str, Any]) -> Any:
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


def _query_influx(flux: str) -> list[dict[str, Any]]:
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    return InfluxDBMetricsProvider()._query(flux)


def _ler_summary_influx() -> dict:
    """Lê gov_zabbix_summary e gov_zabbix_disponibilidade do InfluxDB."""
    from app.config import get_settings

    bucket = get_settings().influx.bucket_raw

    rows_sum = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")
    rows_disp = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_zabbix_disponibilidade")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")
    s = rows_sum[-1] if rows_sum else {}
    d = rows_disp[-1] if rows_disp else {}
    return {
        "hosts_total": int(s.get("hosts_total", 0) or 0),
        "hosts_cftv": int(s.get("hosts_cftv", 0) or 0),
        "hosts_m365": int(s.get("hosts_m365", 0) or 0),
        "problems_disaster": int(s.get("problems_disaster", 0) or 0),
        "problems_high": int(s.get("problems_high", 0) or 0),
        "problems_average": int(s.get("problems_average", 0) or 0),
        "problems_warning": int(s.get("problems_warning", 0) or 0),
        "problems_total": int(s.get("problems_total", 0) or 0),
        "uptime_pct": float(d.get("uptime_pct", 0.0) or 0.0),
        "hosts_up": int(d.get("hosts_up", 0) or 0),
        "hosts_down": int(d.get("hosts_down", 0) or 0),
    }


def _ler_datacenter_temp() -> dict:
    """Lê gov_thingspeak_temperatura (sensor Nextcon) do InfluxDB.

    Retorna último valor (atual/min/max/timestamp) + série de 2h para o
    sparkline. `disponivel=False` quando não há dados no InfluxDB.
    """
    from app.config import get_settings

    bucket = get_settings().influx.bucket_raw

    vazio = {
        "disponivel": False,
        "temp_atual": None,
        "temp_min": None,
        "temp_max": None,
        "atualizado_em": None,
        "serie": [],
    }

    rows_last = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_thingspeak_temperatura")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")
    if not rows_last:
        return vazio

    rows_serie = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "gov_thingspeak_temperatura")
  |> filter(fn: (r) => r._field == "temp_atual")
  |> sort(columns: ["_time"])
""")

    last = rows_last[-1]
    serie = [
        {"time": r["_time"].isoformat(), "value": float(r["_value"])}
        for r in rows_serie
        if r.get("_time") is not None and r.get("_value") is not None
    ]

    return {
        "disponivel": True,
        "temp_atual": float(last.get("temp_atual", 0.0) or 0.0),
        "temp_min": float(last.get("temp_min", 0.0) or 0.0),
        "temp_max": float(last.get("temp_max", 0.0) or 0.0),
        "atualizado_em": last["_time"].isoformat() if last.get("_time") else None,
        "serie": serie,
    }


def _categoria(group_name: str) -> str:
    if group_name in _CATEGORY_MAP:
        return _CATEGORY_MAP[group_name]
    if "Servidores" in group_name or "servidor" in group_name.lower():
        return "Servidores"
    if "Rede" in group_name or "Switch" in group_name:
        return "Firewall / Rede"
    if "Lojas" in group_name:
        return "Lojas"
    return "Outros"


def _buscar_hosts_infra() -> dict:
    """Busca hosts nao-CFTV no Zabbix com problemas ativos."""
    # Todos os grupos exceto CFTV/*
    all_groups = _zbx("hostgroup.get", {"output": ["groupid", "name"]})
    infra_groups = [g for g in all_groups if not g["name"].startswith("CFTV")]
    gids = [g["groupid"] for g in infra_groups]

    if not gids:
        return {"hosts": [], "by_category": {}, "down_list": [], "problems": [], "total_problems": 0}

    hosts = _zbx(
        "host.get",
        {
            "output": ["hostid", "host", "name", "maintenance_status"],
            "groupids": gids,
            "selectGroups": ["name"],
            "selectInterfaces": ["ip"],
            "selectItems": ["key_", "lastvalue", "lastclock"],
        },
    )
    if not hosts:
        return {"hosts": [], "by_category": {}, "down_list": [], "problems": [], "total_problems": 0}

    # Problemas ativos para esses hosts
    host_ids = [h["hostid"] for h in hosts]
    problems_raw = _zbx(
        "problem.get",
        {
            "output": ["eventid", "name", "severity", "clock", "objectid", "acknowledged"],
            "hostids": host_ids,
            "recent": False,
            "suppressed": False,
        },
    )

    problems_by_host: dict[str, int] = {}
    problem_list: list[dict] = []
    sev_label = {0: "not classified", 1: "information", 2: "warning", 3: "average", 4: "high", 5: "disaster"}

    if problems_raw:
        trigger_ids = list({p["objectid"] for p in problems_raw})
        triggers = _zbx(
            "trigger.get",
            {"output": ["triggerid"], "selectHosts": ["hostid", "name"], "triggerids": trigger_ids},
        )
        trigger_hosts = {t["triggerid"]: [h["name"] for h in t.get("hosts", [])] for t in triggers or []}
        trigger_hostids = {t["triggerid"]: [h["hostid"] for h in t.get("hosts", [])] for t in triggers or []}

        from datetime import UTC, datetime

        for p in sorted(problems_raw, key=lambda x: int(x["severity"]), reverse=True):
            sev = int(p.get("severity", 0))
            ts = int(p["clock"])
            clock_fmt = datetime.fromtimestamp(ts, tz=UTC).strftime("%d/%m %H:%M") if ts else "—"
            for hid in trigger_hostids.get(p["objectid"], []):
                problems_by_host[hid] = problems_by_host.get(hid, 0) + 1
            problem_list.append(
                {
                    "name": p["name"],
                    "severity": sev,
                    "severity_label": sev_label.get(sev, "unknown"),
                    "acknowledged": str(p.get("acknowledged", "0")) == "1",
                    "clock_fmt": clock_fmt,
                    "hosts": trigger_hosts.get(p["objectid"], []),
                }
            )

    by_category: dict[str, dict] = {}
    down_list: list[dict] = []

    for h in hosts:
        cat = "Outros"
        for g in h.get("groups", []):
            c = _categoria(g.get("name", ""))
            if c != "Outros":
                cat = c
                break

        if cat not in by_category:
            by_category[cat] = {"total": 0, "up": 0, "down": 0, "nodata": 0, "maint": 0}
        by_category[cat]["total"] += 1

        ip = (h.get("interfaces") or [{}])[0].get("ip", "?")
        in_maint = h.get("maintenance_status") == "1"
        hid = h.get("hostid", "")

        if in_maint:
            by_category[cat]["maint"] += 1

        ping = next((i for i in h.get("items", []) if i["key_"] == "icmpping"), None)
        if not ping or not ping.get("lastclock") or ping["lastclock"] == "0":
            by_category[cat]["nodata"] += 1
        elif ping["lastvalue"] == "1":
            by_category[cat]["up"] += 1
        else:
            by_category[cat]["down"] += 1
            if not in_maint:
                down_list.append(
                    {
                        "host": h["host"],
                        "name": h["name"],
                        "ip": ip,
                        "category": cat,
                        "problems": problems_by_host.get(hid, 0),
                    }
                )

    return {
        "hosts": hosts,
        "by_category": by_category,
        "down_list": sorted(down_list, key=lambda x: x["host"]),
        "problems": problem_list[:40],
        "total_problems": len(problem_list),
    }


def _buscar_infra() -> dict:
    influx = _ler_summary_influx()
    zabbix = _buscar_hosts_infra()

    n_hosts = len(zabbix["hosts"])
    by_cat = zabbix["by_category"]
    total_up = sum(s["up"] for s in by_cat.values())
    total_down = sum(s["down"] for s in by_cat.values())
    total_nodata = sum(s["nodata"] for s in by_cat.values())
    total_maint = sum(s["maint"] for s in by_cat.values())

    return {
        "enabled": True,
        # Dados InfluxDB — visão global do Zabbix
        "influx": influx,
        # Dados Zabbix API — hosts nao-CFTV
        "total": n_hosts,
        "up": total_up,
        "down": total_down,
        "nodata": total_nodata,
        "maint": total_maint,
        "up_pct": round(total_up / n_hosts * 100, 1) if n_hosts else 0.0,
        "by_category": by_cat,
        "down_list": zabbix["down_list"],
        "problems": zabbix["problems"],
        "total_problems": zabbix["total_problems"],
        "datacenter_temp": _ler_datacenter_temp(),
    }


def get_cached_infra_summary() -> dict:
    """Retorna dados de infraestrutura com cache TTL 3min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("infra_monitoring.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("infra_monitoring.cache.miss")
    try:
        dados = _buscar_infra()
    except Exception as exc:
        log.warning("infra_monitoring.busca_falhou", erro=str(exc))
        dados = {
            "enabled": False,
            "influx": {},
            "total": 0,
            "up": 0,
            "down": 0,
            "nodata": 0,
            "maint": 0,
            "up_pct": 0.0,
            "by_category": {},
            "down_list": [],
            "problems": [],
            "total_problems": 0,
            "datacenter_temp": {
                "disponivel": False,
                "temp_atual": None,
                "temp_min": None,
                "temp_max": None,
                "atualizado_em": None,
                "serie": [],
            },
            "_erro": str(exc),
        }
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
