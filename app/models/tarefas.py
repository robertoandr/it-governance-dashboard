"""SQLAlchemy models do módulo Tarefas (Kanban com documentação no card).

Hierarquia: Workspace → Board → Card, e cada Card tem um documento Markdown,
comentários e histórico de atividade. As tabelas vivem no ``app.db`` junto
dos usuários (ADR 0006 do gerenciador-tarefas) e usam o prefixo
``tarefas_`` para não colidir com o restante do dashboard.

Acesso não depende de associação por workspace: vem do perfil do usuário
(``admin``/``gestor``/``operador``/``visualizador``, ADR 0007).

Atenção: o ``app.db`` é criado com ``db.create_all()``, que não altera
tabelas existentes. Mudança de coluna depois do deploy exige migração
explícita.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, func

from app.extensions import db

CARD_STATUS: tuple[str, ...] = ("backlog", "todo", "doing", "done")
CARD_ACTIONS: tuple[str, ...] = ("created", "moved", "edited", "assigned")
BOARD_PADRAO = "Principal"


def _agora() -> datetime:
    return datetime.now(UTC)


class Workspace(db.Model):
    """Agrupador de boards por projeto ou frente (ex.: Infra, Redes)."""

    __tablename__ = "tarefas_workspaces"

    id: int = db.Column(db.Integer, primary_key=True)
    name: str = db.Column(db.String(60), nullable=False)
    created_by: int = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    updated_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora, onupdate=_agora)
    deleted_at: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)

    boards = db.relationship("Board", back_populates="workspace", lazy="selectin", order_by="Board.id")

    @property
    def boards_ativos(self) -> list[Board]:
        """Boards não excluídos, na ordem de criação."""
        return [b for b in self.boards if b.deleted_at is None]


# Nome único (sem diferenciar maiúsculas) apenas entre os ativos: um
# workspace excluído não bloqueia a recriação com o mesmo nome.
db.Index(
    "uq_tarefas_workspaces_nome_ativo",
    func.lower(Workspace.name),
    unique=True,
    sqlite_where=Workspace.deleted_at.is_(None),
    postgresql_where=Workspace.deleted_at.is_(None),
)


class Board(db.Model):
    """Quadro Kanban com as colunas fixas de ``CARD_STATUS``."""

    __tablename__ = "tarefas_boards"

    id: int = db.Column(db.Integer, primary_key=True)
    workspace_id: int = db.Column(db.Integer, db.ForeignKey("tarefas_workspaces.id"), nullable=False, index=True)
    name: str = db.Column(db.String(80), nullable=False)
    # Sobe a cada escrita em qualquer card do board; o front consulta só
    # este número para saber se precisa recarregar (ADR 0008).
    revision: int = db.Column(db.Integer, nullable=False, default=0)
    created_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    deleted_at: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)

    workspace = db.relationship("Workspace", back_populates="boards")


class Card(db.Model):
    """Tarefa do Kanban."""

    __tablename__ = "tarefas_cards"
    __table_args__ = (
        CheckConstraint(f"status IN {CARD_STATUS!r}", name="ck_tarefas_cards_status"),
        db.Index("ix_tarefas_cards_board_status_pos", "board_id", "status", "position"),
    )

    id: int = db.Column(db.Integer, primary_key=True)
    board_id: int = db.Column(db.Integer, db.ForeignKey("tarefas_boards.id"), nullable=False)
    title: str = db.Column(db.String(200), nullable=False)
    status: str = db.Column(db.String(10), nullable=False, default="backlog")
    # Intervalos de 1024 entre cards; a coluna é renumerada quando acaba o espaço.
    position: int = db.Column(db.Integer, nullable=False, default=0)
    assignee_id: int | None = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by: int = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    version: int = db.Column(db.Integer, nullable=False, default=1)
    created_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    updated_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora, onupdate=_agora)
    deleted_at: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)


class CardDocument(db.Model):
    """Documento Markdown do card (1:1), com controle otimista de versão."""

    __tablename__ = "tarefas_card_documents"

    card_id: int = db.Column(db.Integer, db.ForeignKey("tarefas_cards.id"), primary_key=True)
    content_md: str = db.Column(db.Text, nullable=False, default="")
    version: int = db.Column(db.Integer, nullable=False, default=1)
    updated_by: int = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora, onupdate=_agora)


class CardComment(db.Model):
    """Comentário em um card."""

    __tablename__ = "tarefas_card_comments"
    __table_args__ = (db.Index("ix_tarefas_card_comments_card_created", "card_id", "created_at"),)

    id: int = db.Column(db.Integer, primary_key=True)
    card_id: int = db.Column(db.Integer, db.ForeignKey("tarefas_cards.id"), nullable=False)
    author_id: int = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body_md: str = db.Column(db.Text, nullable=False)
    created_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    edited_at: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)
    deleted_at: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)


class CardActivity(db.Model):
    """Histórico do card: criação, movimentação, edição e atribuição."""

    __tablename__ = "tarefas_card_activity"
    __table_args__ = (CheckConstraint(f"action IN {CARD_ACTIONS!r}", name="ck_tarefas_card_activity_action"),)

    id: int = db.Column(db.Integer, primary_key=True)
    card_id: int = db.Column(db.Integer, db.ForeignKey("tarefas_cards.id"), nullable=False, index=True)
    actor_id: int = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action: str = db.Column(db.String(20), nullable=False)
    from_status: str | None = db.Column(db.String(10), nullable=True)
    to_status: str | None = db.Column(db.String(10), nullable=True)
    at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
