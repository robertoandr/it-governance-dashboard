"""Dados CFTV (câmeras e NVRs) via Zabbix API para o dashboard.

Consulta os host groups que começam com "CFTV" no Zabbix usando Bearer token
(sem user.login / sem ZABBIX_PASSWORD). Cache TTL 2min (dados real-time).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 120  # 2 minutos — dados operacionais de câmeras mudam rápido

_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0


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


def _buscar_cftv() -> dict:
    # ── 1. Grupos CFTV ───────────────────────────────────────────────────────
    groups = _zbx(
        "hostgroup.get",
        {"output": ["groupid", "name"], "search": {"name": "CFTV"}},
    )
    gids = [g["groupid"] for g in groups if g["name"].startswith("CFTV")]

    if not gids:
        return {
            "enabled": False,
            "total": 0,
            "up": 0,
            "down": 0,
            "nodata": 0,
            "maint": 0,
            "up_pct": 0.0,
            "by_subcat": {},
            "down_list": [],
            "maint_list": [],
        }

    # ── 2. Hosts com ping e tags ──────────────────────────────────────────────
    hosts = _zbx(
        "host.get",
        {
            "output": ["hostid", "host", "name", "maintenance_status"],
            "groupids": gids,
            "selectInterfaces": ["ip"],
            "selectItems": ["key_", "lastvalue", "lastclock"],
            "selectTags": ["tag", "value"],
        },
    )

    # ── 3. Problemas ativos por host ──────────────────────────────────────────
    host_ids = [h["hostid"] for h in hosts]
    problems_raw = (
        _zbx(
            "problem.get",
            {
                "output": ["eventid", "name", "severity", "clock", "objectid"],
                "hostids": host_ids,
                "recent": False,
                "suppressed": False,
            },
        )
        if host_ids
        else []
    )

    # Agrupar severidade máxima por host (via trigger → host)
    # Para simplificar, conta apenas total de problemas por host via tags
    problems_by_host: dict[str, int] = {}
    if problems_raw:
        trigger_ids = list({p["objectid"] for p in problems_raw})
        triggers = _zbx(
            "trigger.get",
            {
                "output": ["triggerid"],
                "selectHosts": ["hostid"],
                "triggerids": trigger_ids,
            },
        )
        for t in triggers or []:
            for h in t.get("hosts", []):
                hid = h["hostid"]
                problems_by_host[hid] = problems_by_host.get(hid, 0) + 1

    # ── 4. Processar hosts ────────────────────────────────────────────────────
    by_subcat: dict[str, dict] = {}
    down_list: list[dict] = []
    maint_list: list[dict] = []
    maint_count = 0

    for h in hosts:
        subcat = "outros"
        andar = "?"
        for tg in h.get("tags", []):
            if tg["tag"] == "subcategory":
                subcat = tg["value"]
            elif tg["tag"] == "andar":
                andar = tg["value"]

        if subcat not in by_subcat:
            by_subcat[subcat] = {"total": 0, "up": 0, "down": 0, "nodata": 0, "maint": 0}
        by_subcat[subcat]["total"] += 1

        ip = (h.get("interfaces") or [{}])[0].get("ip", "?")
        in_maint = h.get("maintenance_status") == "1"
        hid = h.get("hostid", "")
        n_problems = problems_by_host.get(hid, 0)

        if in_maint:
            by_subcat[subcat]["maint"] += 1
            maint_count += 1
            maint_list.append({"host": h["host"], "name": h["name"], "ip": ip, "andar": andar, "subcat": subcat})

        ping = next((i for i in h.get("items", []) if i["key_"] == "icmpping"), None)
        if not ping or not ping.get("lastclock") or ping["lastclock"] == "0":
            by_subcat[subcat]["nodata"] += 1
        elif ping["lastvalue"] == "1":
            by_subcat[subcat]["up"] += 1
        else:
            by_subcat[subcat]["down"] += 1
            if not in_maint:
                down_list.append(
                    {
                        "host": h["host"],
                        "name": h["name"],
                        "ip": ip,
                        "subcat": subcat,
                        "andar": andar,
                        "problems": n_problems,
                    }
                )

    total = sum(s["total"] for s in by_subcat.values())
    up = sum(s["up"] for s in by_subcat.values())
    down = sum(s["down"] for s in by_subcat.values())
    nodata = sum(s["nodata"] for s in by_subcat.values())

    return {
        "enabled": True,
        "total": total,
        "up": up,
        "down": down,
        "nodata": nodata,
        "maint": maint_count,
        "up_pct": round(up / total * 100, 1) if total else 0.0,
        "by_subcat": by_subcat,
        "down_list": sorted(down_list, key=lambda x: (x["subcat"], x["host"])),
        "maint_list": sorted(maint_list, key=lambda x: (x["subcat"], x["host"])),
    }


def get_cached_cftv_summary() -> dict:
    """Retorna dados CFTV do Zabbix com cache TTL 2min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("cftv_monitoring.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("cftv_monitoring.cache.miss")
    try:
        dados = _buscar_cftv()
    except Exception as exc:
        log.warning("cftv_monitoring.busca_falhou", erro=str(exc))
        dados = {
            "enabled": False,
            "total": 0,
            "up": 0,
            "down": 0,
            "nodata": 0,
            "maint": 0,
            "up_pct": 0.0,
            "by_subcat": {},
            "down_list": [],
            "maint_list": [],
            "_erro": str(exc),
        }
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
