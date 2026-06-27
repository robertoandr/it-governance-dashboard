"""Monitoramento FortiGate via Zabbix API.

Busca hosts do grupo Firewall com template FortiGate by HTTP e retorna:
- Status da API, CPU, memoria, uptime
- Metricas SD-WAN por interface/SLA (latencia, jitter, perda, status)
- Problemas ativos
Cache TTL 60s.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 60
_lock = threading.Lock()
_cache_data: list | None = None
_cache_ts: float = 0.0

_FIREWALL_GROUP = "22"  # groupid do grupo Firewall no Zabbix

_SEV_LABEL = {0: "not classified", 1: "info", 2: "warning", 3: "average", 4: "high", 5: "disaster"}
_SEV_COLOR = {0: "slate", 1: "blue", 2: "yellow", 3: "orange", 4: "red", 5: "red"}


def _load_iface_labels() -> dict[str, str]:
    """Carrega mapeamento iface→label de FORTINET_IFACE_LABELS.

    Formato esperado na env var:
        FORTINET_IFACE_LABELS=wan1=Algar,b=Starlink,internal1=Vivo,...
    """
    raw = os.getenv("FORTINET_IFACE_LABELS", "")
    if not raw.strip():
        return {}
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if "=" in part:
            iface, _, label = part.partition("=")
            result[iface.strip()] = label.strip()
    return result


_SDWAN_RE = re.compile(r"SD-WAN \[([^\]]+)\]:\[([^\]]+)\]: (.+)")


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


def _zbx(method: str, params: dict[str, Any]) -> Any:
    url = os.getenv("ZABBIX_URL", "")
    if not url:
        raise RuntimeError("ZABBIX_URL nao configurado")
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"
    token = os.getenv("ZABBIX_TOKEN", "")
    resp = requests.post(
        url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={"Content-Type": "application/json-rpc", "Authorization": f"Bearer {token}"},
        timeout=15,
        verify=False,  # noqa: S501
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def _buscar_fortinets() -> list[dict]:
    hosts = _zbx(
        "host.get",
        {
            "output": ["hostid", "host", "name"],
            "groupids": [_FIREWALL_GROUP],
            "selectParentTemplates": ["name"],
        },
    )
    # Filtrar apenas hosts com template FortiGate
    fortigates = [h for h in (hosts or []) if any("FortiGate" in t["name"] for t in h.get("parentTemplates", []))]
    if not fortigates:
        return []

    host_ids = [h["hostid"] for h in fortigates]

    # Buscar items de sistema (cpu, mem, uptime, nome, api status)
    system_items = (
        _zbx(
            "item.get",
            {
                "output": ["hostid", "key_", "name", "lastvalue", "lastclock"],
                "hostids": host_ids,
                "filter": {
                    "key_": ["fgate.cpu.util", "fgate.memory.util", "fgate.uptime", "fgate.name", "fgate.api.status"]
                },
            },
        )
        or []
    )

    # Buscar items SD-WAN pelo prefixo do nome
    sdwan_items = (
        _zbx(
            "item.get",
            {
                "output": ["hostid", "key_", "name", "lastvalue", "lastclock"],
                "hostids": host_ids,
                "search": {"name": "SD-WAN ["},
                "searchWildcardsEnabled": False,
            },
        )
        or []
    )

    # Buscar alias/descrição das interfaces via fgate.netif.get_data[*].
    # O template FortiGate by HTTP não cria fgate.interface.alias[*]; os aliases
    # estão no nome do item: "Interface [wan1(VIVO-EMPRESA)]: Get data"
    alias_items = (
        _zbx(
            "item.get",
            {
                "output": ["hostid", "key_", "name"],
                "hostids": host_ids,
                "search": {"key_": "fgate.netif.get_data["},
                "searchWildcardsEnabled": False,
            },
        )
        or []
    )
    # Extrai iface e alias do nome: "Interface [wan1(ALIAS)]: Get data"
    alias_by_host: dict[str, dict[str, str]] = {h["hostid"]: {} for h in fortigates}
    _netif_name_re = re.compile(r"Interface \[([^\(]+)\(([^\)]+)\)\]")
    for ai in alias_items:
        hid = ai["hostid"]
        m = _netif_name_re.search(ai.get("name", ""))
        if m:
            iface, alias = m.group(1).strip(), m.group(2).strip()
            if alias:
                alias_by_host.setdefault(hid, {})[iface] = alias

    # Fallback: env var FORTINET_IFACE_LABELS (sobrescreve os aliases do Zabbix)
    _env_labels = _load_iface_labels()

    # Buscar problemas ativos
    problems_raw = (
        _zbx(
            "problem.get",
            {
                "output": ["eventid", "name", "severity", "clock", "acknowledged", "objectid"],
                "hostids": host_ids,
                "recent": False,
                "suppressed": False,
            },
        )
        or []
    )

    # Mapear trigger -> hostids para associar problema ao host
    trigger_host_map: dict[str, list[str]] = {}
    if problems_raw:
        tids = list({p["objectid"] for p in problems_raw})
        triggers = (
            _zbx(
                "trigger.get",
                {"output": ["triggerid"], "selectHosts": ["hostid"], "triggerids": tids},
            )
            or []
        )
        for t in triggers:
            trigger_host_map[t["triggerid"]] = [h["hostid"] for h in t.get("hosts", [])]

    # Indexar items por hostid
    sys_by_host: dict[str, dict[str, Any]] = {h["hostid"]: {} for h in fortigates}
    for item in system_items:
        hid = item["hostid"]
        key = item["key_"]
        val = item.get("lastvalue", "")
        clk = int(item.get("lastclock", 0) or 0)
        age_s = time.time() - clk if clk else 9999
        sys_by_host.setdefault(hid, {})[key] = {"val": val, "age_s": age_s}

    # Indexar SD-WAN por hostid -> lista de items parseados
    sdwan_by_host: dict[str, list[dict]] = {h["hostid"]: [] for h in fortigates}
    for item in sdwan_items:
        hid = item["hostid"]
        name = item["name"]
        val = item.get("lastvalue", "")
        clk = int(item.get("lastclock", 0) or 0)
        age_s = time.time() - clk if clk else 9999
        if age_s > 600:  # dado > 10 min -> stale
            continue
        m = _SDWAN_RE.match(name)
        if not m:
            continue
        sla, iface, metric = m.group(1), m.group(2), m.group(3).strip()
        sdwan_by_host.setdefault(hid, []).append({"sla": sla, "iface": iface, "metric": metric, "val": val})

    # Indexar problemas por hostid
    prob_by_host: dict[str, list[dict]] = {h["hostid"]: [] for h in fortigates}
    for p in sorted(problems_raw, key=lambda x: int(x["severity"]), reverse=True):
        sev = int(p.get("severity", 0))
        ts = int(p.get("clock", 0) or 0)
        clock_fmt = datetime.fromtimestamp(ts, tz=UTC).strftime("%d/%m %H:%M") if ts else "—"
        for hid in trigger_host_map.get(p["objectid"], []):
            if hid in prob_by_host:
                prob_by_host[hid].append(
                    {
                        "name": p["name"],
                        "severity": sev,
                        "severity_label": _SEV_LABEL.get(sev, "unknown"),
                        "severity_color": _SEV_COLOR.get(sev, "slate"),
                        "acknowledged": str(p.get("acknowledged", "0")) == "1",
                        "clock_fmt": clock_fmt,
                    }
                )

    # Montar SD-WAN agrupado por SLA > interface
    def _agrupar_sdwan(items: list[dict], iface_labels: dict[str, str]) -> list[dict]:
        slas: dict[str, dict[str, dict]] = {}
        for it in items:
            sla = it["sla"]
            iface = it["iface"]
            metric = it["metric"].lower()
            val = it["val"]
            slas.setdefault(sla, {}).setdefault(iface, {})
            if "latency" in metric:
                slas[sla][iface]["latency_ms"] = round(float(val), 1) if val else None
            elif "jitter" in metric:
                slas[sla][iface]["jitter_ms"] = round(float(val), 1) if val else None
            elif "loss" in metric:
                slas[sla][iface]["loss_pct"] = round(float(val), 1) if val else None
            elif "status" in metric or "interface sta" in metric:
                # 0 = alive (UP), 1 = dead (DOWN)
                slas[sla][iface]["down"] = str(val) == "1"

        result = []
        for sla_name, ifaces in sorted(slas.items()):
            members = []
            for iface_name, metrics in sorted(ifaces.items()):
                lat = metrics.get("latency_ms")
                loss = metrics.get("loss_pct", 0.0) or 0.0
                down = metrics.get("down", False)
                if down:
                    link_status = "down"
                elif lat is not None and lat >= 300:
                    link_status = "degraded"
                elif lat is not None and lat >= 100:
                    link_status = "warn"
                elif loss >= 5:
                    link_status = "degraded"
                elif loss >= 1:
                    link_status = "warn"
                else:
                    link_status = "up"
                # Env var sobrescreve alias do Zabbix; Zabbix sobrescreve nome técnico
                label = _env_labels.get(iface_name) or iface_labels.get(iface_name) or iface_name
                members.append(
                    {
                        "iface": iface_name,
                        "label": label,
                        "latency_ms": lat,
                        "jitter_ms": metrics.get("jitter_ms"),
                        "loss_pct": metrics.get("loss_pct"),
                        "status": link_status,
                    }
                )
            result.append({"sla": sla_name, "members": members})
        return result

    result = []
    for h in fortigates:
        hid = h["hostid"]
        sys_data = sys_by_host.get(hid, {})

        def _val(key: str, _sys: dict = sys_data) -> str | None:
            d = _sys.get(key)
            return d["val"] if d and d["age_s"] < 600 else None

        api_raw = _val("fgate.api.status")
        api_up = api_raw == "1" if api_raw is not None else None

        cpu_raw = _val("fgate.cpu.util")
        mem_raw = _val("fgate.memory.util")
        uptime_raw = _val("fgate.uptime")
        fname = _val("fgate.name") or h["name"]

        uptime_h: int | None = None
        if uptime_raw:
            import contextlib

            with contextlib.suppress(ValueError):
                uptime_h = int(int(uptime_raw) / 3600)

        sdwan = _agrupar_sdwan(sdwan_by_host.get(hid, []), alias_by_host.get(hid, {}))
        problems = prob_by_host.get(hid, [])

        has_data = api_raw is not None or bool(sdwan)

        result.append(
            {
                "hostid": hid,
                "host": h["host"],
                "name": fname,
                "has_data": has_data,
                "api_up": api_up,
                "cpu_pct": round(float(cpu_raw), 1) if cpu_raw else None,
                "mem_pct": round(float(mem_raw), 1) if mem_raw else None,
                "uptime_h": uptime_h,
                "sdwan": sdwan,
                "problems": problems,
                "problem_count": len(problems),
            }
        )

    return result


def get_cached_fortinet() -> list[dict]:
    """Retorna lista de FortiGates com cache TTL 60s."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            return _cache_data  # type: ignore[return-value]

    log.info("fortinet_service.cache.miss")
    try:
        dados = _buscar_fortinets()
    except Exception as exc:
        log.warning("fortinet_service.busca_falhou", erro=str(exc))
        dados = []
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
