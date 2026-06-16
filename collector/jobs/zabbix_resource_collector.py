"""Coletor de utilização de recursos Zabbix — escreve gov_zabbix_resource no InfluxDB.

Coleta a utilização média de CPU e memória de todos os hosts monitorados
via Zabbix JSON-RPC e persiste um ponto agregado a cada execução.

Schema do measurement:
  measurement: gov_zabbix_resource
  tags:        (nenhuma — dado agregado por execução)
  fields:      hosts_with_data=<int>   número de hosts com dado válido
               cpu_avg_pct=<float>     média de system.cpu.util (%)
               mem_avg_pct=<float>     média de vm.memory.utilization (%)
  timestamp:   UTC no momento da coleta

Chaves Zabbix buscadas (templates Linux/Windows by Zabbix Agent):
  CPU:      system.cpu.util
  Memória:  vm.memory.utilization
            vm.memory.size[pavailable]  (fallback — % disponível → invertido)
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

log = structlog.get_logger("zabbix_resource_collector")

_ZABBIX_RPC_PATH = "/api_jsonrpc.php"
_CPU_KEY = "system.cpu.util"
# Chaves de memória em ordem de preferência
_MEM_KEYS = ["vm.memory.utilization", "vm.memory.size[pavailable]"]


def _normalize_url(base: str) -> str:
    url = base.rstrip("/")
    if not url.endswith(_ZABBIX_RPC_PATH):
        url += _ZABBIX_RPC_PATH
    return url


class _ZabbixClient:
    """Cliente JSON-RPC mínimo para o coletor de recursos."""

    def __init__(self, url: str, user: str, password: str) -> None:
        self._url = _normalize_url(url)
        self._user = user
        self._password = password
        self._token: str | None = None

    def _call(self, method: str, params: dict[str, Any], use_auth: bool = True) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        if use_auth:
            body["auth"] = self._auth()
        resp = requests.post(self._url, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            detail = data["error"].get("data", "").lower()
            if use_auth and ("re-login" in detail or "session" in detail):
                self._token = None
                body["auth"] = self._auth()
                resp = requests.post(self._url, json=body, timeout=15)
                data = resp.json()
            if "error" in data:
                raise RuntimeError(f"Erro Zabbix API: {data['error']}")
        return data.get("result", {})

    def _auth(self) -> str:
        if self._token:
            return self._token
        result = self._call(
            "user.login",
            {"username": self._user, "password": self._password},
            use_auth=False,
        )
        token = result if isinstance(result, str) else (result.get("token") if isinstance(result, dict) else None)
        if not token:
            raise RuntimeError("Falha na autenticação Zabbix")
        self._token = token
        return self._token

    def get_resource_items(self) -> list[dict[str, Any]]:
        """Retorna todos os itens de CPU e memória de hosts monitorados."""
        return self._call(
            "item.get",
            {
                "output": ["itemid", "key_", "lastvalue", "lastclock", "value_type"],
                "search": {"key_": _CPU_KEY},
                "searchByAny": False,
                "monitored": True,
                "filter": {"status": 0},  # 0 = habilitado
            },
        )

    def get_memory_items(self) -> list[dict[str, Any]]:
        """Retorna itens de memória de hosts monitorados (testa cada chave)."""
        for key in _MEM_KEYS:
            items = self._call(
                "item.get",
                {
                    "output": ["itemid", "key_", "lastvalue", "lastclock", "value_type"],
                    "search": {"key_": key},
                    "searchByAny": False,
                    "monitored": True,
                    "filter": {"status": 0},
                },
            )
            if items:
                log.debug("chave_memoria_selecionada", key=key, count=len(items))
                return items, key
        return [], ""


def _parse_float(raw: str | None) -> float | None:
    """Converte lastvalue string para float; retorna None se inválido."""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _compute_averages(
    cpu_items: list[dict[str, Any]],
    mem_items: list[dict[str, Any]],
    mem_key: str,
) -> tuple[float, float, int]:
    """Calcula médias de CPU e memória a partir dos itens Zabbix.

    Para vm.memory.size[pavailable] o valor é % disponível, então invertemos
    para obter % utilizado (100 - pavailable).

    Returns:
        (cpu_avg_pct, mem_avg_pct, hosts_with_data)
    """
    cpu_values = [v for item in cpu_items if (v := _parse_float(item.get("lastvalue"))) is not None]

    inverted = mem_key == "vm.memory.size[pavailable]"
    mem_values = []
    for item in mem_items:
        v = _parse_float(item.get("lastvalue"))
        if v is None:
            continue
        mem_values.append(100.0 - v if inverted else v)

    cpu_avg = round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else 0.0
    mem_avg = round(sum(mem_values) / len(mem_values), 2) if mem_values else 0.0
    hosts_with_data = max(len(cpu_values), len(mem_values))

    return cpu_avg, mem_avg, hosts_with_data


def run() -> None:
    """Ponto de entrada do coletor — chamado pelo APScheduler."""
    log.info("zabbix_resource_coleta_iniciada")

    zbx = _ZabbixClient(
        url=settings.ZABBIX_URL,
        user=settings.ZABBIX_USER,
        password=settings.ZABBIX_PASSWORD,
    )

    try:
        cpu_items = zbx.get_resource_items()
        mem_items, mem_key = zbx.get_memory_items()
    except Exception as exc:
        log.error("zabbix_resource_coleta_falhou", error=str(exc))
        return

    if not cpu_items and not mem_items:
        log.warning("zabbix_resource_sem_dados", motivo="nenhum item retornado pela API")
        return

    cpu_avg, mem_avg, hosts_with_data = _compute_averages(cpu_items, mem_items, mem_key)

    log.info(
        "zabbix_resource_metricas_calculadas",
        cpu_avg_pct=cpu_avg,
        mem_avg_pct=mem_avg,
        hosts_with_data=hosts_with_data,
    )

    ponto = (
        Point("gov_zabbix_resource")
        .field("cpu_avg_pct", cpu_avg)
        .field("mem_avg_pct", mem_avg)
        .field("hosts_with_data", hosts_with_data)
        .time(datetime.now(UTC), WritePrecision.SECONDS)
    )

    try:
        client = InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        )
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=settings.INFLUX_BUCKET_RAW, record=ponto)
        client.close()
        log.info("zabbix_resource_escrito_influxdb", bucket=settings.INFLUX_BUCKET_RAW)
    except Exception as exc:
        log.error("zabbix_resource_escrita_influxdb_falhou", error=str(exc))
