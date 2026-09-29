"""Limpeza dos dados de teste do módulo Tarefas no ``data/app.db``.

Usada pelos testes de ``tests/tarefas`` e pelo teste de ponta a ponta
(``e2e/``): cada um apaga só o que foi criado pelos próprios usuários,
identificados pelo prefixo do e-mail. Exige um app context ativo.
"""

from __future__ import annotations

from app.extensions import db
from app.models.tarefas import Board, Card, CardActivity, CardComment, CardDocument, Workspace
from app.models.user import User


def limpar_dados_de(prefixo_email: str) -> None:
    """Apaga workspaces, boards, cards e dependências criados pelos usuários do prefixo.

    Args:
        prefixo_email: Início do e-mail dos usuários de teste (ex.: ``"pytest-tarefas-"``).
    """
    usuarios = db.session.query(User.id).filter(User.email.like(f"{prefixo_email}%"))
    workspaces = db.session.query(Workspace.id).filter(Workspace.created_by.in_(usuarios))
    boards = db.session.query(Board.id).filter(Board.workspace_id.in_(workspaces))
    cards = db.session.query(Card.id).filter(Card.board_id.in_(boards))
    for modelo, filtro in (
        (CardActivity, CardActivity.card_id.in_(cards)),
        (CardComment, CardComment.card_id.in_(cards)),
        (CardDocument, CardDocument.card_id.in_(cards)),
        (Card, Card.id.in_(cards)),
        (Board, Board.id.in_(boards)),
        (Workspace, Workspace.id.in_(workspaces)),
    ):
        db.session.query(modelo).filter(filtro).delete(synchronize_session=False)
    db.session.commit()
