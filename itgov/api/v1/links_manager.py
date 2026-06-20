"""Dados para o gerenciador de links WAN/Internet.

Combina:
- SQLite (modelo Link): cadastro dos links (IP, provider, circuito, banda)
- Zabbix API: metricas ICMP em tempo real (status, latencia, perda de pacotes)
- Zabbix history: disponibilidade das ultimas 24h, 7d

Cache por link: TTL 60s (metricas de disponibilidade mudam rapidamente).
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
_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0

# Severidades para classifar latencia
_LATENCY_WARN_MS = 100.0  # amarelo acima de 100ms
_LATENCY_CRIT_MS = 300.0  # vermelho acima de 300ms
_LOSS_WARN_PCT = 1.0  # amarelo acima de 1%
_LOSS_CRIT_PCT = 5.0  # vermelho acima de 5%


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


# ── Zabbix ────────────────────────────────────────────────────────────────────


def _zbx(method: str, params: dict[str, Any]) -> Any:
    url = os.getenv("ZABBIX_URL", "")
    if not url:
        return None
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"
    token = os.getenv("ZABBIX_TOKEN", "")
    try:
        resp = requests.post(
            url,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            headers={"Content-Type": "application/json-rpc", "Authorization": f"Bearer {token}"},
            timeout=10,
            verify=False,  # noqa: S501
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.warning("links_manager.zabbix_erro", method=method, erro=data["error"])
            return None
        return data["result"]
    except Exception as exc:
        log.warning("links_manager.zabbix_falhou", method=method, erro=str(exc))
        return None


def _metricas_zabbix(hostids: list[str]) -> dict[str, dict]:
    """Busca icmpping, icmppingloss e icmppingsec para cada hostid."""
    if not hostids:
        return {}

    items = _zbx(
        "item.get",
        {
            "output": ["itemid", "hostid", "key_", "lastvalue", "lastclock", "units", "value_type"],
            "hostids": hostids,
            "search": {"key_": "icmpping"},
            "searchWildcardsEnabled": True,
            "filter": {},
        },
    )
    if not items:
        return {}

    metricas: dict[str, dict] = {hid: {} for hid in hostids}
    for item in items:
        hid = item["hostid"]
        key = item["key_"]
        val = item.get("lastvalue", "")
        clock = int(item.get("lastclock", 0) or 0)

        # Ignorar dados > 10 min (host pode ter ficado sem coleta)
        age_min = (time.time() - clock) / 60 if clock else 9999
        stale = age_min > 10

        if key == "icmpping":
            metricas[hid]["up"] = val == "1" and not stale
            metricas[hid]["stale"] = stale
            metricas[hid]["last_clock"] = clock
        elif key == "icmppingloss":
            metricas[hid]["loss_pct"] = float(val) if val else None
        elif key == "icmppingsec":
            # Zabbix retorna em segundos; converter para ms
            metricas[hid]["latency_ms"] = round(float(val) * 1000, 2) if val else None

    return metricas


def _disponibilidade_zabbix(hostid: str, period_h: int = 24) -> float | None:
    """Calcula % de disponibilidade ICMP das últimas period_h horas via history."""
    time_from = int(time.time()) - period_h * 3600

    # Busca itemid do icmpping
    items = _zbx(
        "item.get",
        {"output": ["itemid"], "hostids": [hostid], "filter": {"key_": "icmpping"}},
    )
    if not items:
        return None
    itemid = items[0]["itemid"]

    history = _zbx(
        "history.get",
        {
            "output": ["clock", "value"],
            "itemids": [itemid],
            "history": 3,  # unsigned integer
            "time_from": time_from,
            "sortfield": "clock",
            "sortorder": "ASC",
            "limit": 5000,
        },
    )
    if not history:
        return None

    total = len(history)
    up = sum(1 for h in history if h["value"] == "1")
    return round(up / total * 100, 1) if total else None


# ── Banco de dados ────────────────────────────────────────────────────────────


def _listar_links() -> list[dict]:
    """Retorna todos os links ativos do banco de dados."""
    try:
        from app.models.link import Link

        links = Link.query.filter_by(active=True).order_by(Link.name).all()
        return [lnk.to_dict() for lnk in links]
    except Exception as exc:
        log.warning("links_manager.db_falhou", erro=str(exc))
        return []


# ── Montagem final ─────────────────────────────────────────────────────────────


def _classificar_status(up: bool | None, stale: bool, loss: float | None, latency: float | None) -> str:
    if stale or up is None:
        return "nodata"
    if not up:
        return "down"
    if loss is not None and loss >= _LOSS_CRIT_PCT:
        return "degraded"
    if latency is not None and latency >= _LATENCY_CRIT_MS:
        return "degraded"
    if loss is not None and loss >= _LOSS_WARN_PCT:
        return "warn"
    if latency is not None and latency >= _LATENCY_WARN_MS:
        return "warn"
    return "up"


def _buscar_dados() -> dict:
    links_db = _listar_links()

    hostids = [lnk["zabbix_hostid"] for lnk in links_db if lnk.get("zabbix_hostid")]
    metricas = _metricas_zabbix(hostids)

    links_enriquecidos: list[dict] = []
    for lnk in links_db:
        hid = lnk.get("zabbix_hostid")
        m = metricas.get(hid, {}) if hid else {}

        up = m.get("up")
        stale = m.get("stale", True)
        loss = m.get("loss_pct")
        latency = m.get("latency_ms")
        clock = m.get("last_clock", 0)

        status = _classificar_status(up, stale, loss, latency)

        clock_fmt = ""
        if clock:
            clock_fmt = datetime.fromtimestamp(clock, tz=UTC).strftime("%d/%m %H:%M")

        links_enriquecidos.append(
            {
                **lnk,
                "status": status,
                "latency_ms": latency,
                "loss_pct": loss,
                "last_check": clock_fmt,
                "avail_24h": None,  # carregado sob demanda na rota de detalhe
            }
        )

    total = len(links_enriquecidos)
    up_count = sum(1 for lnk in links_enriquecidos if lnk["status"] == "up")
    down_count = sum(1 for lnk in links_enriquecidos if lnk["status"] == "down")
    degraded_count = sum(1 for lnk in links_enriquecidos if lnk["status"] in ("degraded", "warn"))
    nodata_count = sum(1 for lnk in links_enriquecidos if lnk["status"] == "nodata")

    return {
        "links": links_enriquecidos,
        "total": total,
        "up": up_count,
        "down": down_count,
        "degraded": degraded_count,
        "nodata": nodata_count,
        "all_ok": total > 0 and down_count == 0 and degraded_count == 0,
        "has_zabbix": bool(os.getenv("ZABBIX_URL")),
    }


def get_cached_links() -> dict:
    """Retorna dados de links WAN com cache TTL 60s."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("links_manager.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("links_manager.cache.miss")
    try:
        dados = _buscar_dados()
    except Exception as exc:
        log.warning("links_manager.busca_falhou", erro=str(exc))
        dados = {
            "links": [],
            "total": 0,
            "up": 0,
            "down": 0,
            "degraded": 0,
            "nodata": 0,
            "all_ok": False,
            "has_zabbix": False,
            "_erro": str(exc),
        }
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados


def invalidar_cache() -> None:
    global _cache_ts
    with _lock:
        _cache_ts = 0.0
