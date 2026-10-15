"""Verificação automática dos canais do CFTV: está gravando? tem imagem?

Só nos gravadores Intelbras cuja senha HTTP o dashboard já acertou
(``GravadorAcesso``) e só nos canais com câmera na conferência no local
(``CanalCftv`` com situação ``ativo``).

- Gravação, a cada ``INTERVALO_GRAVACAO``: procura no gravador o arquivo mais
  recente do canal (``mediaFileFind``). Sem arquivo terminando nos últimos
  ``TOLERANCIA_GRAVACAO`` = gravação parada; modo "desligado" = desligada.
  Analógica sem sinal continua "gravando" (tela preta): por isso a imagem é
  verificada à parte.
- Imagem, nos ``HORARIOS_FOTO`` (de dia): tira a foto do canal. Câmera que
  não entrega foto (duas tentativas) = sem resposta; tela chapada = sem cena
  (``cftv_imagem``). A miniatura fica guardada só enquanto há problema.

Depois de cada rodada, o resultado vai para o Zabbix (itens trapper do
template "CFTV Verificação (dashboard)", ver ``infra-setup/12_cftv_verificacao.py``):
o trigger avisa no WhatsApp, sem abrir chamado.

Roda numa thread por container: o primeiro worker do gunicorn que pega a
trava (``flock``) agenda; os outros só servem páginas.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple
from zoneinfo import ZoneInfo

import httpx
import requests
import structlog
from sqlalchemy.exc import SQLAlchemyError

from itgov.services import cftv_imagem
from itgov.services.intelbras_api import AcessoNegadoError, SessaoGravador

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

INTERVALO_GRAVACAO = timedelta(minutes=30)
BUSCA_GRAVACAO = timedelta(hours=2)
TOLERANCIA_GRAVACAO = timedelta(minutes=30)
HORARIOS_FOTO = (8, 11, 14, 17)  # horário de Brasília, de dia (foto à noite é escura de verdade)
ESPERA_NOVA_FOTO = 3.0
PAUSA_ENTRE_FOTOS = 0.5
CHAVE_SEM_GRAVAR = "cftv.verif.sem_gravar"
CHAVE_SEM_IMAGEM = "cftv.verif.sem_imagem"
_TZ = ZoneInfo("America/Sao_Paulo")
_TRAVA = os.path.join(tempfile.gettempdir(), "itgov-cftv-verificacao.lock")


class Alvo(NamedTuple):
    """Gravador a verificar."""

    ip: str
    usuario: str
    senha: str
    canais: list[int]


def _agora() -> datetime:
    return datetime.now(UTC)


def alvos() -> list[Alvo]:
    """Gravadores com senha HTTP aceita e canais com câmera na conferência (requer app context)."""
    from app.models.unidade import CanalCftv, DvrUnidade, GravadorAcesso
    from app.services.cftv_gravadores import _usuario_senha
    from itgov.api.v1.cftv_monitoring import get_cached_cftv_summary

    canais: dict[str, list[int]] = {}
    for c in CanalCftv.query.filter_by(situacao="ativo").order_by(CanalCftv.canal):
        canais.setdefault(c.ip_gravador, []).append(c.canal)
    # Nome oficial do gravador, para a credencial própria (CFTV_DVR_<NOME>_*)
    apelidos = {v.dvr: v.apelido for v in DvrUnidade.query.all() if v.apelido}
    nomes = {
        d["ip"]: apelidos.get(d["gravador"]) or d["name"]
        for d in get_cached_cftv_summary().get("devices", [])
        if d.get("is_gravador") and d.get("ip")
    }
    resultado = []
    for acesso in GravadorAcesso.query.all():
        if acesso.ip in canais and acesso.credencial and acesso.leitura and not acesso.erro:
            usuario, senha = _usuario_senha(acesso.credencial, nomes.get(acesso.ip, ""))
            if usuario and senha:
                resultado.append(Alvo(acesso.ip, usuario, senha, canais[acesso.ip]))
    return resultado


def _registro(ip: str, canal: int) -> Any:
    from app.extensions import db
    from app.models.unidade import VerificacaoCanal

    existente = VerificacaoCanal.query.filter_by(ip_gravador=ip, canal=canal).first()
    if existente is None:
        existente = VerificacaoCanal(ip_gravador=ip, canal=canal)
        db.session.add(existente)
    return existente


def _recusado(ip: str, agora: datetime) -> None:
    """Credencial recusada: anota como a leitura de canais faz, para ninguém insistir."""
    from app.extensions import db
    from app.models.unidade import GravadorAcesso

    acesso = db.session.get(GravadorAcesso, ip)
    if acesso is None:
        return
    falhas = json.loads(acesso.falhas or "{}")
    falhas[acesso.credencial or "?"] = agora.isoformat()
    acesso.falhas, acesso.credencial, acesso.erro = json.dumps(falhas), "", "credencial recusada"
    log.warning("cftv_verificacao.credencial_recusada", ip=ip)


def verificar_gravacao(lista: list[Alvo], agora: datetime | None = None) -> set[str]:
    """Confere se cada canal está gravando (requer app context).

    Args:
        lista: Gravadores e canais.
        agora: Horário da rodada (UTC).

    Returns:
        IPs dos gravadores verificados (os que responderam).
    """
    from app.extensions import db

    agora = agora or _agora()
    feitos: set[str] = set()
    for alvo in lista:
        try:
            with SessaoGravador(alvo.ip, alvo.usuario, alvo.senha) as g:
                hora = g.hora()
                modos = g.modos_gravacao()
                for canal in alvo.canais:
                    reg = _registro(alvo.ip, canal)
                    if modos.get(canal) == 2:
                        reg.gravacao, reg.ultima_gravacao = "desligada", None
                    else:
                        fim = g.ultima_gravacao(canal, hora - BUSCA_GRAVACAO, hora)
                        reg.ultima_gravacao = fim
                        reg.gravacao = "ok" if fim is not None and hora - fim <= TOLERANCIA_GRAVACAO else "parada"
                    reg.gravacao_em = agora
        except AcessoNegadoError:
            _recusado(alvo.ip, agora)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("cftv_verificacao.gravacao_falhou", ip=alvo.ip, erro=f"{type(exc).__name__}: {exc}"[:200])
            db.session.rollback()
            continue
        else:
            feitos.add(alvo.ip)
        db.session.commit()
    return feitos


def _foto(g: SessaoGravador, canal: int) -> bytes | None:
    jpeg = g.foto(canal)
    if jpeg is None:
        time.sleep(ESPERA_NOVA_FOTO)
        jpeg = g.foto(canal)
    return jpeg


def verificar_imagens(lista: list[Alvo], agora: datetime | None = None) -> set[str]:
    """Tira a foto de cada canal e vê se há imagem (requer app context).

    Args:
        lista: Gravadores e canais.
        agora: Horário da rodada (UTC).

    Returns:
        IPs dos gravadores verificados (os que responderam).
    """
    from app.extensions import db

    agora = agora or _agora()
    feitos: set[str] = set()
    for alvo in lista:
        try:
            with SessaoGravador(alvo.ip, alvo.usuario, alvo.senha) as g:
                for canal in alvo.canais:
                    reg = _registro(alvo.ip, canal)
                    jpeg = _foto(g, canal)
                    try:
                        analise = cftv_imagem.analisar(jpeg) if jpeg is not None else None
                    except ValueError:
                        analise = None
                    if jpeg is None or analise is None:
                        reg.imagem, reg.miniatura = "sem_resposta", None
                    elif cftv_imagem.sem_imagem(analise):
                        reg.imagem, reg.miniatura = "chapada", cftv_imagem.miniatura(jpeg)
                    else:
                        reg.imagem, reg.miniatura = "ok", None
                    reg.imagem_em = agora
                    time.sleep(PAUSA_ENTRE_FOTOS)
        except AcessoNegadoError:
            _recusado(alvo.ip, agora)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("cftv_verificacao.imagem_falhou", ip=alvo.ip, erro=f"{type(exc).__name__}: {exc}"[:200])
            db.session.rollback()
            continue
        else:
            feitos.add(alvo.ip)
        db.session.commit()
    return feitos


def por_gravador() -> dict[str, dict[int, dict[str, Any]]]:
    """Última verificação: IP → canal → ``{"gravacao", "ultima_gravacao", "gravacao_em", "imagem", "imagem_em", "miniatura"}``."""
    from app.models.unidade import VerificacaoCanal

    try:
        itens = VerificacaoCanal.query.all()
    except SQLAlchemyError as exc:
        log.warning("cftv_verificacao.leitura_falhou", erro=str(exc)[:200])
        return {}
    resultado: dict[str, dict[int, dict[str, Any]]] = {}
    for v in itens:
        resultado.setdefault(v.ip_gravador, {})[v.canal] = {
            "gravacao": v.gravacao,
            "ultima_gravacao": v.ultima_gravacao,
            "gravacao_em": v.gravacao_em,
            "imagem": v.imagem,
            "imagem_em": v.imagem_em,
            "miniatura": v.miniatura is not None,
        }
    return resultado


def _lista_canais(canais: list[int]) -> str:
    if not canais:
        return ""
    nums = [str(c) for c in sorted(canais)]
    texto = nums[0] if len(nums) == 1 else ", ".join(nums[:-1]) + " e " + nums[-1]
    return ("canal " if len(nums) == 1 else "canais ") + texto


def textos_para_zabbix(ips: set[str]) -> dict[str, dict[str, str]]:
    """Texto de cada item trapper por gravador verificado: lista dos canais com problema ("" = tudo ok)."""
    estado = por_gravador()
    textos: dict[str, dict[str, str]] = {}
    for ip in ips:
        canais = estado.get(ip, {})
        textos[ip] = {
            CHAVE_SEM_GRAVAR: _lista_canais([c for c, v in canais.items() if v["gravacao"] in ("parada", "desligada")]),
            CHAVE_SEM_IMAGEM: _lista_canais(
                [c for c, v in canais.items() if v["imagem"] in ("sem_resposta", "chapada")]
            ),
        }
    return textos


def enviar_zabbix(ips: set[str], chave: str) -> int:
    """Manda para o Zabbix o texto do item ``chave`` dos gravadores ``ips`` (``history.push``).

    Só envia para os gravadores que têm o template "CFTV Verificação (dashboard)".

    Returns:
        Quantos valores foram enviados.
    """
    from itgov.api.v1.cftv_monitoring import _zbx

    if not ips or not os.getenv("ZABBIX_URL"):
        return 0
    textos = textos_para_zabbix(ips)
    try:
        itens = _zbx(
            "item.get",
            {"output": ["itemid", "hostid"], "filter": {"key_": [chave]}, "selectInterfaces": ["ip"]},
        )
        valores = [
            {"itemid": item["itemid"], "value": textos[ip][chave]}
            for item in itens
            for ip in {i["ip"] for i in item.get("interfaces", [])} & ips
        ]
        if valores:
            _zbx("history.push", valores)
    except (requests.RequestException, RuntimeError, KeyError) as exc:
        log.warning("cftv_verificacao.zabbix_falhou", chave=chave, erro=str(exc)[:200])
        return 0
    return len(valores)


# ── Agendador ────────────────────────────────────────────────────────────────


def _ultima(coluna: str) -> datetime | None:
    from app.extensions import db
    from app.models.unidade import VerificacaoCanal

    valor = db.session.query(db.func.max(getattr(VerificacaoCanal, coluna))).scalar()
    return valor.replace(tzinfo=UTC) if valor is not None and valor.tzinfo is None else valor


def horario_de_foto(agora: datetime, ultima: datetime | None) -> bool:
    """Está num horário de foto (até 30 min depois da hora cheia) ainda sem foto nesse horário?"""
    local = agora.astimezone(_TZ)
    if local.hour not in HORARIOS_FOTO or local.minute >= 30:
        return False
    inicio = local.replace(minute=0, second=0, microsecond=0)
    return ultima is None or ultima.astimezone(_TZ) < inicio


# Última tentativa neste processo: gravador fora do ar não pode virar consulta a cada minuto
_tentativa: dict[str, datetime | None] = {"gravacao": None, "imagem": None}


def _mais_recente(*datas: datetime | None) -> datetime | None:
    return max((d for d in datas if d is not None), default=None)


def rodada(agora: datetime | None = None) -> dict[str, int]:
    """Faz o que estiver vencido: gravação a cada 30 min, foto nos horários (requer app context).

    Returns:
        Quantos gravadores foram verificados em cada parte.
    """
    agora = agora or _agora()
    feito = {"gravacao": 0, "imagem": 0}
    ultima_gravacao = _mais_recente(_ultima("gravacao_em"), _tentativa["gravacao"])
    gravacao = ultima_gravacao is None or agora - ultima_gravacao >= INTERVALO_GRAVACAO
    foto = horario_de_foto(agora, _mais_recente(_ultima("imagem_em"), _tentativa["imagem"]))
    if not (gravacao or foto):
        return feito
    lista = alvos()
    if gravacao:
        _tentativa["gravacao"] = agora
        ips = verificar_gravacao(lista, agora)
        enviar_zabbix(ips, CHAVE_SEM_GRAVAR)
        feito["gravacao"] = len(ips)
    if foto:
        _tentativa["imagem"] = agora
        ips = verificar_imagens(lista, agora)
        enviar_zabbix(ips, CHAVE_SEM_IMAGEM)
        feito["imagem"] = len(ips)
    log.info("cftv_verificacao.rodada", **feito)
    return feito


def iniciar_agendador(app: Flask) -> bool:
    """Liga o agendador neste processo se for o gunicorn e ninguém mais tiver a trava.

    Returns:
        True se este processo ficou com o agendador.
    """
    if app.config.get("TESTING") or not os.getenv("ZABBIX_URL") or "gunicorn" not in os.path.basename(sys.argv[0]):
        return False
    trava = open(_TRAVA, "w")  # noqa: SIM115 -- fica aberta enquanto o worker viver (segura a trava)
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        trava.close()
        return False

    def _laco() -> None:
        while True:
            time.sleep(60)
            try:
                with app.app_context():
                    rodada()
            except (SQLAlchemyError, httpx.HTTPError, requests.RequestException, OSError, ValueError) as exc:
                log.warning("cftv_verificacao.rodada_falhou", erro=f"{type(exc).__name__}: {exc}"[:200])
                with contextlib.suppress(SQLAlchemyError), app.app_context():
                    from app.extensions import db

                    db.session.rollback()

    app.extensions["cftv_verificacao_trava"] = trava
    threading.Thread(target=_laco, name="cftv-verificacao", daemon=True).start()
    log.info("cftv_verificacao.agendador_ligado", pid=os.getpid())
    return True
