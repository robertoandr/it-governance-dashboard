"""Monitor de disponibilidade de links de saida via ICMP ping.

Executa 5 pacotes por IP a cada 60s. Interpreta a saida do ping system
para extrair RTT medio e percentual de perda — sem dependencias extras.

Measurement: link_monitor
  tags:   ip, link_name, site
  fields: reachable (int 0/1), rtt_ms (float), packet_loss (float)

Faixas de alerta:
  OK      → reachable=1, loss=0%,     rtt < 80ms
  ATENCAO → loss 1-5%  OU rtt 80-150ms          → log.warning
  CRITICO → reachable=0 OU loss >5%   OU rtt>150ms → log.error
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

log = structlog.get_logger("link_monitor")

_PING_COUNT = 5
_PING_TIMEOUT = 2  # segundos por pacote

# Regex para "rtt min/avg/max/mdev = 0.186/0.400/0.918/0.263 ms"
_RTT_RE = re.compile(r"rtt\s+\S+\s*=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+\s+ms")
# Regex para "5 packets transmitted, 5 received, 0% packet loss"
_LOSS_RE = re.compile(r"(\d+(?:\.\d+)?)%\s+packet\s+loss")

# Thresholds
_RTT_WARN = 80.0
_RTT_CRIT = 150.0
_LOSS_WARN = 1.0
_LOSS_CRIT = 5.0


def _ping(ip: str) -> tuple[int, float, float]:
    """Executa ping e retorna (reachable, rtt_ms, packet_loss_pct).

    reachable=0 quando timeout total ou falha de rede.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["ping", "-c", str(_PING_COUNT), "-W", str(_PING_TIMEOUT), ip],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_PING_COUNT * (_PING_TIMEOUT + 1) + 5,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        log.warning("ping_timeout_global", ip=ip)
        return 0, 0.0, 100.0
    except Exception as exc:
        log.error("ping_erro", ip=ip, erro=str(exc))
        return 0, 0.0, 100.0

    loss_match = _LOSS_RE.search(output)
    packet_loss = float(loss_match.group(1)) if loss_match else 100.0

    rtt_match = _RTT_RE.search(output)
    rtt_ms = float(rtt_match.group(1)) if rtt_match else 0.0

    # Considera reachable apenas se pelo menos 1 pacote retornou
    reachable = 1 if (packet_loss < 100.0 and rtt_ms > 0) else 0

    return reachable, rtt_ms, packet_loss


def _avaliar(link_name: str, ip: str, reachable: int, rtt_ms: float, loss: float) -> None:
    """Loga o estado do link na faixa correta."""
    ctx = {"link": link_name, "ip": ip, "reachable": reachable, "rtt_ms": round(rtt_ms, 2), "loss_pct": loss}

    if reachable == 0 or loss > _LOSS_CRIT or rtt_ms > _RTT_CRIT:
        log.error("link_critico", **ctx)
    elif loss > _LOSS_WARN or rtt_ms > _RTT_WARN:
        log.warning("link_atencao", **ctx)
    else:
        log.info("link_ok", **ctx)


def run() -> None:
    links_raw = settings.LINK_MONITOR_TARGETS
    if not links_raw:
        log.warning("link_monitor_ignorado", motivo="LINK_MONITOR_TARGETS nao configurado")
        return

    ts = datetime.now(UTC)
    pontos: list[Point] = []

    for entry in links_raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        # Formato: "ip,link_name,site"
        parts = [p.strip() for p in entry.split(",")]
        if len(parts) != 3:
            log.warning("link_monitor_entrada_invalida", entrada=entry)
            continue
        ip, link_name, site = parts

        reachable, rtt_ms, packet_loss = _ping(ip)
        _avaliar(link_name, ip, reachable, rtt_ms, packet_loss)

        pontos.append(
            Point("link_monitor")
            .tag("ip", ip)
            .tag("link_name", link_name)
            .tag("site", site)
            .field("reachable", reachable)
            .field("rtt_ms", float(rtt_ms))
            .field("packet_loss", float(packet_loss))
            .time(ts, WritePrecision.S)
        )

    if not pontos:
        log.warning("link_monitor_sem_pontos")
        return

    with InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    ) as client:
        client.write_api(write_options=SYNCHRONOUS).write(
            bucket=settings.INFLUX_BUCKET_RAW,
            record=pontos,
        )

    log.info("link_monitor_gravado", links=len(pontos))
