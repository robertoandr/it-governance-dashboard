"""Documento Markdown do card, com controle de versão (nunca perder texto).

O cliente envia a versão que tinha ao começar a editar. Se outra pessoa
salvou antes, a gravação é recusada (``DocumentoConflitoError``) com o
conteúdo atual, e o cliente decide sem descartar o texto local.
Versão 0 = documento ainda não existe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from pydantic import AfterValidator, BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.tarefas import Board, CardActivity, CardDocument, em_utc
from app.models.user import User
from app.services.tarefas import markdown
from app.services.tarefas.card_service import obter_card

log = structlog.get_logger(__name__)

LIMITE_BYTES = 200 * 1024


def _limite_bytes(texto: str) -> str:
    if len(texto.encode("utf-8")) > LIMITE_BYTES:
        raise ValueError(f"o documento passa de {LIMITE_BYTES // 1024} KB")
    return texto


class DocumentoIn(BaseModel):
    """Gravação do documento."""

    model_config = ConfigDict(extra="forbid")

    content_md: Annotated[str, AfterValidator(_limite_bytes)]
    version: int


class DocumentoConflitoError(Exception):
    """Outra pessoa salvou uma versão mais nova do documento."""

    def __init__(self, atual: dict[str, Any]) -> None:
        super().__init__("O documento foi salvo por outra pessoa.")
        self.atual = atual


def _iso(valor: Any) -> str | None:
    utc = em_utc(valor)
    return utc.isoformat() if utc else None


def _serializar(doc: CardDocument | None) -> dict[str, Any]:
    if doc is None:
        return {"content_md": "", "html": "", "version": 0, "updated_by": None, "updated_at": None}
    nome = db.session.execute(select(User.name).where(User.id == doc.updated_by)).scalar_one_or_none()
    return {
        "content_md": doc.content_md,
        "html": markdown.renderizar(doc.content_md),
        "version": doc.version,
        "updated_by": nome,
        "updated_at": _iso(doc.updated_at),
    }


def _buscar(card_id: int) -> CardDocument | None:
    return db.session.execute(
        select(CardDocument).where(CardDocument.card_id == card_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()


def obter(card_id: int) -> dict[str, Any]:
    """Documento atual do card (vazio, versão 0, se ainda não existe).

    Raises:
        CardNaoEncontradoError: Card inexistente ou excluído.
    """
    obter_card(card_id)
    return _serializar(_buscar(card_id))


def salvar(card_id: int, dados: DocumentoIn, user_id: int) -> dict[str, Any]:
    """Grava o documento se a versão enviada ainda for a atual.

    Returns:
        Documento salvo (com a nova versão e o HTML renderizado).

    Raises:
        CardNaoEncontradoError: Card inexistente ou excluído.
        DocumentoConflitoError: Outra pessoa salvou antes (traz o conteúdo atual).
    """
    card = obter_card(card_id)
    atual = _buscar(card_id)
    tinha_conteudo = bool(atual and atual.content_md.strip())
    agora = datetime.now(UTC)

    if atual is None:
        if dados.version != 0:
            raise DocumentoConflitoError(_serializar(None))
        db.session.add(CardDocument(card_id=card_id, content_md=dados.content_md, version=1, updated_by=user_id))
        try:
            db.session.flush()
        except IntegrityError as exc:  # outra pessoa criou o documento no mesmo instante
            db.session.rollback()
            raise DocumentoConflitoError(_serializar(_buscar(card_id))) from exc
    else:
        # UPDATE condicional: a checagem de versão e a gravação são atômicas.
        resultado = db.session.execute(
            update(CardDocument)
            .where(CardDocument.card_id == card_id, CardDocument.version == dados.version)
            .values(
                content_md=dados.content_md,
                version=CardDocument.version + 1,
                updated_by=user_id,
                updated_at=agora,
            )
            .execution_options(synchronize_session=False)
        )
        if resultado.rowcount != 1:
            db.session.rollback()
            raise DocumentoConflitoError(_serializar(_buscar(card_id)))

    db.session.add(CardActivity(card_id=card_id, actor_id=user_id, action="edited"))
    # O board só mostra "Documentado"; só recarrega os outros quando isso muda,
    # e não a cada salvamento automático.
    if tinha_conteudo != bool(dados.content_md.strip()):
        db.session.execute(update(Board).where(Board.id == card.board_id).values(revision=Board.revision + 1))
    db.session.commit()
    log.info("tarefas.documento_salvo", card_id=card_id, user_id=user_id, bytes=len(dados.content_md.encode("utf-8")))
    return _serializar(_buscar(card_id))
