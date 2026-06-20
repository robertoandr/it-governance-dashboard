"""Dados de Infraestrutura via Zabbix API para o dashboard.

Consulta hosts dos grupos de infra (Servidores, Hypervisors, Databases,
Firewall, VM, Cloud, Rede) usando Bearer token. Exclui CFTV e M365,
que têm páginas próprias. Cache TTL 3min.
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

# Grupos a excluir da página de Infraestrutura (têm páginas próprias ou são internos)
_EXCLUDE_PREFIXES = ("CFTV", "Discovered", "Zabbix", "Microsoft 365", "Lojas")

# Mapeamento grupo → categoria de exibição
_CATEGORY_MAP: dict[str, str] = {
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
        verify=False,  # noqa: S501 — Zabbix interno sem cert válido
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def _categoria(group_name: str) -> str:
    """Retorna a categoria de exibição para um nome de grupo."""
    if group_name in _CATEGORY_MAP:
        return _CATEGORY_MAP[group_name]
    if "Servidores" in group_name or "servidor" in group_name.lower():
        return "Servidores"
    if "Rede" in group_name or "rede" in group_name.lower() or "Switch" in group_name:
        return "Firewall / Rede"
    if "Lojas" in group_name:
        return "Lojas"
    return "Outros"


def _buscar_infra() -> dict:
    # ── 1. Grupos (filtrar infra) ─────────────────────────────────────────────
    all_groups = _zbx("hostgroup.get", {"output": ["groupid", "name"]})
    infra_groups = [g for g in all_groups if not any(g["name"].startswith(p) for p in _EXCLUDE_PREFIXES)]
    gids = [g["groupid"] for g in infra_groups]

    if not gids:
        return {
            "enabled": False,
            "total": 0,
            "up": 0,
            "down": 0,
            "nodata": 0,
            "maint": 0,
            "up_pct": 0.0,
            "by_category": {},
            "down_list": [],
            "problems": [],
        }

    # ── 2. Hosts com ping, grupos e tags ─────────────────────────────────────
    hosts = _zbx(
        "host.get",
        {
            "output": ["hostid", "host", "name", "maintenance_status"],
            "groupids": gids,
            "selectGroups": ["groupid", "name"],
            "selectInterfaces": ["ip"],
            "selectItems": ["key_", "lastvalue", "lastclock"],
            "selectTags": ["tag", "value"],
        },
    )

    # ── 3. Problemas ativos para esses hosts ──────────────────────────────────
    host_ids = [h["hostid"] for h in hosts]
    problems_raw = (
        _zbx(
            "problem.get",
            {
                "output": ["eventid", "name", "severity", "clock", "objectid", "acknowledged"],
                "hostids": host_ids,
                "recent": False,
                "suppressed": False,
            },
        )
        if host_ids
        else []
    )

    # Contar problemas por host e enriquecer com nome de host
    problems_by_host: dict[str, int] = {}
    problem_list: list[dict] = []
    sev_label = {
        0: "not classified",
        1: "information",
        2: "warning",
        3: "average",
        4: "high",
        5: "disaster",
    }

    if problems_raw:
        trigger_ids = list({p["objectid"] for p in problems_raw})
        triggers = _zbx(
            "trigger.get",
            {
                "output": ["triggerid"],
                "selectHosts": ["hostid", "name"],
                "triggerids": trigger_ids,
            },
        )
        trigger_hosts: dict[str, list[str]] = {
            t["triggerid"]: [h["name"] for h in t.get("hosts", [])] for t in triggers or []
        }
        trigger_hostids: dict[str, list[str]] = {
            t["triggerid"]: [h["hostid"] for h in t.get("hosts", [])] for t in triggers or []
        }

        from datetime import UTC, datetime

        for p in sorted(problems_raw, key=lambda x: int(x["severity"]), reverse=True):
            sev = int(p.get("severity", 0))
            ts = int(p["clock"])
            clock_fmt = datetime.fromtimestamp(ts, tz=UTC).strftime("%d/%m %H:%M") if ts else "—"
            host_names = trigger_hosts.get(p["objectid"], [])
            for hid in trigger_hostids.get(p["objectid"], []):
                problems_by_host[hid] = problems_by_host.get(hid, 0) + 1
            problem_list.append(
                {
                    "name": p["name"],
                    "severity": sev,
                    "severity_label": sev_label.get(sev, "unknown"),
                    "acknowledged": str(p.get("acknowledged", "0")) == "1",
                    "clock_fmt": clock_fmt,
                    "hosts": host_names,
                }
            )

    # ── 4. Processar hosts ────────────────────────────────────────────────────
    by_category: dict[str, dict] = {}
    down_list: list[dict] = []
    maint_count = 0

    for h in hosts:
        # Categoria = primeiro grupo que não seja o mais genérico
        host_groups = h.get("groups", [])
        cat = "Outros"
        for g in host_groups:
            gn = g.get("name", "")
            if gn and not any(gn.startswith(p) for p in _EXCLUDE_PREFIXES):
                c = _categoria(gn)
                if c != "Outros":
                    cat = c
                    break
        if cat == "Outros" and host_groups:
            cat = _categoria(host_groups[0].get("name", "Outros"))

        if cat not in by_category:
            by_category[cat] = {"total": 0, "up": 0, "down": 0, "nodata": 0, "maint": 0}
        by_category[cat]["total"] += 1

        ip = (h.get("interfaces") or [{}])[0].get("ip", "?")
        in_maint = h.get("maintenance_status") == "1"
        hid = h.get("hostid", "")

        if in_maint:
            by_category[cat]["maint"] += 1
            maint_count += 1

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

    total = sum(s["total"] for s in by_category.values())
    up = sum(s["up"] for s in by_category.values())
    down = sum(s["down"] for s in by_category.values())
    nodata = sum(s["nodata"] for s in by_category.values())

    return {
        "enabled": True,
        "total": total,
        "up": up,
        "down": down,
        "nodata": nodata,
        "maint": maint_count,
        "up_pct": round(up / total * 100, 1) if total else 0.0,
        "by_category": by_category,
        "down_list": sorted(down_list, key=lambda x: (x["category"], x["host"])),
        "problems": problem_list[:40],
        "total_problems": len(problem_list),
    }


def get_cached_infra_summary() -> dict:
    """Retorna dados de infra do Zabbix com cache TTL 3min."""
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
            "_erro": str(exc),
        }
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
