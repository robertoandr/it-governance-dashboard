"""Casos de concorrência do painel que a API não consegue provocar sozinha."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from sqlalchemy import update

from app.extensions import db
from app.models.tarefas import Card, CardDocument
from app.services.tarefas import board_service, card_service, document_service
from app.services.tarefas import workspace_service as ws_svc


@pytest.fixture
def ctx(factory_app: Flask) -> Iterator[None]:
    with factory_app.app_context():
        yield


@pytest.fixture
def admin_id(usuario_id: Callable[..., int]) -> int:
    return usuario_id("admin")


@pytest.fixture
def card(ctx: None, admin_id: int) -> Card:
    ws = ws_svc.criar(ws_svc.WorkspaceIn(name="Infraestrutura"), user_id=admin_id)
    return board_service.criar_card(ws.boards_ativos[0].id, board_service.CardIn(title="A"), user_id=admin_id)


def test_edicao_concorrente_do_card_vira_conflito(card: Card, admin_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outra conexão grava o card entre a checagem de versão e o flush."""
    original = card_service.obter_card

    def com_corrida(card_id: int) -> Card:
        c = original(card_id)
        tabela = Card.__table__
        db.session.execute(update(tabela).where(tabela.c.id == card_id).values(version=tabela.c.version + 1))
        return c

    monkeypatch.setattr(card_service, "obter_card", com_corrida)
    with pytest.raises(board_service.ConflitoError):
        card_service.editar(card.id, card_service.CardEditIn(version=card.version, title="B"), user_id=admin_id)


def test_documento_criado_ao_mesmo_tempo_vira_conflito(
    card: Card, admin_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duas pessoas criam o primeiro documento no mesmo instante: a segunda recebe o texto da primeira."""
    original = document_service._buscar
    chamadas = {"n": 0}

    def outra_pessoa_criou_antes(card_id: int) -> CardDocument | None:
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            # outra conexão, com commit próprio (como outro worker do Gunicorn)
            with db.engine.begin() as outra:
                outra.execute(
                    CardDocument.__table__.insert().values(
                        card_id=card_id, content_md="da outra pessoa", version=1, updated_by=admin_id
                    )
                )
            return None  # a nossa leitura aconteceu antes da gravação dela
        return original(card_id)

    monkeypatch.setattr(document_service, "_buscar", outra_pessoa_criou_antes)
    with pytest.raises(document_service.DocumentoConflitoError) as erro:
        document_service.salvar(card.id, document_service.DocumentoIn(content_md="meu", version=0), user_id=admin_id)
    assert erro.value.atual["content_md"] == "da outra pessoa"
