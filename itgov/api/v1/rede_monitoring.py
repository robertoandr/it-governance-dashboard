"""Dados de Rede para o dashboard.

Combina:
- InfluxDB: gov_infra_assets (totais por categoria do último nmap scan)
             gov_infra_asset_detail (um ponto por IP descoberto)
- Zabbix API: discovery rules (drules) — configuração e status

Cache TTL 5min (scan muda no máximo a cada hora).
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
_lock = threading.Lock()
_cache_data: dict | None = None
_cache_ts: float = 0.0


def _cache_valido() -> bool:
    return _cache_data is not None and (time.monotonic() - _cache_ts) < _CACHE_TTL


# ── Zabbix ────────────────────────────────────────────────────────────────────


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
        headers={"Content-Type": "application/json-rpc", "Authorization": f"Bearer {token}"},
        timeout=15,
        verify=False,  # nosec B501 # noqa: S501
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


_CHECK_TYPES = {
    "0": "SSH",
    "1": "LDAP",
    "2": "SMTP",
    "3": "FTP",
    "4": "HTTP",
    "5": "POP",
    "6": "NNTP",
    "7": "IMAP",
    "8": "TCP",
    "9": "Zabbix agent",
    "10": "SNMPv1",
    "11": "SNMPv2c",
    "12": "ICMP ping",
    "13": "SNMPv3",
    "14": "HTTPS",
    "15": "Telnet",
}


def _buscar_drules() -> list[dict]:
    """Retorna discovery rules com checks e status."""
    try:
        drules = _zbx(
            "drule.get",
            {
                "output": ["druleid", "name", "status", "iprange", "delay", "nextcheck"],
                "selectDChecks": ["type", "ports", "key_", "snmp_community"],
            },
        )
        result = []
        for dr in drules:
            checks = []
            for c in dr.get("dchecks", []):
                label = _CHECK_TYPES.get(str(c.get("type", "")), f"type={c.get('type')}")
                port = c.get("ports", "0")
                if port and port != "0":
                    label = f"{label}:{port}"
                checks.append(label)

            ranges = [r.strip() for r in dr.get("iprange", "").replace("\r\n", "\n").split(",") if r.strip()]
            nextcheck = int(dr.get("nextcheck", 0) or 0)
            result.append(
                {
                    "id": dr["druleid"],
                    "name": dr["name"],
                    "enabled": dr.get("status") == "0",
                    "ranges": ranges,
                    "delay": dr.get("delay", "—"),
                    "checks": checks,
                    "nextcheck": nextcheck,
                }
            )
        return result
    except Exception as exc:
        log.warning("rede_monitoring.drules_falhou", erro=str(exc))
        return []


# ── InfluxDB ──────────────────────────────────────────────────────────────────


def _query_influx(flux: str) -> list[dict[str, Any]]:
    from app.services.influxdb_provider import InfluxDBMetricsProvider

    return InfluxDBMetricsProvider()._query(flux)


def _ler_assets_influx() -> dict:
    """Lê gov_infra_assets (summary) e gov_infra_asset_detail (por host) do InfluxDB."""
    from app.config import get_settings

    bucket = get_settings().influx.bucket_raw

    # Último ponto de resumo da varredura
    rows_sum = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_infra_assets" and r.scan == "summary")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
""")

    # toString() antes do pivot evita schema collision entre campos string e int
    rows_hosts = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_infra_asset_detail")
  |> group(columns: ["ip", "_field"])
  |> last()
  |> map(fn: (r) => ({{r with _value: string(v: r._value)}}))
  |> group(columns: ["ip", "category", "os_guess"])
  |> pivot(rowKey: ["_time", "ip", "category", "os_guess"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
""")

    # Histórico de totais (para sparkline)
    rows_hist = _query_influx(f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "gov_infra_assets" and r.scan == "summary" and r._field == "total_descobertos")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
""")

    s = rows_sum[-1] if rows_sum else {}

    def _int(v: object) -> int:
        try:
            return int(str(v)) if v is not None else 0
        except (ValueError, TypeError):
            return 0

    hosts: list[dict] = []
    for row in rows_hosts:
        hosts.append(
            {
                "ip": row.get("ip", row.get("_field", "?")),
                "category": row.get("category", "Outros"),
                "hostname": str(row.get("hostname", "")),
                "vendor": str(row.get("vendor", "")),
                "os_guess": row.get("os_guess", ""),
                "open_ports": _int(row.get("open_ports_count")),
                "has_agent": _int(row.get("has_agent")) == 1,
                "has_snmp": _int(row.get("has_snmp")) == 1,
                "has_ssh": _int(row.get("has_ssh")) == 1,
                "scan_time": str(row.get("_time", "")),
            }
        )

    hist = [int(r.get("_value", 0) or 0) for r in rows_hist]

    last_scan = s.get("_time", "")

    return {
        "has_data": bool(s),
        "last_scan": str(last_scan)[:19].replace("T", " ") if last_scan else None,
        "total": int(s.get("total_descobertos", 0) or 0),
        "linux": int(s.get("servidores_linux", 0) or 0),
        "windows": int(s.get("servidores_windows", 0) or 0),
        "rede": int(s.get("dispositivos_rede", 0) or 0),
        "databases": int(s.get("bancos_de_dados", 0) or 0),
        "web": int(s.get("aplicacoes_web", 0) or 0),
        "genericos": int(s.get("hosts_genericos", 0) or 0),
        "com_agente": sum(1 for h in hosts if h["has_agent"]),
        "com_snmp": sum(1 for h in hosts if h["has_snmp"]),
        "hosts": sorted(hosts, key=lambda x: (x["category"], x["ip"])),
        "history": hist,
    }


# ── Montagem final ─────────────────────────────────────────────────────────────


def _buscar_rede() -> dict:
    influx = _ler_assets_influx()
    drules = _buscar_drules()

    # Ranges únicos varridos
    all_ranges: list[str] = []
    for dr in drules:
        for r in dr["ranges"]:
            if r not in all_ranges:
                all_ranges.append(r)

    # Conta ranges ativos (drule habilitada)
    active_drules = [dr for dr in drules if dr["enabled"]]

    return {
        "enabled": True,
        "influx": influx,
        "drules": drules,
        "active_drules": active_drules,
        "scan_ranges": all_ranges,
    }


def get_cached_rede_summary() -> dict:
    """Retorna dados de rede com cache TTL 5min."""
    global _cache_data, _cache_ts
    with _lock:
        if _cache_valido():
            log.debug("rede_monitoring.cache.hit")
            return _cache_data  # type: ignore[return-value]

    log.info("rede_monitoring.cache.miss")
    try:
        dados = _buscar_rede()
    except Exception as exc:
        log.warning("rede_monitoring.busca_falhou", erro=str(exc))
        dados = {
            "enabled": False,
            "influx": {"has_data": False, "total": 0, "hosts": [], "history": []},
            "drules": [],
            "active_drules": [],
            "scan_ranges": [],
            "_erro": str(exc),
        }
    with _lock:
        _cache_data = dados
        _cache_ts = time.monotonic()
    return dados
