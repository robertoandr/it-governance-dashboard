"""Classificações de dispositivo cadastradas na página Rede.

Cada classificação vira um tipo de ativo aceito (rótulo e sigla para o padrão
de nomes) e, se tiver palavras-chave, reclassifica sozinha os hosts
descobertos que batem com elas.
"""

from __future__ import annotations

import re
import unicodedata

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.rede import ClassificacaoDispositivo
from itgov.api.v1.rede_descoberta import SIGLA_TIPO, RegraClassificacao
from itgov.models.ativo import TIPO_LABELS, definir_tipos_extras

log = structlog.get_logger(__name__)


def carregar() -> list[ClassificacaoDispositivo]:
    """Lê as classificações do banco e atualiza os tipos aceitos neste processo.

    Returns:
        Classificações em ordem de rótulo; lista vazia se o banco falhar.
    """
    try:
        itens = ClassificacaoDispositivo.query.order_by(ClassificacaoDispositivo.rotulo).all()
    except SQLAlchemyError as exc:
        log.warning("classificacoes.leitura_falhou", erro=str(exc))
        db.session.rollback()
        return []
    definir_tipos_extras({c.chave: c.rotulo for c in itens})
    return itens


def regras(itens: list[ClassificacaoDispositivo]) -> list[RegraClassificacao]:
    """Regras de reclassificação das que têm palavras-chave."""
    return [RegraClassificacao(c.chave, c.rotulo, c.lista_palavras) for c in itens if c.lista_palavras]


def siglas(itens: list[ClassificacaoDispositivo]) -> dict[str, str]:
    """Tipo → sigla das classificações, para ``sugerir_nome``."""
    return {c.chave: c.sigla for c in itens}


def chave_de(rotulo: str) -> str:
    """Chave do tipo a partir do rótulo: minúsculas, sem acento, ``_`` no lugar de espaço.

    Example:
        ``chave_de("Relógio de ponto")`` → ``"relogio_de_ponto"``.
    """
    sem_acento = unicodedata.normalize("NFKD", rotulo).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")[:20].rstrip("_")


def validar(rotulo: str, sigla: str, existentes: list[ClassificacaoDispositivo]) -> str | None:
    """Confere os dados de uma classificação nova.

    Returns:
        Mensagem de erro, ou None se estiver tudo certo.
    """
    if not 3 <= len(rotulo) <= 60:
        return "O nome precisa ter de 3 a 60 caracteres."
    if not re.fullmatch(r"[A-Z0-9]{2,4}", sigla):
        return "A sigla precisa ter de 2 a 4 letras ou números (ex.: PON)."
    chave = chave_de(rotulo)
    if not chave:
        return "O nome precisa ter letras ou números."
    if chave in TIPO_LABELS or any(c.chave == chave for c in existentes):
        return f"Já existe a classificação {rotulo}."
    if sigla in SIGLA_TIPO.values() or any(c.sigla == sigla for c in existentes):
        return f"A sigla {sigla} já está em uso."
    return None
