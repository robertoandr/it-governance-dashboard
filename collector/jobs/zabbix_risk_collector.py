"""Coletor de Riscos Zabbix — escreve gov_zabbix_riscos e gov_zabbix_summary no bucket governance_raw.

Métricas coletadas via problem.get (Zabbix 7.0 LTS):
  gov_zabbix_riscos
    fields: score, incidentes_criticos, criticos, altos, medios, avisos, total_problemas
    field:  top_incidente (nome do problema de maior severidade ativa)

  gov_zabbix_summary  (adaptador para o NOC Flask — lido por influxdb_provider._zabbix_stats)
    fields: hosts_total, problems_disaster, problems_high, problems_average, problems_warning
    Todos int, sem tags — pivot() no Flux transforma _field em coluna.

Score: decaimento multiplicativo por severidade — 100 sem problemas, ~0 em colapso.
  Fatores: crítico×0.25 · alto×0.08 · médio×0.03 · aviso×0.008
  Exemplo real (1C+9A+6M+10Av): score ≈ 27.2 → 🔴 Gestão de Riscos

Auth: Bearer token no header Authorization (Zabbix 7.0+ — NÃO usa "auth" no body).
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

log = structlog.get_logger("zabbix_risk_collector")

# Fatores de decaimento multiplicativo — score *= (1 - fator)^n por severidade
_FATORES: dict[int, float] = {5: 0.25, 4: 0.08, 3: 0.03, 2: 0.008, 1: 0.0, 0: 0.0}

_MEASUREMENT = "gov_zabbix_riscos"
_MEASUREMENT_SUMMARY = "gov_zabbix_summary"


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


def _zbx_hosts_total() -> int:
    """Retorna o total de hosts monitorados (status=0). countOutput retorna string no Zabbix 7.0."""
    raw = _zbx("host.get", {"output": ["hostid"], "filter": {"status": 0}, "countOutput": True})
    return int(raw) if isinstance(raw, (str, int)) else len(raw)


def _coletar_problemas() -> list[dict[str, Any]]:
    """Retorna todos os problemas ativos não suprimidos, do mais grave ao mais leve."""
    problemas = _zbx(
        "problem.get",
        {
            "output": ["eventid", "name", "severity", "clock"],
            "recent": False,
            "suppressed": False,
        },
    )
    # Ordena localmente: severity DESC, clock DESC (sortfield em 7.0 só aceita "eventid")
    return sorted(problemas, key=lambda p: (int(p["severity"]), int(p["clock"])), reverse=True)


def _calcular_score(problemas: list[dict[str, Any]]) -> dict[str, Any]:
    """Aplica decaimento multiplicativo e retorna métricas consolidadas."""
    contagem: dict[int, int] = dict.fromkeys(range(6), 0)
    for p in problemas:
        contagem[int(p["severity"])] += 1

    score = 100.0
    for sev, fator in _FATORES.items():
        if fator > 0 and contagem[sev] > 0:
            score *= (1.0 - fator) ** contagem[sev]

    return {
        "score": round(score, 1),
        "incidentes_criticos": contagem[5] + contagem[4],
        "criticos": contagem[5],
        "altos": contagem[4],
        "medios": contagem[3],
        "avisos": contagem[2],
        "total_problemas": sum(contagem.values()),
        "top_incidente": problemas[0]["name"] if problemas else "Nenhum",
    }


def run() -> None:
    """Entry point para o APScheduler."""
    log.info("zabbix_risk_coleta_iniciada")

    if not settings.ZABBIX_URL or not settings.ZABBIX_TOKEN:
        log.warning("zabbix_risk_ignorado", motivo="ZABBIX_URL ou ZABBIX_TOKEN não configurados")
        return

    try:
        problemas = _coletar_problemas()
        resultado = _calcular_score(problemas)
        hosts_total = _zbx_hosts_total()
    except Exception as exc:
        log.error("zabbix_risk_coleta_falhou", erro=str(exc))
        return

    log.info("zabbix_risk_metricas_calculadas", **resultado, hosts_total=hosts_total)

    coletado_em = datetime.now(UTC)

    ponto_riscos = (
        Point(_MEASUREMENT)
        .field("score", float(resultado["score"]))
        .field("incidentes_criticos", int(resultado["incidentes_criticos"]))
        .field("criticos", int(resultado["criticos"]))
        .field("altos", int(resultado["altos"]))
        .field("medios", int(resultado["medios"]))
        .field("avisos", int(resultado["avisos"]))
        .field("total_problemas", int(resultado["total_problemas"]))
        .field("top_incidente", resultado["top_incidente"])
        .time(coletado_em, WritePrecision.S)
    )

    # Adaptador NOC: gov_zabbix_summary é lido pelo Flask (influxdb_provider._zabbix_stats).
    # Campos e tipos devem corresponder EXATAMENTE ao que o pivot() do Flux expõe.
    ponto_summary = (
        Point(_MEASUREMENT_SUMMARY)
        .field("hosts_total", int(hosts_total))
        .field("problems_disaster", int(resultado["criticos"]))  # severity=5
        .field("problems_high", int(resultado["altos"]))  # severity=4
        .field("problems_average", int(resultado["medios"]))  # severity=3
        .field("problems_warning", int(resultado["avisos"]))  # severity=2
        .time(coletado_em, WritePrecision.S)
    )

    try:
        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            write_api = client.write_api(write_options=SYNCHRONOUS)
            write_api.write(bucket=settings.INFLUX_BUCKET_RAW, record=ponto_riscos)
            write_api.write(bucket=settings.INFLUX_BUCKET_RAW, record=ponto_summary)
        log.info(
            "zabbix_risk_escrito_influxdb",
            measurement=_MEASUREMENT,
            measurement_summary=_MEASUREMENT_SUMMARY,
            score=resultado["score"],
            hosts_total=hosts_total,
        )
    except Exception as exc:
        log.error("zabbix_risk_escrita_falhou", erro=str(exc))
