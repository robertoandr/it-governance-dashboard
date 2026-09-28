"""Casos de uso do board Kanban: listar, criar e mover cards.

Ordem dentro da coluna: ``Card.position`` com intervalos de ``INTERVALO``.
Mover entre dois cards usa o ponto médio; quando não há mais espaço, a
coluna é renumerada. Toda escrita incrementa ``Board.revision`` na mesma
transação, e o front só recarrega o board quando a revisão muda (ADR 0008).
"""

from __future__ import annotations

from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import Select, func, select, update
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.tarefas import CARD_STATUS, Board, Card, CardActivity, CardComment, CardDocument, Workspace
from app.models.user import User

log = structlog.get_logger(__name__)

INTERVALO = 1024

StatusCard = Literal["backlog", "todo", "doing", "done"]
TituloCard = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CardIn(BaseModel):
    """Payload de criação rápida de card."""

    model_config = ConfigDict(extra="forbid")

    title: TituloCard
    status: StatusCard = "backlog"


class MoverIn(BaseModel):
    """Payload de movimentação: coluna de destino e vizinhos na nova posição.

    ``before_id`` é o card que fica logo acima; ``after_id``, o que fica logo
    abaixo. Ambos ``None`` significa coluna vazia.
    """

    model_config = ConfigDict(extra="forbid")

    status: StatusCard
    before_id: int | None = None
    after_id: int | None = None
    version: int


class BoardNaoEncontradoError(LookupError):
    """Board inexistente, excluído ou de workspace excluído."""


class CardNaoEncontradoError(LookupError):
    """Card inexistente ou excluído."""


class ConflitoError(Exception):
    """O board mudou desde que o cliente o carregou; é preciso recarregar."""


# ── Consultas ────────────────────────────────────────────────────────────────


def _boards_ativos() -> Select[tuple[Board]]:
    return (
        select(Board)
        .join(Workspace, Board.workspace_id == Workspace.id)
        .where(Board.deleted_at.is_(None), Workspace.deleted_at.is_(None))
    )


def obter_board(board_id: int) -> Board:
    """Board ativo (e de workspace ativo).

    Raises:
        BoardNaoEncontradoError: Board inexistente ou excluído.
    """
    board = db.session.execute(_boards_ativos().where(Board.id == board_id)).scalar_one_or_none()
    if board is None:
        raise BoardNaoEncontradoError(f"Board {board_id} não encontrado")
    return board


def revisao(board_id: int) -> int:
    """Número de revisão do board (consulta leve da atualização automática).

    Raises:
        BoardNaoEncontradoError: Board inexistente ou excluído.
    """
    valor = db.session.execute(
        _boards_ativos().with_only_columns(Board.revision).where(Board.id == board_id)
    ).scalar_one_or_none()
    if valor is None:
        raise BoardNaoEncontradoError(f"Board {board_id} não encontrado")
    return valor


def _cards_da_coluna(board_id: int, status: str) -> list[Card]:
    return list(
        db.session.execute(
            select(Card)
            .where(Card.board_id == board_id, Card.status == status, Card.deleted_at.is_(None))
            .order_by(Card.position, Card.id)
        )
        .scalars()
        .all()
    )


def listar_cards(board_id: int) -> dict[str, list[dict[str, object]]]:
    """Cards ativos do board agrupados por coluna, na ordem de exibição.

    Returns:
        ``{status: [card, ...]}`` com as 4 colunas sempre presentes.

    Raises:
        BoardNaoEncontradoError: Board inexistente ou excluído.
    """
    obter_board(board_id)
    comentarios = (
        select(CardComment.card_id, func.count().label("n"))
        .where(CardComment.deleted_at.is_(None))
        .group_by(CardComment.card_id)
        .subquery()
    )
    linhas = db.session.execute(
        select(Card, User.name, comentarios.c.n, CardDocument.card_id)
        .outerjoin(User, Card.assignee_id == User.id)
        .outerjoin(comentarios, comentarios.c.card_id == Card.id)
        .outerjoin(CardDocument, (CardDocument.card_id == Card.id) & (CardDocument.content_md != ""))
        .where(Card.board_id == board_id, Card.deleted_at.is_(None))
        .order_by(Card.position, Card.id)
    ).all()
    colunas: dict[str, list[dict[str, object]]] = {s: [] for s in CARD_STATUS}
    for card, responsavel, n_comentarios, doc in linhas:
        colunas[card.status].append(
            {
                "id": card.id,
                "title": card.title,
                "status": card.status,
                "position": card.position,
                "version": card.version,
                "assignee": {"id": card.assignee_id, "name": responsavel} if card.assignee_id else None,
                "has_document": doc is not None,
                "comment_count": n_comentarios or 0,
            }
        )
    return colunas


# ── Escritas ─────────────────────────────────────────────────────────────────


def _subir_revisao(board_id: int) -> None:
    # Incremento no SQL, não em Python: duas escritas simultâneas não perdem
    # uma à outra.
    db.session.execute(update(Board).where(Board.id == board_id).values(revision=Board.revision + 1))


def criar_card(board_id: int, dados: CardIn, user_id: int) -> Card:
    """Cria o card no topo da coluna pedida.

    Raises:
        BoardNaoEncontradoError: Board inexistente ou excluído.
    """
    obter_board(board_id)
    topo = db.session.execute(
        select(func.min(Card.position)).where(
            Card.board_id == board_id, Card.status == dados.status, Card.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    card = Card(
        board_id=board_id,
        title=dados.title,
        status=dados.status,
        position=0 if topo is None else topo - INTERVALO,
        created_by=user_id,
    )
    db.session.add(card)
    db.session.flush()
    db.session.add(CardActivity(card_id=card.id, actor_id=user_id, action="created", to_status=dados.status))
    _subir_revisao(board_id)
    db.session.commit()
    log.info("tarefas.card_criado", card_id=card.id, board_id=board_id, user_id=user_id)
    return card


def _renumerar(cards: list[Card]) -> None:
    for i, c in enumerate(cards):
        c.position = (i + 1) * INTERVALO


def _vizinhos_batem(indice: dict[int, int], total: int, before_id: int | None, after_id: int | None) -> bool:
    """Confere se os vizinhos que o cliente viu ainda são vizinhos no banco.

    ``indice`` mapeia id → posição na coluna de destino (sem o card movido).
    """
    if before_id is None and after_id is None:
        return total == 0
    if before_id is None:
        return indice[after_id] == 0  # type: ignore[index]
    if after_id is None:
        return indice[before_id] == total - 1
    return indice[after_id] == indice[before_id] + 1


def _nova_posicao(board_id: int, card: Card, dados: MoverIn) -> int:
    """Posição entre os vizinhos informados, validando que o cliente não está defasado."""
    coluna = [c for c in _cards_da_coluna(board_id, dados.status) if c.id != card.id]
    indice = {c.id: i for i, c in enumerate(coluna)}

    for vizinho in (dados.before_id, dados.after_id):
        if vizinho is not None and vizinho not in indice:
            raise ConflitoError("O card vizinho não está mais nessa coluna.")
    if not _vizinhos_batem(indice, len(coluna), dados.before_id, dados.after_id):
        raise ConflitoError("A ordem da coluna mudou.")

    if not coluna:
        return 0
    if dados.before_id is None:
        return coluna[0].position - INTERVALO
    antes = coluna[indice[dados.before_id]]
    if dados.after_id is None:
        return antes.position + INTERVALO
    depois = coluna[indice[dados.after_id]]
    if depois.position - antes.position < 2:
        _renumerar(coluna)
    return (antes.position + depois.position) // 2


def mover_card(card_id: int, dados: MoverIn, user_id: int) -> tuple[Card, int]:
    """Move o card para a coluna e posição pedidas.

    Returns:
        Tupla ``(card atualizado, nova revisão do board)``.

    Raises:
        CardNaoEncontradoError: Card inexistente, excluído ou de board excluído.
        ConflitoError: Versão do card ou vizinhos não batem com o banco.
    """
    card = db.session.get(Card, card_id)
    if card is None or card.deleted_at is not None:
        raise CardNaoEncontradoError(f"Card {card_id} não encontrado")
    try:
        obter_board(card.board_id)
    except BoardNaoEncontradoError as exc:
        raise CardNaoEncontradoError(f"Card {card_id} não encontrado") from exc
    if card.version != dados.version:
        raise ConflitoError("O card foi alterado por outra pessoa.")
    if card.id in (dados.before_id, dados.after_id):
        raise ConflitoError("Um card não pode ser vizinho de si mesmo.")

    de = card.status
    card.position = _nova_posicao(card.board_id, card, dados)
    card.status = dados.status
    db.session.add(
        CardActivity(card_id=card.id, actor_id=user_id, action="moved", from_status=de, to_status=card.status)
    )
    try:
        # O flush envia o UPDATE do card com "WHERE version = ?"; precisa
        # estar dentro do try porque o próximo execute também faria autoflush.
        db.session.flush()
        _subir_revisao(card.board_id)
        db.session.commit()
    except StaleDataError as exc:
        db.session.rollback()
        raise ConflitoError("O card foi alterado por outra pessoa.") from exc
    log.info("tarefas.card_movido", card_id=card.id, de=de, para=card.status, user_id=user_id)
    return card, revisao(card.board_id)
