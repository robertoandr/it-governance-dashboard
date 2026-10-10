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


def canais_do_card(card: dict[str, Any], acesso: dict[str, Any] | None, manual: int | None) -> dict[str, Any] | None:
    """Capacidade, canais em uso, livres e canais sem vídeo de um card de gravador.

    Canal em uso: tem câmera no Zabbix (tag ``canal``), tem vídeo, ou tem nome
    dado no gravador. Canal sem vídeo só vira alerta se estiver em uso.

    Args:
        card: Card de ``montar_visao``.
        acesso: Item de ``leituras()`` do IP do gravador (ou None).
        manual: Capacidade informada à mão (``DvrUnidade.canais``).

    Returns:
        ``{"total", "usados", "livres", "sem_video": [{"canal", "nome"}], "fonte", "erro"}``,
        ou None quando não há como saber a capacidade.
    """
    leitura: LeituraGravador | None = (acesso or {}).get("leitura")
    no_zabbix = {int(d["canal"]) for d in card["dispositivos"] if str(d.get("canal", "")).isdigit()}
    modelo = leitura.modelo if leitura else ((card.get("gravador_host") or {}).get("model") or "")
    if manual:
        total, fonte = manual, "informado"
    elif leitura and leitura.canais:
        total, fonte = leitura.canais, "gravador"
    elif capacidade_do_modelo(modelo):
        total, fonte = capacidade_do_modelo(modelo), "modelo"
    else:
        return None
    usados = set(no_zabbix)
    sem_video: list[dict[str, Any]] = []
    if leitura:
        for canal in range(1, total + 1):
            nome = leitura.titulos.get(canal, "")
            em_uso = canal in no_zabbix or not titulo_padrao(nome) or canal not in leitura.sem_video
            if em_uso:
                usados.add(canal)
                if canal in leitura.sem_video:
                    sem_video.append({"canal": canal, "nome": nome})
    usados = {c for c in usados if c <= total}
    return {
        "total": total,
        "usados": len(usados),
        "livres": max(total - len(usados), 0),
        "sem_video": sem_video,
        "fonte": fonte,
        "modelo": modelo,
        "erro": (acesso or {}).get("erro", "") if not leitura else "",
    }
