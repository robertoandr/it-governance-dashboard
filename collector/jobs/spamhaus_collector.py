"""Spamhaus reputation collector — verifica IPs de saida corporativos via DNS.

Metodo: DNSBL lookup em zen.spamhaus.org (ZEN = SBL + XBL + PBL combinados).
  - NXDOMAIN           → limpo (listed=0, list_hit="clean")
  - 127.0.0.2          → SBL (Spamhaus Block List — spam direto)
  - 127.0.0.3          → CSS (Content/spam source)
  - 127.0.0.4-7        → XBL (Exploits — botnets, proxies)
  - 127.0.0.10-11      → PBL (Policy — IP residencial/dial-up)

Measurement: spamhaus_reputation
  tags:   ip, list_hit
  fields: listed (0/1), return_code (string "127.0.0.x" ou "clean")
"""

from __future__ import annotations

import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

log = structlog.get_logger("spamhaus_collector")

_ZEN_SUFFIX = "zen.spamhaus.org"

_CODE_MAP: dict[str, str] = {
    "127.0.0.2": "sbl",
    "127.0.0.3": "css",
    "127.0.0.4": "xbl",
    "127.0.0.5": "xbl",
    "127.0.0.6": "xbl",
    "127.0.0.7": "xbl",
    "127.0.0.10": "pbl",
    "127.0.0.11": "pbl",
}


def _lookup(ip: str) -> tuple[int, str, str]:
    """Consulta Spamhaus ZEN para um IP.

    Returns:
        (listed, list_hit, return_code)
        listed:      0 = limpo, 1 = listado
        list_hit:    "clean" | "sbl" | "css" | "xbl" | "pbl" | "unknown"
        return_code: "clean" | "127.0.0.x"
    """
    reversed_ip = ".".join(reversed(ip.split(".")))
    query = f"{reversed_ip}.{_ZEN_SUFFIX}"
    try:
        results = socket.getaddrinfo(query, None, socket.AF_INET)
        addrs = [r[4][0] for r in results]
        # Pega o primeiro codigo retornado
        code = addrs[0] if addrs else "unknown"
        list_hit = _CODE_MAP.get(code, "unknown")
        return 1, list_hit, code
    except socket.gaierror:
        # NXDOMAIN = limpo
        return 0, "clean", "clean"


def run() -> None:
    ips: list[str] = [ip.strip() for ip in settings.SPAMHAUS_IPS.split(",") if ip.strip()]
    if not ips:
        log.warning("spamhaus_job_ignorado", motivo="SPAMHAUS_IPS nao configurado")
        return

    log.info("spamhaus_coleta_iniciada", ips=ips)
    ts = datetime.now(UTC)
    pontos: list[Point] = []

    for ip in ips:
        listed, list_hit, return_code = _lookup(ip)

        if listed:
            log.warning(
                "spamhaus_ip_listado",
                ip=ip,
                list_hit=list_hit,
                return_code=return_code,
            )
        else:
            log.info("spamhaus_ip_limpo", ip=ip)

        pontos.append(
            Point("spamhaus_reputation")
            .tag("ip", ip)
            .tag("list_hit", list_hit)
            .field("listed", listed)
            .field("return_code", return_code)
            .time(ts, WritePrecision.S)
        )

    with InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    ) as client:
        client.write_api(write_options=SYNCHRONOUS).write(
            bucket=settings.INFLUX_BUCKET_RAW,
            record=pontos,
        )

    log.info(
        "spamhaus_coleta_concluida",
        ips_verificados=len(ips),
        listados=sum(1 for p in pontos if p._fields.get("listed") == 1),
    )
