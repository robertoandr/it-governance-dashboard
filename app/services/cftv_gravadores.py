"""Canais dos gravadores (DVR/NVR) pela API Intelbras: capacidade, livres e sem vídeo.

Credenciais (variáveis de ambiente):

- própria do gravador: ``CFTV_DVR_<NOME>_USER`` / ``CFTV_DVR_<NOME>_PASS``, com
  ``<NOME>`` = nome oficial em maiúsculas, sem acento e com ``_`` (ex.:
  ``Triunfo_2_Folha2`` → ``CFTV_DVR_TRIUNFO_2_FOLHA2_PASS``). O campo que faltar
  vem da padrão. Quando existe, é a única tentada naquele gravador;
- gerais, nesta ordem: Sede Centro ``CFTV_CAM_CENTRO_*``; depois ``CFTV_CAM_PADRAO_*``,
  ``CFTV_CAM_*`` e ``INTELBRAS_HTTP_USER``/``INTELBRAS_HTTP_PASSWORD``.

A que funcionou fica guardada por IP; a que falhou não é tentada de novo por
``ESPERA_FALHA``. O aparelho bloqueia o usuário depois de algumas recusas
seguidas, então cada leitura tenta no máximo uma credencial recusada e, depois
de uma recusa, o gravador espera ``ESPERA_RECUSA`` antes de qualquer outra.
A leitura roda numa thread, no máximo a cada ``INTERVALO`` por gravador.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
import structlog
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from itgov.services.intelbras_api import (
    SEM_ENDERECO,
    AcessoNegadoError,
    LeituraGravador,
    capacidade_do_modelo,
    ler_gravador,
    titulo_padrao,
)

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

CREDENCIAIS: dict[str, tuple[str, str]] = {
    "centro": ("CFTV_CAM_CENTRO_USER", "CFTV_CAM_CENTRO_PASS"),
    "padrao": ("CFTV_CAM_PADRAO_USER", "CFTV_CAM_PADRAO_PASS"),
    "principal": ("CFTV_CAM_USER", "CFTV_CAM_PASS"),
    "intelbras": ("INTELBRAS_HTTP_USER", "INTELBRAS_HTTP_PASSWORD"),
}
PROPRIA = "propria"
INTERVALO = timedelta(minutes=5)
ESPERA_FALHA = timedelta(hours=6)
ESPERA_RECUSA = timedelta(minutes=30)
_ocupado = threading.Lock()


class Alvo(NamedTuple):
    """Gravador a ler."""

    ip: str
    centro: bool
    nome: str = ""


def chave_env(nome: str) -> str:
    """Prefixo das variáveis da credencial própria (``Shopping _R3`` → ``CFTV_DVR_SHOPPING_R3``)."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return "CFTV_DVR_" + re.sub(r"[^A-Z0-9]+", "_", sem_acento.upper()).strip("_")


def credencial_propria(nome: str) -> tuple[str, str] | None:
    """Usuário e senha próprios do gravador, completando com a padrão o que faltar.

    Returns:
        ``(usuario, senha)``, ou None se o gravador não tem credencial própria
        (ou se falta um campo e não há padrão para completar).
    """
    if not nome:
        return None
    prefixo = chave_env(nome)
    usuario, senha = os.getenv(f"{prefixo}_USER", ""), os.getenv(f"{prefixo}_PASS", "")
    if not usuario and not senha:
        return None
    padrao_u, padrao_p = (os.getenv(v, "") for v in CREDENCIAIS["padrao"])
    usuario, senha = usuario or padrao_u, senha or padrao_p
    return (usuario, senha) if usuario and senha else None


def ordem_credenciais(centro: bool, nome: str = "") -> list[str]:
    """Credenciais configuradas, na ordem de tentativa."""
    if credencial_propria(nome):
        return [PROPRIA]
    ordem = ["centro", "padrao", "principal", "intelbras"] if centro else ["padrao", "principal", "intelbras"]
    return [n for n in ordem if os.getenv(CREDENCIAIS[n][0]) and os.getenv(CREDENCIAIS[n][1])]


def _usuario_senha(credencial: str, nome: str = "") -> tuple[str, str]:
    if credencial == PROPRIA:
        return credencial_propria(nome) or ("", "")
    u, p = CREDENCIAIS[credencial]
    return os.getenv(u, ""), os.getenv(p, "")


def _utc(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def ler_um(ip: str, centro: bool, acesso: Any, agora: datetime, nome: str = "") -> None:
    """Lê um gravador e atualiza ``acesso`` (``GravadorAcesso``) no lugar.

    Tenta primeiro a credencial que já funcionou; pula as que falharam há
    menos de ``ESPERA_FALHA``. Para na primeira recusa (o aparelho bloqueia o
    usuário depois de algumas seguidas) e não tenta nada por ``ESPERA_RECUSA``.

    Args:
        ip: IP do gravador.
        centro: É da Sede Centro (usa a credencial do centro primeiro).
        acesso: ``GravadorAcesso`` do IP.
        agora: Horário da leitura (UTC).
        nome: Nome oficial do gravador (para a credencial própria).
    """
    falhas: dict[str, str] = json.loads(acesso.falhas or "{}")
    ultima = max((datetime.fromisoformat(v) for v in falhas.values()), default=None)
    if ultima is not None and agora - ultima < ESPERA_RECUSA:
        return
    ordem = ordem_credenciais(centro, nome)
    if acesso.credencial in ordem:
        ordem.remove(acesso.credencial)
        ordem.insert(0, acesso.credencial)
    tentou = False
    for credencial in ordem:
        recusada = falhas.get(credencial)
        if credencial != acesso.credencial and recusada and agora - datetime.fromisoformat(recusada) < ESPERA_FALHA:
            continue
        tentou = True
        try:
            leitura = ler_gravador(ip, *_usuario_senha(credencial, nome))
        except AcessoNegadoError:
            falhas[credencial] = agora.isoformat()
            if acesso.credencial == credencial:
                acesso.credencial = ""
            acesso.erro = "credencial recusada"
            log.warning("cftv_gravadores.credencial_recusada", ip=ip, credencial=credencial)
            break
        except httpx.HTTPError as exc:
            acesso.erro = f"sem resposta: {type(exc).__name__}"
            break
        falhas.pop(credencial, None)
        acesso.credencial, acesso.leitura, acesso.erro = credencial, leitura.model_dump_json(), ""
        break
    if not tentou:
        acesso.erro = "nenhuma credencial aceita" if ordem else "nenhuma credencial configurada"
    acesso.falhas = json.dumps(falhas)
    acesso.lido_em = agora


def atualizar(gravadores: list[Alvo]) -> int:
    """Lê os gravadores vencidos (requer app context).

    Args:
        gravadores: Gravadores com IP.

    Returns:
        Quantos foram lidos agora.
    """
    from app.extensions import db
    from app.models.unidade import GravadorAcesso

    agora = datetime.now(UTC)
    lidos = 0
    for ip, centro, nome in gravadores:
        acesso = db.session.get(GravadorAcesso, ip) or GravadorAcesso(ip=ip, falhas="{}")
        visto = _utc(acesso.lido_em)
        if visto is not None and agora - visto < INTERVALO:
            continue
        ler_um(ip, centro, acesso, agora, nome)
        db.session.add(acesso)
        try:
            db.session.commit()
        except IntegrityError:
            # Outro worker gravou o mesmo IP ao mesmo tempo: fica a leitura dele
            db.session.rollback()
            continue
        lidos += 1
    return lidos


def disparar(app: Flask, gravadores: list[Alvo]) -> None:
    """Atualiza em segundo plano (uma thread por vez por worker)."""
    if not gravadores or not _ocupado.acquire(blocking=False):
        return

    def _rodar() -> None:
        try:
            with app.app_context():
                atualizar(gravadores)
        except SQLAlchemyError as exc:
            log.warning("cftv_gravadores.falhou", erro=str(exc)[:200])
        finally:
            _ocupado.release()

    threading.Thread(target=_rodar, daemon=True).start()


def leituras() -> dict[str, dict[str, Any]]:
    """IP → ``{"leitura": LeituraGravador | None, "credencial", "erro", "lido_em"}``."""
    from app.models.unidade import GravadorAcesso

    try:
        itens = GravadorAcesso.query.all()
    except SQLAlchemyError as exc:
        log.warning("cftv_gravadores.leitura_falhou", erro=str(exc)[:200])
        return {}
    return {
        a.ip: {
            "leitura": LeituraGravador.model_validate_json(a.leitura) if a.leitura else None,
            "credencial": a.credencial,
            "erro": a.erro,
            "lido_em": _utc(a.lido_em),
        }
        for a in itens
    }


def cadastro_canais() -> dict[str, dict[int, dict[str, Any]]]:
    """Cadastro da conferência no local por gravador: IP → canal → campos de ``CanalCftv``."""
    from app.models.unidade import CanalCftv

    try:
        itens = CanalCftv.query.all()
    except SQLAlchemyError as exc:
        log.warning("cftv_gravadores.cadastro_falhou", erro=str(exc)[:200])
        return {}
    cadastro: dict[str, dict[int, dict[str, Any]]] = {}
    for c in itens:
        cadastro.setdefault(c.ip_gravador, {})[c.canal] = {
            "situacao": c.situacao,
            "analogica": c.analogica,
            "ip_camera": c.ip_camera,
            "equipamento": c.equipamento,
            "imagem": c.imagem,
        }
    return cadastro


def _mais_recente(*datas: datetime | None) -> datetime | None:
    return max((_utc(d) for d in datas if d is not None), default=None)


def _snmp_do_card(card: dict[str, Any]) -> tuple[dict[int, dict[str, str]], bool]:
    host = card.get("gravador_host") or {}
    snmp = {int(k): v for k, v in (host.get("canais_snmp") or {}).items() if str(k).isdigit()}
    return snmp, bool(host.get("status_canal_confiavel", True))


def canais_do_card(
    card: dict[str, Any],
    acesso: dict[str, Any] | None,
    manual: int | None,
    cadastro: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Capacidade, canais em uso, livres e canais sem vídeo de um card de gravador.

    Canal em uso é o que entra no inventário (``inventario_do_card``). Canal
    sem vídeo só vira alerta se estiver em uso.

    Args:
        card: Card de ``montar_visao``.
        acesso: Item de ``leituras()`` do IP do gravador (ou None).
        manual: Capacidade informada à mão (``DvrUnidade.canais``).
        cadastro: Canal → conferência no local (``cadastro_canais()[ip]``).

    Returns:
        ``{"total", "usados", "livres", "sem_video": [{"canal", "nome"}], "analogicas", "fonte", "erro"}``,
        ou None quando não há como saber a capacidade.
    """
    leitura: LeituraGravador | None = (acesso or {}).get("leitura")
    snmp, _ = _snmp_do_card(card)
    modelo = leitura.modelo if leitura else ((card.get("gravador_host") or {}).get("model") or "")
    if manual:
        total, fonte = manual, "informado"
    elif leitura and leitura.canais:
        total, fonte = leitura.canais, "gravador"
    elif snmp:
        total, fonte = max(snmp), "snmp"
    elif capacidade_do_modelo(modelo):
        total, fonte = capacidade_do_modelo(modelo), "modelo"
    else:
        return None
    linhas = [x for x in inventario_do_card(card, acesso, cadastro) if x["canal"] <= total]
    return {
        "total": total,
        "usados": len(linhas),
        "livres": max(total - len(linhas), 0),
        "sem_video": [{"canal": x["canal"], "nome": x["nome"]} for x in linhas if x["sem_video"]],
        "analogicas": sum(1 for x in linhas if x["tipo"] == "analogica"),
        "fonte": fonte,
        "modelo": modelo,
        "erro": (acesso or {}).get("erro", "") if not leitura else "",
    }


def inventario_do_card(
    card: dict[str, Any],
    acesso: dict[str, Any] | None,
    cadastro: dict[int, dict[str, Any]] | None = None,
    verificacao: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Câmeras em uso de um card de gravador, canal a canal — inclusive as analógicas.

    Junta quatro fontes: a conferência no local (``cadastro``: câmera, vazio ou
    outro equipamento, analógica), o SNMP do gravador no Zabbix (status, nome e
    IP de cada canal), a leitura pela API Intelbras (tipo, modelo, IP, MAC,
    nome do canal e perda de vídeo) e a câmera do Zabbix no mesmo canal (nome
    e status do ping).

    Entra o canal marcado como câmera/outro na conferência, com câmera no
    Zabbix, câmera IP cadastrada no gravador (API ou IP no SNMP), vídeo (API ou
    SNMP ``Connected``) ou nome dado no gravador. Canal conferido como vazio sai, a não ser que
    tenha câmera no Zabbix.

    Sem vídeo: perda de vídeo pela API, SNMP ``Unconnect``, ou SNMP ``Empty``
    com IP de câmera cadastrado em gravador cujo status é confiável.

    Args:
        card: Card de ``montar_visao``.
        acesso: Item de ``leituras()`` do IP do gravador (ou None).
        cadastro: Canal → conferência no local (``cadastro_canais()[ip]``).
        verificacao: Canal → verificação automática (``cftv_verificacao.por_gravador()[ip]``).

    Returns:
        ``[{"canal", "nome", "tipo", "modelo", "ip", "mac", "sem_video", "video", "status",
        "situacao", "sem_imagem", "gravacao", "ultima_gravacao", "imagem", "verificado_em", "miniatura"}]`` em ordem
        de canal. ``tipo``: ``ip``, ``analogica``, ``outro`` ou ``""``; ``video``: ``ok``, ``sem``
        ou ``""`` (não se sabe); ``gravacao`` e ``imagem``: resultado da verificação automática.
    """
    leitura: LeituraGravador | None = (acesso or {}).get("leitura")
    snmp, confiavel = _snmp_do_card(card)
    cadastro = cadastro or {}
    verificacao = verificacao or {}
    no_zabbix: dict[int, dict[str, Any]] = {
        int(d["canal"]): d for d in card["dispositivos"] if str(d.get("canal", "")).isdigit()
    }
    cameras = {c.canal: c for c in leitura.cameras} if leitura else {}
    canais = set(no_zabbix) | {c for c, cam in cameras.items() if cam.tipo == "ip"}
    if leitura:
        for canal in range(1, leitura.canais + 1):
            if not titulo_padrao(leitura.titulos.get(canal, "")) or canal not in leitura.sem_video:
                canais.add(canal)
    canais |= {c for c, s in snmp.items() if s.get("status") == "Connected" or s.get("ip", "") not in SEM_ENDERECO}
    canais |= {c for c, m in cadastro.items() if m["situacao"] in ("ativo", "outro")}
    canais -= {c for c, m in cadastro.items() if m["situacao"] == "vazio" and c not in no_zabbix}
    linhas = []
    for canal in sorted(canais):
        cam = cameras.get(canal)
        zbx = no_zabbix.get(canal, {})
        conf = cadastro.get(canal, {})
        s = snmp.get(canal, {})
        v = verificacao.get(canal, {})
        ip_snmp = "" if s.get("ip", "") in SEM_ENDERECO else s["ip"]
        titulo = leitura.titulos.get(canal, "") if leitura else ""
        if conf.get("situacao") == "outro":
            tipo = "outro"
        elif cam:
            tipo = cam.tipo
        elif conf.get("analogica"):
            tipo = "analogica"
        elif conf.get("ip_camera") or ip_snmp:
            tipo = "ip"
        else:
            tipo = ""
        status_snmp = s.get("status", "")
        sem_video = (
            (leitura is not None and canal in leitura.sem_video)
            or status_snmp == "Unconnect"
            or (status_snmp == "Empty" and confiavel and bool(ip_snmp))
        )
        com_video = status_snmp == "Connected" or (leitura is not None and canal not in leitura.sem_video)
        linhas.append(
            {
                "canal": canal,
                "nome": zbx.get("name")
                or ("" if titulo_padrao(titulo) else titulo)
                or conf.get("equipamento", "")
                or ("" if titulo_padrao(s.get("nome", "")) else s.get("nome", "")),
                "tipo": tipo,
                "modelo": (cam.modelo if cam else "") or zbx.get("model", ""),
                "ip": (cam.ip if cam else "") or zbx.get("ip", "") or conf.get("ip_camera", "") or ip_snmp,
                "mac": cam.mac if cam else "",
                "sem_video": sem_video,
                "video": "sem" if sem_video else ("ok" if com_video else ""),
                "status": zbx.get("status", ""),
                "situacao": conf.get("situacao", ""),
                "sem_imagem": conf.get("imagem") == "sem",
                "gravacao": v.get("gravacao", ""),
                "ultima_gravacao": v.get("ultima_gravacao"),
                "imagem": v.get("imagem", ""),
                "verificado_em": _mais_recente(v.get("gravacao_em"), v.get("imagem_em")),
                "miniatura": bool(v.get("miniatura")),
            }
        )
    return linhas
