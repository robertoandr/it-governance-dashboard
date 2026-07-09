"""Triggers / Problemas ativos do Zabbix — consulta direta via API JSON-RPC.

Cache: TTL 60s (dados de monitoramento devem ser frescos).
Requer: ZABBIX_URL e ZABBIX_TOKEN no ambiente.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 60
_cache_lock = threading.Lock()
_cache_dados: dict | None = None
_cache_ts: float = 0.0

_SEV_MAP = {
    "0": {"label": "Não classificado", "color": "gray", "order": 0},
    "1": {"label": "Informação", "color": "blue", "order": 1},
    "2": {"label": "Aviso", "color": "yellow", "order": 2},
    "3": {"label": "Médio", "color": "orange", "order": 3},
    "4": {"label": "Alto", "color": "red", "order": 4},
    "5": {"label": "Desastre", "color": "purple", "order": 5},
}


def _zbx_url() -> str:
    url = os.getenv("ZABBIX_URL", "")
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"
    return url


def _zbx_token() -> str:
    return os.getenv("ZABBIX_TOKEN", "")


def _zbx(method: str, params: dict[str, Any]) -> Any:
    resp = requests.post(
        _zbx_url(),
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {_zbx_token()}",
        },
        timeout=10,
        verify=False,  # nosec B501 -- Zabbix usa certificado self-signed interno # noqa: S501
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def _fetch_problems() -> list[dict]:
    """Busca todos os problemas ativos com nome de host e estado de ack."""
    problems = _zbx(
        "problem.get",
        {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged", "r_eventid"],
            "recent": False,
            "suppressed": False,
            "limit": 500,
        },
    )

    if not problems:
        return []

    # Enriquecer com nome do host via trigger
    trigger_ids = list({p["objectid"] for p in problems})
    triggers_raw = _zbx(
        "trigger.get",
        {
            "output": ["triggerid", "description"],
            "triggerids": trigger_ids,
            "selectHosts": ["hostid", "name"],
        },
    )
    trigger_map: dict[str, dict] = {t["triggerid"]: t for t in triggers_raw}

    result = []
    for p in problems:
        trig = trigger_map.get(p["objectid"], {})
        hosts = trig.get("hosts", [])
        host_name = hosts[0]["name"] if hosts else "—"
        sev = str(p.get("severity", "0"))
        sev_info = _SEV_MAP.get(sev, _SEV_MAP["0"])
        ts = int(p.get("clock", 0))
        since_dt = datetime.fromtimestamp(ts, tz=UTC) if ts else None
        result.append(
            {
                "eventid": p["eventid"],
                "triggerid": p["objectid"],
                "name": p.get("name", ""),
                "host": host_name,
                "severity": int(sev),
                "severity_label": sev_info["label"],
                "severity_color": sev_info["color"],
                "acknowledged": p.get("acknowledged") == "1",
                "since": since_dt.strftime("%d/%m %H:%M") if since_dt else "—",
                "since_iso": since_dt.isoformat() if since_dt else None,
                "zabbix_url": _build_event_url(p["eventid"]),
            }
        )

    result.sort(key=lambda x: (-x["severity"], x["since_iso"] or ""))
    return result


def _build_event_url(eventid: str) -> str:
    front = os.getenv("ZABBIX_FRONT_URL", "").rstrip("/")
    if not front:
        front = os.getenv("ZABBIX_URL", "").rstrip("/").replace("/api_jsonrpc.php", "")
    return f"{front}/tr_events.php?triggerid=0&eventid={eventid}"


def get_cached_triggers() -> dict:
    global _cache_dados, _cache_ts
    with _cache_lock:
        if _cache_dados is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL:
            log.debug("zabbix_triggers.cache.hit")
            return _cache_dados

    log.info("zabbix_triggers.cache.miss")

    if not _zbx_url() or not _zbx_token():
        return {"enabled": False, "reason": "zabbix_not_configured", "problems": [], "counts": {}}

    try:
        problems = _fetch_problems()
    except Exception as exc:
        log.warning("zabbix_triggers.fetch_failed", error=str(exc))
        return {"enabled": False, "reason": str(exc), "problems": [], "counts": {}}

    counts: dict[str, int] = {v["label"]: 0 for v in _SEV_MAP.values()}
    for p in problems:
        label = p["severity_label"]
        counts[label] = counts.get(label, 0) + 1

    result = {
        "enabled": True,
        "total": len(problems),
        "problems": problems,
        "counts": counts,
        "updated_at": datetime.now(UTC).strftime("%d/%m %H:%M"),
    }

    with _cache_lock:
        _cache_dados = result
        _cache_ts = time.monotonic()

    return result


def ack_problem(eventid: str, message: str = "") -> bool:
    """Acknowledge um problema no Zabbix."""
    try:
        _zbx(
            "event.acknowledge",
            {
                "eventids": [eventid],
                "action": 6,  # 2=ack + 4=add message
                "message": message or "Acknowledged via IT Gov Dashboard",
            },
        )
        global _cache_dados
        with _cache_lock:
            _cache_dados = None
        return True
    except Exception as exc:
        log.warning("zabbix_ack_failed", error=str(exc))
        return False
