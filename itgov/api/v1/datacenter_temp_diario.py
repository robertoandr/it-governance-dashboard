"""Histórico diário da temperatura do datacenter (sensor Nextcon DSB WIFI).

Lê as leituras brutas direto da API pública do ThingSpeak (field1 =
temperatura, uma leitura a cada ~2.5min) e consolida por dia no fuso de
Brasília: média, mínima e máxima com o horário, leituras acima do limite e
cobertura (leituras recebidas / esperadas).

Não usa o InfluxDB: o coletor só grava dali para frente, e o ThingSpeak guarda
o histórico completo do canal. Os campos field2/field3 (mín/máx) do sensor são
acumulados do próprio aparelho e não servem como mín/máx do dia.

Cache TTL 30min — são ~3 requisições (~700KB) para 30 dias, por isso a página
busca estes dados via JSON depois de carregar, e não no render.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
import structlog

log = structlog.get_logger(__name__)

_CACHE_TTL = 1800  # 30 minutos
_TZ = ZoneInfo("America/Sao_Paulo")
_API = "https://api.thingspeak.com/channels/{canal}/fields/1.json"
_JANELA_DIAS = 10  # ThingSpeak devolve no máximo 8000 leituras por chamada (~13 dias)
_LEITURAS_ESPERADAS_DIA = 576  # sensor publica a cada 2.5min

# Mesmo limite da trigger "Datacenter: temperatura acima de 22°C" no Zabbix
# (infra-setup/04_create_datacenter_temp_sensor.py).
LIMITE_C = 22.0

_lock = threading.Lock()
_cache: dict[int, tuple[float, dict]] = {}


def _buscar_leituras(canal: str, inicio: datetime, fim: datetime) -> list[tuple[datetime, float]]:
    """Leituras (UTC, °C) entre inicio e fim, em janelas de _JANELA_DIAS."""
    leituras: list[tuple[datetime, float]] = []
    cursor = inicio
    while cursor < fim:
        ate = min(cursor + timedelta(days=_JANELA_DIAS), fim)
        resp = requests.get(
            _API.format(canal=canal),
            params={
                "start": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                "end": ate.strftime("%Y-%m-%d %H:%M:%S"),
                "results": 8000,
            },
            timeout=20,
        )
        resp.raise_for_status()
        for feed in resp.json().get("feeds", []):
            valor = feed.get("field1")
            if valor in (None, ""):
                continue
            quando = datetime.fromisoformat(feed["created_at"].replace("Z", "+00:00"))
            leituras.append((quando, float(valor)))
        cursor = ate
    return leituras


def consolidar_por_dia(leituras: list[tuple[datetime, float]], dias: list[date]) -> list[dict[str, Any]]:
    """Resumo por dia local. Dias sem leitura entram com n=0 (buraco visível)."""
    por_dia: dict[date, list[tuple[datetime, float]]] = defaultdict(list)
    for quando, valor in leituras:
        local = quando.astimezone(_TZ)
        por_dia[local.date()].append((local, valor))

    hoje = datetime.now(_TZ).date()
    resultado = []
    for dia in dias:
        pontos = por_dia.get(dia, [])
        if dia == hoje:
            agora = datetime.now(_TZ)
            esperadas = max(1, int((agora.hour * 60 + agora.minute) / 2.5))
        else:
            esperadas = _LEITURAS_ESPERADAS_DIA
        linha: dict[str, Any] = {
            "data": dia.isoformat(),
            "n": len(pontos),
            "cobertura_pct": min(100.0, round(len(pontos) / esperadas * 100, 1)),
            "parcial": dia == hoje,
            "media": None,
            "min": None,
            "min_hora": None,
            "max": None,
            "max_hora": None,
            "acima_limite": 0,
        }
        if pontos:
            valores = [v for _, v in pontos]
            quando_min, v_min = min(pontos, key=lambda p: p[1])
            quando_max, v_max = max(pontos, key=lambda p: p[1])
            linha.update(
                media=round(sum(valores) / len(valores), 1),
                min=v_min,
                min_hora=quando_min.strftime("%H:%M"),
                max=v_max,
                max_hora=quando_max.strftime("%H:%M"),
                acima_limite=sum(1 for v in valores if v > LIMITE_C),
            )
        resultado.append(linha)
    return resultado


def _montar(dias: int) -> dict:
    canal = os.getenv("NEXTCON_CHANNEL_ID", "")
    if not canal:
        return {"disponivel": False, "erro": "NEXTCON_CHANNEL_ID não configurado", "limite": LIMITE_C, "dias": []}

    hoje = datetime.now(_TZ).date()
    lista_dias = [hoje - timedelta(days=i) for i in range(dias - 1, -1, -1)]
    inicio = datetime.combine(lista_dias[0], datetime.min.time(), _TZ).astimezone(UTC)
    fim = datetime.now(UTC)

    leituras = _buscar_leituras(canal, inicio, fim)
    return {
        "disponivel": bool(leituras),
        "limite": LIMITE_C,
        "canal": canal,
        "gerado_em": datetime.now(_TZ).isoformat(timespec="seconds"),
        "dias": consolidar_por_dia(leituras, lista_dias),
    }


def get_cached_temp_diaria(dias: int = 30) -> dict:
    """Resumo diário dos últimos `dias` dias (inclui hoje, parcial), cache 30min."""
    with _lock:
        hit = _cache.get(dias)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL:
            return hit[1]

    try:
        dados = _montar(dias)
    except Exception as exc:
        log.warning("datacenter_temp_diario.busca_falhou", erro=str(exc))
        # Não cacheia a falha: a próxima abertura da página tenta de novo.
        return {"disponivel": False, "erro": "Falha ao consultar o ThingSpeak", "limite": LIMITE_C, "dias": []}

    with _lock:
        _cache[dias] = (time.monotonic(), dados)
    return dados
