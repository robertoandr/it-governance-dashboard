"""Monitoramento no Zabbix dos dispositivos descobertos (item r2).

Com o automático ligado (Rede → "Monitorar sozinho no Zabbix"), a cada
atualização da descoberta os dispositivos com template de confiança alta
viram host no Zabbix, numa thread e com limite por rodada. Os de confiança
baixa ficam na tela como sugestão para confirmar ou trocar; "não monitorar"
fica guardado e não volta a ser sugerido.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from itgov.services import zabbix_templates

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

CHAVE_AUTO = "zabbix_auto"
POR_RODADA = 20  # hosts criados por rodada, para não inundar o Zabbix de uma vez
ESPERA_ERRO = timedelta(hours=6)
_ocupado = threading.Lock()
_ultima_rodada = 0.0
INTERVALO = 300.0

_FALHAS_ZABBIX = (requests.RequestException, RuntimeError, ValueError, KeyError)


def auto_ligado() -> bool:
    """O monitoramento automático está ligado?"""
    from app.extensions import db
    from app.models.rede import RedeConfig

    try:
        item = db.session.get(RedeConfig, CHAVE_AUTO)
    except SQLAlchemyError:
        return False
    return bool(item and item.valor == "1")


def definir_auto(ligado: bool) -> None:
    """Liga ou desliga o monitoramento automático."""
    from app.extensions import db
    from app.models.rede import RedeConfig

    item = db.session.get(RedeConfig, CHAVE_AUTO) or RedeConfig(chave=CHAVE_AUTO)
    item.valor = "1" if ligado else "0"
    db.session.add(item)
    db.session.commit()


def decisoes() -> dict[str, Any]:
    """IP → ``RedeMonitoramento`` de tudo que já foi decidido."""
    from app.models.rede import RedeMonitoramento

    try:
        return {d.ip: d for d in RedeMonitoramento.query.all()}
    except SQLAlchemyError as exc:
        log.warning("rede_zabbix.leitura_falhou", erro=str(exc))
        return {}


def situacao(host: dict[str, Any], decisao: Any | None) -> dict[str, Any]:
    """Como o host aparece na coluna Zabbix da página Rede.

    Returns:
        ``{"estado": "monitorado" | "pelo_dashboard" | "recusado" | "auto" |
        "sugestao" | "erro" | "nao_se_aplica", "sugestao": Sugestao | None, ...}``
    """
    sugestao = zabbix_templates.sugerir(host)
    if host.get("zabbix_grupos") and not (decisao and decisao.hostid):
        return {"estado": "monitorado", "sugestao": None}
    if decisao is not None:
        if decisao.decisao == "recusado":
            return {"estado": "recusado", "sugestao": sugestao}
        if decisao.decisao == "erro":
            return {"estado": "erro", "sugestao": sugestao, "erro": decisao.erro}
        return {"estado": "pelo_dashboard", "sugestao": sugestao, "template": decisao.template}
    if sugestao is None:
        return {"estado": "nao_se_aplica", "sugestao": None}
    return {"estado": "auto" if sugestao.confianca == "alta" else "sugestao", "sugestao": sugestao}


def registrar(ip: str, decisao: str, template: str = "", hostid: str = "", erro: str = "", por: str = "") -> None:
    """Grava (ou troca) a decisão sobre o IP."""
    from app.extensions import db
    from app.models.rede import RedeMonitoramento

    item = db.session.get(RedeMonitoramento, ip) or RedeMonitoramento(ip=ip)
    item.decisao, item.template, item.hostid, item.erro = decisao, template, hostid, erro[:255]
    item.por, item.em = por, datetime.now(UTC)
    db.session.add(item)
    db.session.commit()


def monitorar(host: dict[str, Any], template: str, grupo: str, interface: str, decisao: str, por: str) -> str:
    """Cria (ou corrige) o host no Zabbix e grava a decisão.

    Returns:
        O ``hostid``.

    Raises:
        ValueError, RuntimeError, requests.RequestException: Falha no Zabbix
            (a decisão fica gravada como ``erro``).
    """
    nome = str(host.get("ia_nome") or (host.get("hostname") if host.get("hostname") != host["ip"] else "") or "")
    try:
        hostid, _ = zabbix_templates.monitorar(host["ip"], nome, template, grupo, interface)
    except _FALHAS_ZABBIX as exc:
        registrar(host["ip"], "erro", template, erro=str(exc), por=por)
        raise
    registrar(host["ip"], decisao, template, hostid, por=por)
    return hostid


def processar_em_segundo_plano(app: Flask, hosts: list[dict[str, Any]]) -> None:
    """Monitora sozinho, numa thread, os dispositivos de confiança alta (se ligado)."""
    global _ultima_rodada
    if time.monotonic() - _ultima_rodada < INTERVALO or not _ocupado.acquire(blocking=False):
        return
    _ultima_rodada = time.monotonic()
    threading.Thread(target=_rodar, args=(app, hosts), daemon=True).start()


def _rodar(app: Flask, hosts: list[dict[str, Any]]) -> None:
    try:
        with app.app_context():
            _processar(hosts)
    finally:
        _ocupado.release()


def _processar(hosts: list[dict[str, Any]]) -> int:
    from app.services import classificacoes
    from itgov.api.v1.rede_descoberta import aplicar_classificacoes

    if not auto_ligado():
        return 0
    feitas = decisoes()
    agora = datetime.now(UTC)
    criados = 0
    for h in aplicar_classificacoes(hosts, classificacoes.regras(classificacoes.carregar())):
        if criados >= POR_RODADA:
            break
        d = feitas.get(h["ip"])
        if d is not None and not (d.decisao == "erro" and agora - _utc(d.em) > ESPERA_ERRO):
            continue
        info = situacao(h, None)
        if info["estado"] != "auto":
            continue
        s = info["sugestao"]
        try:
            monitorar(h, s.template, s.grupo, s.interface, "auto", "automático")
            criados += 1
        except _FALHAS_ZABBIX as exc:
            log.warning("rede_zabbix.auto_falhou", ip=h["ip"], template=s.template, erro=str(exc)[:200])
    if criados:
        log.info("rede_zabbix.rodada", criados=criados)
    return criados


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
