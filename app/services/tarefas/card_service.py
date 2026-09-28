"""Casos de uso do card aberto no painel: detalhes, título, responsável e histórico.

Editar título ou responsável sobe a versão do card (bloqueio otimista do
modelo) e a revisão do board, para que outros boards abertos recarreguem.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.tarefas import Board, Card, CardActivity, Workspace, iso_utc
from app.models.user import User
from app.services.tarefas.board_service import (
    BoardNaoEncontradoError,
    CardNaoEncontradoError,
    ConflitoError,
    obter_board,
    subir_revisao,
)

log = structlog.get_logger(__name__)

LIMITE_HISTORICO = 30

TituloCard = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CardEditIn(BaseModel):
    """Edição do card: só os campos enviados mudam. ``assignee_id: null`` remove o responsável."""

    model_config = ConfigDict(extra="forbid")

    version: int
    title: TituloCard | None = None
    assignee_id: int | None = None


class ResponsavelInvalidoError(ValueError):
    """Responsável inexistente ou desativado."""


def obter_card(card_id: int) -> Card:
    """Card ativo, de board e workspace ativos.

    Raises:
        CardNaoEncontradoError: Card inexistente, excluído ou fora de board ativo.
    """
    card = db.session.get(Card, card_id)
    if card is None or card.deleted_at is not None:
        raise CardNaoEncontradoError(f"Card {card_id} não encontrado")
    try:
        obter_board(card.board_id)
    except BoardNaoEncontradoError as exc:
        raise CardNaoEncontradoError(f"Card {card_id} não encontrado") from exc
    return card


def _nome(user_id: int) -> str | None:
    return db.session.execute(select(User.name).where(User.id == user_id)).scalar_one_or_none()


def detalhar(card_id: int) -> dict[str, Any]:
    """Dados do painel do card: campos, board, workspace e histórico recente.

    Raises:
        CardNaoEncontradoError: Card inexistente ou excluído.
    """
    card = obter_card(card_id)
    board = db.session.get(Board, card.board_id)
    workspace = db.session.get(Workspace, board.workspace_id)
    historico = db.session.execute(
        select(CardActivity, User.name)
        .outerjoin(User, CardActivity.actor_id == User.id)
        .where(CardActivity.card_id == card.id)
        .order_by(CardActivity.at.desc(), CardActivity.id.desc())
        .limit(LIMITE_HISTORICO)
    ).all()
    return {
        "id": card.id,
        "title": card.title,
        "status": card.status,
        "version": card.version,
        "assignee": {"id": card.assignee_id, "name": _nome(card.assignee_id)} if card.assignee_id else None,
        "created_by": {"id": card.created_by, "name": _nome(card.created_by)},
        "created_at": iso_utc(card.created_at),
        "board": {"id": board.id, "name": board.name},
        "workspace": {"id": workspace.id, "name": workspace.name},
        "activity": [
            {
                "action": a.action,
                "from_status": a.from_status,
                "to_status": a.to_status,
                "actor": nome,
                "at": iso_utc(a.at),
            }
            for a, nome in historico
        ],
    }


def usuarios_ativos() -> list[dict[str, Any]]:
    """Usuários ativos (id e nome) para escolher o responsável."""
    linhas = db.session.execute(select(User.id, User.name).where(User.is_active.is_(True)).order_by(User.name)).all()
    return [{"id": uid, "name": nome} for uid, nome in linhas]


def editar(card_id: int, dados: CardEditIn, user_id: int) -> Card:
    """Altera título e/ou responsável do card.

    Raises:
        CardNaoEncontradoError: Card inexistente ou excluído.
        ConflitoError: Versão enviada diferente da atual.
        ResponsavelInvalidoError: Responsável inexistente ou desativado.
    """
    card = obter_card(card_id)
    if card.version != dados.version:
        raise ConflitoError("O card foi alterado por outra pessoa.")
    campos = dados.model_fields_set - {"version"}
    muda_titulo = "title" in campos and dados.title is not None and dados.title != card.title
    muda_responsavel = "assignee_id" in campos and dados.assignee_id != card.assignee_id
    # Valida tudo antes de alterar o card: uma consulta depois de alterar
    # faria autoflush e a versão subiria duas vezes numa só edição.
    if muda_responsavel and dados.assignee_id is not None:
        ativo = db.session.execute(
            select(User.id).where(User.id == dados.assignee_id, User.is_active.is_(True))
        ).scalar_one_or_none()
        if ativo is None:
            raise ResponsavelInvalidoError("Responsável inexistente ou desativado.")
    if not muda_titulo and not muda_responsavel:
        return card
    if muda_titulo:
        card.title = dados.title
        db.session.add(CardActivity(card_id=card.id, actor_id=user_id, action="edited"))
    if muda_responsavel:
        card.assignee_id = dados.assignee_id
        db.session.add(CardActivity(card_id=card.id, actor_id=user_id, action="assigned"))
    try:
        db.session.flush()
        subir_revisao(card.board_id)
        db.session.commit()
    except StaleDataError as exc:
        db.session.rollback()
        raise ConflitoError("O card foi alterado por outra pessoa.") from exc
    log.info("tarefas.card_editado", card_id=card.id, campos=sorted(campos), user_id=user_id)
    return card
