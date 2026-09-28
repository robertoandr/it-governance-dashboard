"""Comentários do card (RF04, decisão D2 / ADR 0002).

Editar é só do autor; excluir é do autor ou de quem pode moderar (admin).
Criar e excluir sobem a revisão do board, porque o card mostra a contagem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select, update

from app.extensions import db
from app.models.tarefas import Board, CardComment, em_utc
from app.models.user import User
from app.services.tarefas import markdown
from app.services.tarefas.card_service import obter_card

log = structlog.get_logger(__name__)

TextoComentario = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]


class ComentarioIn(BaseModel):
    """Texto do comentário (Markdown)."""

    model_config = ConfigDict(extra="forbid")

    body_md: TextoComentario


class ComentarioNaoEncontradoError(LookupError):
    """Comentário inexistente, excluído ou de card inativo."""


class SemPermissaoError(PermissionError):
    """O usuário não pode alterar este comentário."""


def _iso(valor: Any) -> str | None:
    utc = em_utc(valor)
    return utc.isoformat() if utc else None


def serializar(comentario: CardComment, autor: str | None) -> dict[str, Any]:
    """Formato do comentário na API (sem as permissões, que dependem de quem pede)."""
    return {
        "id": comentario.id,
        "author": {"id": comentario.author_id, "name": autor},
        "body_md": comentario.body_md,
        "html": markdown.renderizar(comentario.body_md),
        "created_at": _iso(comentario.created_at),
        "edited_at": _iso(comentario.edited_at),
    }


def _subir_revisao(board_id: int) -> None:
    db.session.execute(update(Board).where(Board.id == board_id).values(revision=Board.revision + 1))


def listar(card_id: int) -> list[dict[str, Any]]:
    """Comentários ativos do card, do mais antigo para o mais novo.

    Raises:
        CardNaoEncontradoError: Card inexistente ou excluído.
    """
    obter_card(card_id)
    linhas = db.session.execute(
        select(CardComment, User.name)
        .outerjoin(User, CardComment.author_id == User.id)
        .where(CardComment.card_id == card_id, CardComment.deleted_at.is_(None))
        .order_by(CardComment.created_at, CardComment.id)
    ).all()
    return [serializar(c, nome) for c, nome in linhas]


def criar(card_id: int, dados: ComentarioIn, user_id: int) -> dict[str, Any]:
    """Cria um comentário.

    Raises:
        CardNaoEncontradoError: Card inexistente ou excluído.
    """
    card = obter_card(card_id)
    comentario = CardComment(card_id=card.id, author_id=user_id, body_md=dados.body_md)
    db.session.add(comentario)
    _subir_revisao(card.board_id)
    db.session.commit()
    log.info("tarefas.comentario_criado", card_id=card.id, comentario_id=comentario.id, user_id=user_id)
    autor = db.session.execute(select(User.name).where(User.id == user_id)).scalar_one_or_none()
    return serializar(comentario, autor)


def _buscar(comentario_id: int) -> CardComment:
    comentario = db.session.get(CardComment, comentario_id)
    if comentario is None or comentario.deleted_at is not None:
        raise ComentarioNaoEncontradoError(f"Comentário {comentario_id} não encontrado")
    try:
        obter_card(comentario.card_id)
    except LookupError as exc:
        raise ComentarioNaoEncontradoError(f"Comentário {comentario_id} não encontrado") from exc
    return comentario


def editar(comentario_id: int, dados: ComentarioIn, user_id: int) -> dict[str, Any]:
    """Edita o próprio comentário.

    Raises:
        ComentarioNaoEncontradoError: Comentário inexistente ou excluído.
        SemPermissaoError: O usuário não é o autor.
    """
    comentario = _buscar(comentario_id)
    if comentario.author_id != user_id:
        raise SemPermissaoError("Só o autor pode editar o comentário.")
    comentario.body_md = dados.body_md
    comentario.edited_at = datetime.now(UTC)
    db.session.commit()
    log.info("tarefas.comentario_editado", comentario_id=comentario.id, user_id=user_id)
    autor = db.session.execute(select(User.name).where(User.id == user_id)).scalar_one_or_none()
    return serializar(comentario, autor)


def excluir(comentario_id: int, user_id: int, pode_moderar: bool) -> None:
    """Exclui (soft delete) o comentário.

    Args:
        comentario_id: Comentário alvo.
        user_id: Quem está excluindo.
        pode_moderar: Se o usuário pode excluir comentários de outras pessoas.

    Raises:
        ComentarioNaoEncontradoError: Comentário inexistente ou excluído.
        SemPermissaoError: Não é o autor nem pode moderar.
    """
    comentario = _buscar(comentario_id)
    if comentario.author_id != user_id and not pode_moderar:
        raise SemPermissaoError("Só o autor ou um admin pode excluir o comentário.")
    comentario.deleted_at = datetime.now(UTC)
    card = obter_card(comentario.card_id)
    _subir_revisao(card.board_id)
    db.session.commit()
    log.info("tarefas.comentario_excluido", comentario_id=comentario.id, user_id=user_id, moderacao=pode_moderar)
