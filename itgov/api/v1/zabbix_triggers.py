"""Triggers / Problemas do Zabbix — consulta direta via API JSON-RPC.

Traz os problemas ativos e os resolvidos nos últimos ``_RESOLVED_DAYS`` dias.
Cache: TTL 60s (dados de monitoramento devem ser frescos).
Requer: ZABBIX_URL e ZABBIX_TOKEN no ambiente.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 60
_RESOLVED_DAYS = 7
_RESOLVED_LIMIT = 500
_TZ_LOCAL = ZoneInfo("America/Sao_Paulo")
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
    if url and not url.endswith("/api_jsonrpc.php"):
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
            "output": ["triggerid", "description", "manual_close"],
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
        item = _problem_item(p, host_name)
        item["manual_close"] = trig.get("manual_close") == "1"
        result.append(item)

    result.sort(key=lambda x: (-x["severity"], x["since_iso"] or ""))
    return result


def _fmt_ts(ts: int) -> tuple[str, str | None]:
    """Formata um epoch do Zabbix em horário de Brasília (texto curto, ISO)."""
    if not ts:
        return "—", None
    dt = datetime.fromtimestamp(ts, tz=UTC).astimezone(_TZ_LOCAL)
    return dt.strftime("%d/%m %H:%M"), dt.isoformat()


def _problem_item(p: dict, host_name: str) -> dict:
    """Campos comuns a problema ativo e resolvido."""
    sev = str(p.get("severity", "0"))
    sev_info = _SEV_MAP.get(sev, _SEV_MAP["0"])
    since, since_iso = _fmt_ts(int(p.get("clock", 0)))
    return {
        "eventid": p["eventid"],
        "triggerid": p["objectid"],
        "name": p.get("name", ""),
        "host": host_name,
        "severity": int(sev),
        "severity_label": sev_info["label"],
        "severity_color": sev_info["color"],
        "acknowledged": p.get("acknowledged") == "1",
        "since": since,
        "since_iso": since_iso,
        "zabbix_url": _build_event_url(p["eventid"], p["objectid"]),
    }


def _fetch_resolved(days: int = _RESOLVED_DAYS) -> list[dict]:
    """Busca os eventos de problema dos últimos ``days`` dias que já normalizaram.

    Returns:
        Lista do mais recente para o mais antigo, com ``resolved_at``.
    """
    events = _zbx(
        "event.get",
        {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged", "r_eventid"],
            "source": 0,  # eventos de trigger
            "object": 0,
            "value": 1,  # PROBLEM
            "time_from": int(time.time()) - days * 86400,
            "selectHosts": ["name"],
            "sortfield": ["clock"],
            "sortorder": "DESC",
            "limit": _RESOLVED_LIMIT,
        },
    )
    events = [e for e in events if e.get("r_eventid", "0") != "0"]
    if not events:
        return []

    recovery = _zbx("event.get", {"output": ["eventid", "clock"], "eventids": [e["r_eventid"] for e in events]})
    recovery_clock = {r["eventid"]: int(r.get("clock", 0)) for r in recovery}

    result = []
    for e in events:
        hosts = e.get("hosts", [])
        item = _problem_item(e, hosts[0]["name"] if hosts else "—")
        item["resolved_at"], item["resolved_iso"] = _fmt_ts(recovery_clock.get(e["r_eventid"], 0))
        result.append(item)
    return result


def _build_event_url(eventid: str, triggerid: str) -> str:
    front = os.getenv("ZABBIX_FRONT_URL", "").rstrip("/")
    if not front:
        front = os.getenv("ZABBIX_URL", "").rstrip("/").replace("/api_jsonrpc.php", "")
    # Com triggerid=0 o Zabbix não acha o evento e a página abre vazia.
    return f"{front}/tr_events.php?triggerid={triggerid}&eventid={eventid}"


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
        resolved = _fetch_resolved()
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
        "resolved": resolved,
        "resolved_days": _RESOLVED_DAYS,
        "counts": counts,
        "updated_at": datetime.now(_TZ_LOCAL).strftime("%d/%m %H:%M"),
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
        invalidate_cache()
        return True
    except Exception as exc:
        log.warning("zabbix_ack_failed", error=str(exc))
        return False


def invalidate_cache() -> None:
    """Descarta o cache para a próxima leitura refletir o Zabbix."""
    global _cache_dados
    with _cache_lock:
        _cache_dados = None


def resolve_problem(eventid: str, *, close: bool, acknowledged: bool, message: str) -> bool:
    """Marca um problema como resolvido no Zabbix.

    Args:
        eventid: Evento de problema.
        close: Fecha o problema (só para trigger com ``manual_close=1``).
        acknowledged: Se o evento já tem ack — o Zabbix recusa ack repetido.
        message: Mensagem registrada no histórico do evento.

    Returns:
        ``True`` se o Zabbix aceitou a operação.
    """
    action = 4  # adicionar mensagem
    if close:
        action |= 1
    if not acknowledged:
        action |= 2
    try:
        _zbx("event.acknowledge", {"eventids": [eventid], "action": action, "message": message})
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        log.warning("zabbix_resolve_failed", eventid=eventid, close=close, error=str(exc))
        return False
    invalidate_cache()
    return True
