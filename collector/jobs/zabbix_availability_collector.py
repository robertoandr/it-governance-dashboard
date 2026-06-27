"""Coletor de Disponibilidade Zabbix — escreve gov_zabbix_disponibilidade no bucket governance_raw.

Métricas coletadas:
  gov_zabbix_disponibilidade
    fields: score, uptime_pct, hosts_up, hosts_down, hosts_unknown, total_monitorado
    field:  top_host_down (string — nome do host crítico offline, ou "—")

### Por que NÃO usamos interface.available

Este ambiente usa Zabbix Active Agent: o agente chama home, não o server que conecta.
Nesse modelo interface.available = 0 (UNKNOWN) é o estado normal de todos os hosts —
não significa offline. Usar interface.available produziria 0% de uptime erroneamente.

### Abordagem adotada: trigger-based

Hosts DOWN = hosts com trigger "unavailable" em estado PROBLEM (value=1) e não suprimidos.
"ICMP Ping: Unavailable by ICMP ping" é o sinal canônico de host offline neste Zabbix.
Suprimidos são excluídos: manutenção programada não deve penalizar o score.

Score = uptime_pct (base linear). Gancho para decaimento por criticidade comentado abaixo.

Auth: Bearer token no header Authorization (Zabbix 7.0+ — idêntico ao zabbix_risk_collector).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings

log = structlog.get_logger("zabbix_availability_collector")

_MEASUREMENT = "gov_zabbix_disponibilidade"

# GANCHO PARA CRITICIDADE (deixar pronto, descomentado quando configurar a tag):
# CRITICIDADE_TAG = settings.ZABBIX_CRIT_TAG  # ex: "criticidade=alta"
# Quando configurado: trigger.get adicional filtrando por tag no host.
# Hosts críticos down aplicam decaimento extra: score *= (1 - 0.15) ** n_criticos_down


def _zbx(method: str, params: dict[str, Any]) -> Any:
    """Chama a API JSON-RPC do Zabbix 7.0 com Bearer token no header."""
    url = settings.ZABBIX_URL
    if not url.endswith("/api_jsonrpc.php"):
        url = url.rstrip("/") + "/api_jsonrpc.php"

    resp = requests.post(
        url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {settings.ZABBIX_TOKEN}",
        },
        timeout=15,
        verify=settings.ZBX_VERIFY_TLS,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API: {data['error']}")
    return data["result"]


def _coletar_disponibilidade() -> dict[str, Any]:
    """Coleta hosts monitorados e identifica quais estão DOWN via trigger-based approach.

    Hosts DOWN = hosts com trigger contendo 'unavailable' em PROBLEM e não suprimidos.
    Hosts UNKNOWN = 0 neste modelo (Active Agent — interface.available sempre 0).
    """
    # Total de hosts monitorados (status=0 = habilitados)
    hosts_total_raw = _zbx(
        "host.get",
        {
            "output": ["hostid"],
            "filter": {"status": 0},
            "countOutput": True,
        },
    )
    # countOutput retorna string em Zabbix 7.0
    total_monitorado = int(hosts_total_raw) if isinstance(hosts_total_raw, (str, int)) else len(hosts_total_raw)

    # Hosts DOWN: triggers de indisponibilidade em PROBLEM, não suprimidos
    triggers_down = _zbx(
        "trigger.get",
        {
            "output": ["triggerid", "description", "priority"],
            "search": {"description": "unavailable"},
            "filter": {"value": 1},  # value=1 → estado PROBLEM
            "monitored": True,
            "suppressed": False,  # exclui janelas de manutenção
            "selectHosts": ["hostid", "name"],
        },
    )

    # Deduplica: um host pode ter múltiplos triggers de indisponibilidade
    hosts_down: dict[str, str] = {}
    top_priority = -1
    top_host_down = "—"

    for trigger in triggers_down:
        prio = int(trigger.get("priority", 0))
        for host in trigger.get("hosts", []):
            hid = host["hostid"]
            if hid not in hosts_down:
                hosts_down[hid] = host["name"]
            # top_host_down = host do trigger de maior prioridade
            if prio > top_priority:
                top_priority = prio
                top_host_down = host["name"]

    hosts_down_count = len(hosts_down)
    hosts_up_count = max(0, total_monitorado - hosts_down_count)
    uptime_pct = round(hosts_up_count / total_monitorado * 100, 2) if total_monitorado > 0 else 100.0

    # Score = uptime_pct (linear). Gancho para decaimento comentado acima.
    score = uptime_pct

    return {
        "score": round(score, 2),
        "uptime_pct": uptime_pct,
        "hosts_up": hosts_up_count,
        "hosts_down": hosts_down_count,
        "hosts_unknown": 0,
        "total_monitorado": total_monitorado,
        "top_host_down": top_host_down,
    }


def run() -> None:
    """Entry point para o APScheduler."""
    log.info("zabbix_availability_coleta_iniciada")

    if not settings.ZABBIX_URL or not settings.ZABBIX_TOKEN:
        log.warning("zabbix_availability_ignorado", motivo="ZABBIX_URL ou ZABBIX_TOKEN não configurados")
        return

    try:
        resultado = _coletar_disponibilidade()
    except Exception as exc:
        log.error("zabbix_availability_coleta_falhou", erro=str(exc))
        return

    log.info("zabbix_availability_metricas_calculadas", **resultado)

    ponto = (
        Point(_MEASUREMENT)
        .field("score", float(resultado["score"]))
        .field("uptime_pct", float(resultado["uptime_pct"]))
        .field("hosts_up", int(resultado["hosts_up"]))
        .field("hosts_down", int(resultado["hosts_down"]))
        .field("hosts_unknown", int(resultado["hosts_unknown"]))
        .field("total_monitorado", int(resultado["total_monitorado"]))
        .field("top_host_down", resultado["top_host_down"])
        .time(datetime.now(UTC), WritePrecision.S)
    )

    try:
        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=settings.INFLUX_BUCKET_RAW,
                record=ponto,
            )
        log.info(
            "zabbix_availability_escrito_influxdb",
            measurement=_MEASUREMENT,
            score=resultado["score"],
            uptime_pct=resultado["uptime_pct"],
        )
    except Exception as exc:
        log.error("zabbix_availability_escrita_falhou", erro=str(exc))
