"""Sondagem periódica dos IPs "outro" da página Rede atrás de câmeras e gravadores.

A cada ``INTERVALO`` pega os hosts online que a descoberta não soube
classificar (``tipo_sugerido == "outro"``) e pergunta a cada um, sem senha, se é
Hikvision ou Intelbras/Dahua (``itgov.services.assinatura_rede``). O resultado
fica num JSON no volume de dados (``ASSINATURAS_PATH``), lido pela página Rede,
que passa o IP para "camera" com o motivo da assinatura.

Quem não respondeu como câmera só é testado de novo depois de ``REPETIR_NEGATIVO``;
quem respondeu, depois de ``REPETIR_POSITIVO``. Um worker só roda o laço (trava
em arquivo, como os outros agentes).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from itgov.services import assinatura_rede

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

INTERVALO = timedelta(hours=6)
REPETIR_NEGATIVO = timedelta(days=1)
REPETIR_POSITIVO = timedelta(days=7)
MAX_POR_RODADA = 400
PARALELO = 16
_PRIMEIRA_RODADA_S = 300
_TRAVA = os.path.join(tempfile.gettempdir(), "itgov-assinatura-rede.lock")


def _arquivo() -> Path:
    return Path(os.environ.get("ASSINATURAS_PATH", "/app/data/assinaturas_rede.json"))


def carregar() -> dict[str, dict[str, Any]]:
    """IP → ``{"marca", "tipo", "evidencia", "em"}`` (``marca`` vazia = não é câmera).

    Returns:
        O que está gravado; vazio se o arquivo não existe ou está ilegível.
    """
    try:
        with open(_arquivo(), encoding="utf-8") as f:
            dados = json.load(f)
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _gravar(dados: dict[str, dict[str, Any]]) -> None:
    caminho = _arquivo()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, caminho)


def assinatura(ip: str, cache: dict[str, dict[str, Any]]) -> assinatura_rede.Assinatura | None:
    """A assinatura positiva gravada para o IP, ou None."""
    d = cache.get(ip) or {}
    if not d.get("marca"):
        return None
    return assinatura_rede.Assinatura(marca=d["marca"], tipo=d.get("tipo", "cftv"), evidencia=d.get("evidencia", ""))


def _vencido(registro: dict[str, Any] | None, agora: datetime) -> bool:
    if not registro:
        return True
    try:
        em = datetime.fromisoformat(str(registro.get("em")))
    except ValueError:
        return True
    espera = REPETIR_POSITIVO if registro.get("marca") else REPETIR_NEGATIVO
    return agora - em >= espera


def a_sondar(hosts: list[dict[str, Any]], cache: dict[str, dict[str, Any]], agora: datetime) -> list[str]:
    """IPs que valem uma sondagem nesta rodada.

    Args:
        hosts: Hosts da página Rede (``get_cached_rede_summary()["hosts"]``).
        cache: O que já foi gravado.
        agora: Momento da rodada.

    Returns:
        IPs online, ainda "outro", cuja última sondagem venceu (no máximo ``MAX_POR_RODADA``).
    """
    ips = [
        h["ip"]
        for h in hosts
        if h.get("online") and h.get("tipo_sugerido") == "outro" and _vencido(cache.get(h["ip"]), agora)
    ]
    return ips[:MAX_POR_RODADA]


def sondar_todos(ips: list[str], cache: dict[str, dict[str, Any]], agora: datetime) -> int:
    """Sonda os IPs e grava o resultado no cache (no lugar).

    Returns:
        Quantos responderam como câmera ou gravador.
    """

    def _um(ip: str) -> tuple[str, assinatura_rede.Assinatura | None]:
        return ip, assinatura_rede.sondar(ip)

    achados = 0
    with ThreadPoolExecutor(PARALELO) as ex:
        for ip, a in ex.map(_um, ips):
            cache[ip] = (a.model_dump() if a else {"marca": ""}) | {"em": agora.isoformat()}
            achados += a is not None
    return achados


def rodada(agora: datetime | None = None) -> dict[str, int]:
    """Uma rodada: escolhe, sonda e grava. Requer app context (lê a página Rede).

    Returns:
        ``{"sondados", "achados"}``.
    """
    from itgov.api.v1.rede_monitoring import get_cached_rede_summary

    agora = agora or datetime.now(UTC)
    cache = carregar()
    ips = a_sondar(get_cached_rede_summary().get("hosts", []), cache, agora)
    achados = sondar_todos(ips, cache, agora) if ips else 0
    if ips:
        _gravar(cache)
    resultado = {"sondados": len(ips), "achados": achados}
    log.info("assinatura_rede.rodada", **resultado)
    return resultado


def iniciar_agendador(app: Flask) -> bool:
    """Liga a sondagem neste processo se for o gunicorn e ninguém mais tiver a trava.

    Returns:
        True se este processo ficou com o agente.
    """
    if app.config.get("TESTING") or "gunicorn" not in os.path.basename(sys.argv[0]):
        return False
    trava = open(_TRAVA, "w")  # noqa: SIM115 -- fica aberta enquanto o worker viver (segura a trava)
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        trava.close()
        return False

    def _laco() -> None:
        time.sleep(_PRIMEIRA_RODADA_S)
        while True:
            try:
                with app.app_context():
                    rodada()
            except (OSError, RuntimeError, ValueError) as exc:
                log.warning("assinatura_rede.rodada_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
            time.sleep(INTERVALO.total_seconds())

    app.extensions["assinatura_rede_trava"] = trava
    threading.Thread(target=_laco, name="assinatura-rede", daemon=True).start()
    log.info("assinatura_rede.agendador_ligado", pid=os.getpid())
    return True
