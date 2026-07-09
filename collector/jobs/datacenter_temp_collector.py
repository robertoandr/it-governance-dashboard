"""Coletor de Temperatura do Datacenter (sensor Nextcon DSB WIFI via ThingSpeak).

Fonte: API pública do ThingSpeak (sem autenticação), canal do sensor
instalado no datacenter. O sensor atualiza o feed a cada ~2.5min; a coleta
roda a cada 5min (ver registro em collector/main.py).

Escreve gov_thingspeak_temperatura no bucket governance_raw.
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

log = structlog.get_logger("datacenter_temp_collector")

_MEASUREMENT = "gov_thingspeak_temperatura"
_THINGSPEAK_TIMEOUT_S = 10


def _buscar_feed() -> dict[str, Any] | None:
    """Busca o feed mais recente do canal ThingSpeak.

    Retorna None em caso de falha de rede/HTTP ou canal sem feeds — nunca
    levanta exceção (ver regra de tratamento de falha de rede não fatal).
    """
    url = f"https://api.thingspeak.com/channels/{settings.NEXTCON_CHANNEL_ID}/feeds.json"
    try:
        resp = requests.get(url, params={"results": 1}, timeout=_THINGSPEAK_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("datacenter_temp_requisicao_falhou", erro=str(exc))
        return None
    except ValueError as exc:  # JSON inválido
        log.warning("datacenter_temp_resposta_invalida", erro=str(exc))
        return None

    feeds = data.get("feeds") or []
    if not feeds:
        log.warning("datacenter_temp_sem_feeds", channel_id=settings.NEXTCON_CHANNEL_ID)
        return None
    return feeds[-1]


def _parse_feed(feed: dict[str, Any]) -> dict[str, Any] | None:
    """Extrai e valida os campos numéricos do feed do ThingSpeak.

    Retorna None se algum campo obrigatório estiver ausente ou não for numérico.
    """
    try:
        temp_atual = float(feed["field1"])
        temp_min = float(feed["field2"])
        temp_max = float(feed["field3"])
        uptime_min = float(feed["field4"])
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("datacenter_temp_feed_invalido", erro=str(exc), feed=feed)
        return None

    timestamp = datetime.now(UTC)
    created_at = feed.get("created_at")
    if created_at:
        try:
            timestamp = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            log.warning("datacenter_temp_created_at_invalido", created_at=created_at)

    return {
        "temp_atual": temp_atual,
        "temp_min": temp_min,
        "temp_max": temp_max,
        "uptime_min": uptime_min,
        "timestamp": timestamp,
    }


def _escrever_influx(dados: dict[str, Any]) -> None:
    ponto = (
        Point(_MEASUREMENT)
        .tag("sensor", f"DSB-WIFI-{settings.NEXTCON_CHANNEL_ID}")
        .tag("local", "datacenter")
        .field("temp_atual", dados["temp_atual"])
        .field("temp_min", dados["temp_min"])
        .field("temp_max", dados["temp_max"])
        .field("uptime_min", dados["uptime_min"])
        .time(dados["timestamp"], WritePrecision.S)
    )

    with InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    ) as client:
        client.write_api(write_options=SYNCHRONOUS).write(
            bucket=settings.INFLUX_BUCKET_RAW,
            record=ponto,
        )


def collect() -> None:
    """Entry point: coleta o feed do ThingSpeak e grava no InfluxDB."""
    log.info("datacenter_temp_coleta_iniciada")

    if not settings.NEXTCON_CHANNEL_ID:
        log.warning("datacenter_temp_ignorado", motivo="NEXTCON_CHANNEL_ID não configurado")
        return

    feed = _buscar_feed()
    if feed is None:
        return

    dados = _parse_feed(feed)
    if dados is None:
        return

    try:
        _escrever_influx(dados)
    except Exception as exc:
        log.error("datacenter_temp_escrita_falhou", erro=str(exc))
        return

    log.info(
        "datacenter_temp_escrito_influxdb",
        measurement=_MEASUREMENT,
        temp_atual=dados["temp_atual"],
        temp_min=dados["temp_min"],
        temp_max=dados["temp_max"],
    )
