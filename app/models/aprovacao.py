"""Solicitações de alteração que esperam aprovação do super admin."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.extensions import db

log = structlog.get_logger(__name__)

PENDENTE = "pendente"
APROVADA = "aprovada"
REJEITADA = "rejeitada"
FALHOU = "falhou"
STATUS = (PENDENTE, APROVADA, REJEITADA, FALHOU)


class Solicitacao(db.Model):
    """Uma alteração de cadastro/configuração guardada até o super admin decidir.

    Guarda o endpoint e os dados do formulário originais; ao aprovar, a mesma
    rota é executada de novo em nome de quem pediu.
    """

    __tablename__ = "aprovacoes"

    id: int = db.Column(db.Integer, primary_key=True)
    resumo: str = db.Column(db.String(300), nullable=False)
    endpoint: str = db.Column(db.String(120), nullable=False)
    caminho: str = db.Column(db.String(500), nullable=False)
    view_args_json: str = db.Column(db.Text, nullable=False, default="{}")
    form_json: str = db.Column(db.Text, nullable=False, default="[]")
    arquivos_json: str = db.Column(db.Text, nullable=False, default="[]")
    corpo_json: str | None = db.Column(db.Text, nullable=True)
    solicitante_id: int = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    solicitante_nome: str = db.Column(db.String(120), nullable=False)
    status: str = db.Column(db.String(12), nullable=False, default=PENDENTE, index=True)
    resultado: str = db.Column(db.Text, nullable=False, default="")
    decidido_por: str = db.Column(db.String(120), nullable=False, default="")
    criado_em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    decidido_em: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)
    # Quem pediu já viu a situação atual? Volta a falso quando o super admin decide
    ciente: bool = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def view_args(self) -> dict[str, Any]:
        """Argumentos da URL (ex.: ``{"user_id": 3}``)."""
        return json.loads(self.view_args_json or "{}")

    @property
    def form(self) -> list[tuple[str, str]]:
        """Campos do formulário na ordem original (sem senhas)."""
        return [tuple(par) for par in json.loads(self.form_json or "[]")]  # type: ignore[misc]

    @property
    def arquivos(self) -> list[dict[str, str]]:
        """Arquivos enviados: campo, nome, tipo e conteúdo em base64."""
        return json.loads(self.arquivos_json or "[]")

    @property
    def corpo(self) -> Any:
        """Corpo JSON da requisição, para rotas que recebem JSON."""
        return json.loads(self.corpo_json) if self.corpo_json else None

    def __repr__(self) -> str:
        return f"<Solicitacao {self.id} {self.endpoint} {self.status}>"


def garantir_coluna_ciente(engine: Engine | None = None) -> None:
    """Cria a coluna ``aprovacoes.ciente`` em bancos anteriores a ela (idempotente).

    As solicitações antigas entram como vistas: ninguém recebe aviso de
    decisões tomadas antes do aviso existir.

    Args:
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
    """
    engine = engine or db.engine
    tabela = Solicitacao.__tablename__
    if "ciente" in {c["name"] for c in inspect(engine).get_columns(tabela)}:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN ciente BOOLEAN NOT NULL DEFAULT 1"))
        log.info("aprovacoes.coluna_adicionada", coluna="ciente")
    except OperationalError as exc:
        # Outro worker criou a coluna entre a inspeção e o ALTER
        if "duplicate column" not in str(exc).lower():
            raise
