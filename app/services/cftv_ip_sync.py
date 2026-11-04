"""Corrige no Zabbix o IP das câmeras que mudaram de endereço (agente de hora em hora).

O Zabbix pinga cada câmera no IP cadastrado no host. Quando a câmera troca de
IP (DHCP, troca de equipamento), o ping cai, o problema abre e o WhatsApp avisa
"câmera fora" — mas o gravador continua recebendo vídeo dela no IP novo.

A cada ``INTERVALO`` o agente:

1. lê todos os gravadores (API Intelbras), sem depender de alguém abrir a tela;
2. para cada câmera **fora no Zabbix** com canal, pega o IP que o gravador usa
   naquele canal — da leitura da API (se for de menos de ``LEITURA_FRESCA`` e o
   canal tiver vídeo) ou do SNMP do gravador (canal ``Connected``);
3. se o IP for outro, válido, da rede interna e de nenhum outro host CFTV,
   troca no Zabbix as interfaces do host que estavam no IP antigo.

Cada troca (ou recusa) fica em ``CftvIpCorrecao`` e aparece no /gov/cftv.
No máximo ``MAX_POR_RODADA`` trocas por rodada: uma leitura com defeito não
consegue reescrever o cadastro inteiro.
"""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from itgov.services.intelbras_api import SEM_ENDERECO

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

INTERVALO = timedelta(hours=1)
LEITURA_FRESCA = timedelta(hours=2)
MAX_POR_RODADA = 20
_PRIMEIRA_RODADA_S = 120
_TRAVA = os.path.join(tempfile.gettempdir(), "itgov-cftv-ip-sync.lock")

# Situação de cada correção registrada
CORRIGIDO = "corrigido"
CONFLITO = "conflito"  # o IP novo já é de outro host CFTV no Zabbix
FALHOU = "falhou"  # o Zabbix recusou ou o host não tinha interface no IP antigo


class Correcao(NamedTuple):
    """Câmera fora no Zabbix com IP diferente do que o gravador usa no canal."""

    hostid: str
    host: str
    nome: str
    gravador: str
    canal: int
    ip_antigo: str
    ip_novo: str
    fonte: str  # "api" ou "snmp"


def _agora() -> datetime:
    return datetime.now(UTC)


def ip_valido(ip: str) -> bool:
    """IPv4 da rede interna, que não seja endereço vazio/padrão de fábrica."""
    if ip in SEM_ENDERECO:
        return False
    try:
        end = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return end.version == 4 and end.is_private and not end.is_loopback and not end.is_link_local


def _ip_do_canal(card: dict[str, Any], acesso: dict[str, Any] | None, canal: int, agora: datetime) -> tuple[str, str]:
    """IP que o gravador usa no canal, se ele está recebendo vídeo: ``(ip, fonte)`` ou ``("", "")``."""
    from app.services.cftv_gravadores import _snmp_do_card

    acesso = acesso or {}
    leitura = acesso.get("leitura")
    lido = acesso.get("lido_ok_em")
    if leitura is not None and lido is not None and agora - lido <= LEITURA_FRESCA:
        cam = next((c for c in leitura.cameras if c.canal == canal), None)
        if cam is not None and cam.tipo == "ip" and cam.ip and canal not in leitura.sem_video:
            return cam.ip, "api"
    snmp, _ = _snmp_do_card(card)
    s = snmp.get(canal, {})
    if s.get("status") == "Connected" and s.get("ip", "") not in SEM_ENDERECO:
        return s["ip"], "snmp"
    return "", ""


def candidatos(cards: list[dict[str, Any]], acessos: dict[str, dict[str, Any]], agora: datetime) -> list[Correcao]:
    """Câmeras fora no Zabbix cujo gravador recebe vídeo delas em outro IP.

    Args:
        cards: Cards de ``montar_visao`` (com ``dispositivos`` e ``gravador_host``).
        acessos: ``cftv_gravadores.leituras()``.
        agora: Horário da rodada (UTC).

    Returns:
        As correções a fazer, sem repetir host.
    """
    achadas: dict[str, Correcao] = {}
    for card in cards:
        ip_gravador = (card.get("gravador_host") or {}).get("ip", "")
        if not ip_gravador:
            continue
        for d in card.get("dispositivos") or []:
            canal = str(d.get("canal", ""))
            if d.get("is_gravador") or d.get("status") != "down" or not canal.isdigit() or not d.get("hostid"):
                continue
            novo, fonte = _ip_do_canal(card, acessos.get(ip_gravador), int(canal), agora)
            if not novo or novo == d.get("ip") or novo == ip_gravador or not ip_valido(novo):
                continue
            achadas.setdefault(
                d["hostid"],
                Correcao(
                    d["hostid"],
                    d.get("host", ""),
                    d.get("name", ""),
                    card.get("titulo", ""),
                    int(canal),
                    d.get("ip", ""),
                    novo,
                    fonte,
                ),
            )
    return list(achadas.values())


def _trocar_no_zabbix(c: Correcao) -> int:
    """Troca no host as interfaces que estavam no IP antigo. Devolve quantas trocou.

    Raises:
        RuntimeError: O Zabbix devolveu erro.
        requests.RequestException: Falha de rede.
    """
    from itgov.api.v1.cftv_monitoring import _zbx

    interfaces = _zbx("hostinterface.get", {"hostids": [c.hostid], "output": ["interfaceid", "ip"]})
    trocadas = 0
    for i in interfaces:
        if i.get("ip") == c.ip_antigo:
            _zbx("hostinterface.update", {"interfaceid": i["interfaceid"], "ip": c.ip_novo})
            trocadas += 1
    return trocadas


def aplicar(correcoes: list[Correcao], ips_cftv: set[str]) -> dict[str, int]:
    """Faz as trocas no Zabbix e registra cada uma. Requer app context.

    Args:
        correcoes: De ``candidatos``.
        ips_cftv: IPs de todos os hosts CFTV no Zabbix (para não duplicar).

    Returns:
        Situação → quantas.
    """
    from app.extensions import db
    from app.models.cftv_ip import CftvIpCorrecao

    contagem: dict[str, int] = {}
    for c in correcoes[:MAX_POR_RODADA]:
        if c.ip_novo in ips_cftv:
            situacao, detalhe = CONFLITO, f"{c.ip_novo} já é de outro host CFTV no Zabbix"
        else:
            try:
                trocadas = _trocar_no_zabbix(c)
            except (RuntimeError, requests.RequestException) as exc:
                log.warning("cftv_ip_sync.zabbix_falhou", host=c.host, erro=f"{type(exc).__name__}: {exc}"[:200])
                situacao, detalhe = FALHOU, "o Zabbix não aceitou a troca (detalhe no log)"
            else:
                if trocadas:
                    situacao, detalhe = CORRIGIDO, f"{trocadas} interface(s) trocada(s)"
                    ips_cftv.add(c.ip_novo)
                else:
                    situacao, detalhe = FALHOU, f"o host não tem interface no IP {c.ip_antigo}"
        # Conflito repetido de hora em hora não precisa de uma linha nova por rodada
        if situacao == CONFLITO and _conflito_ja_registrado(c):
            continue
        db.session.add(
            CftvIpCorrecao(
                hostid=c.hostid,
                host=c.host[:128],
                nome=c.nome[:200],
                gravador=c.gravador[:120],
                canal=c.canal,
                ip_antigo=c.ip_antigo,
                ip_novo=c.ip_novo,
                fonte=c.fonte,
                situacao=situacao,
                detalhe=detalhe[:255],
            )
        )
        db.session.commit()
        contagem[situacao] = contagem.get(situacao, 0) + 1
        log.info(
            "cftv_ip_sync.correcao",
            host=c.host,
            canal=c.canal,
            gravador=c.gravador,
            de=c.ip_antigo,
            para=c.ip_novo,
            fonte=c.fonte,
            situacao=situacao,
        )
    if len(correcoes) > MAX_POR_RODADA:
        log.warning("cftv_ip_sync.limite", pendentes=len(correcoes) - MAX_POR_RODADA)
    return contagem


def _conflito_ja_registrado(c: Correcao) -> bool:
    from app.models.cftv_ip import CftvIpCorrecao

    ultima = CftvIpCorrecao.query.filter_by(hostid=c.hostid).order_by(CftvIpCorrecao.quando.desc()).first()
    return ultima is not None and ultima.situacao == CONFLITO and ultima.ip_novo == c.ip_novo


def rodada(agora: datetime | None = None) -> dict[str, Any]:
    """Lê os gravadores e corrige os IPs no Zabbix. Requer app context.

    Returns:
        Quantos gravadores foram lidos, quantos candidatos e a contagem por situação.
    """
    from app.services import cftv_gravadores
    from app.views.dashboards import _visao_cftv
    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary

    agora = agora or _agora()
    visao = _visao_cftv("", disparar_leitura=False).visao
    alvos = cftv_gravadores.alvos_dos_cards(visao["cards"])
    with cftv_gravadores.ocupado():
        lidos = cftv_gravadores.atualizar(alvos)
        ips_gravadores = {ip for c in visao["cards"] if (ip := (c.get("gravador_host") or {}).get("ip"))}
        cftv_gravadores.limpar_abandonados(ips_gravadores, agora)
    correcoes = candidatos(visao["cards"], cftv_gravadores.leituras(), agora)
    ips_cftv = {d.get("ip", "") for d in (get_cached_cftv_summary() or {}).get("devices", []) if d.get("ip")}
    contagem = aplicar(correcoes, ips_cftv) if correcoes else {}
    resultado = {"lidos": lidos, "candidatos": len(correcoes), **contagem}
    log.info("cftv_ip_sync.rodada", **resultado)
    return resultado


def ultimas(n: int = 15) -> list[dict[str, Any]]:
    """Últimas correções registradas (ver ``app.models.cftv_ip.ultimas_correcoes``)."""
    from app.models.cftv_ip import ultimas_correcoes

    return ultimas_correcoes(n)


def iniciar_agendador(app: Flask) -> bool:
    """Liga o agente neste processo se for o gunicorn e ninguém mais tiver a trava.

    Returns:
        True se este processo ficou com o agente.
    """
    if app.config.get("TESTING") or "gunicorn" not in os.path.basename(sys.argv[0]):
        return False
    if not os.getenv("ZABBIX_URL"):
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
            except (
                SQLAlchemyError,
                httpx.HTTPError,
                requests.RequestException,
                RuntimeError,
                OSError,
                ValueError,
            ) as exc:
                log.warning("cftv_ip_sync.rodada_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
                with contextlib.suppress(SQLAlchemyError), app.app_context():
                    from app.extensions import db

                    db.session.rollback()
            time.sleep(INTERVALO.total_seconds())

    app.extensions["cftv_ip_sync_trava"] = trava
    threading.Thread(target=_laco, name="cftv-ip-sync", daemon=True).start()
    log.info("cftv_ip_sync.agendador_ligado", pid=os.getpid())
    return True
