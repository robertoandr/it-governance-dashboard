"""Casos de uso de workspaces do módulo Tarefas.

Criar um workspace cria junto o board padrão (RF02). Exclusão é soft
delete (ADR-003 do dashboard) e leva junto os boards do workspace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import structlog
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.tarefas import BOARD_PADRAO, Board, Workspace, chave_nome

log = structlog.get_logger(__name__)

NomeWorkspace = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=60)]


class WorkspaceIn(BaseModel):
    """Payload de criação e renomeação de workspace."""

    model_config = ConfigDict(extra="forbid")

    name: NomeWorkspace


class WorkspaceNaoEncontradoError(LookupError):
    """Workspace inexistente ou excluído."""


class WorkspaceDuplicadoError(ValueError):
    """Já existe um workspace ativo com o mesmo nome."""


LIMITE_MAXIMO = 1000


def normalizar_paginacao(limit: int, offset: int) -> tuple[int, int]:
    """Ajusta ``limit`` a 1–1000 e ``offset`` a ≥ 0 (ADR-003 do dashboard).

    Args:
        limit: Tamanho de página pedido.
        offset: Deslocamento pedido.

    Returns:
        Tupla ``(limit, offset)`` efetivamente aplicada.
    """
    return max(1, min(limit, LIMITE_MAXIMO)), max(0, offset)


def _ativos() -> Select[tuple[Workspace]]:
    return select(Workspace).where(Workspace.deleted_at.is_(None))


def _buscar(workspace_id: int) -> Workspace:
    ws = db.session.execute(_ativos().where(Workspace.id == workspace_id)).scalar_one_or_none()
    if ws is None:
        raise WorkspaceNaoEncontradoError(f"Workspace {workspace_id} não encontrado")
    return ws


def _nome_em_uso(nome: str, ignorar_id: int | None = None) -> bool:
    stmt = _ativos().where(Workspace.name_key == chave_nome(nome))
    if ignorar_id is not None:
        stmt = stmt.where(Workspace.id != ignorar_id)
    return db.session.execute(stmt).first() is not None


def _commit_ou_duplicado(nome: str) -> None:
    # A checagem prévia cobre o caso comum; o índice único cobre a corrida
    # entre duas criações simultâneas. Outras violações (FK, NOT NULL) não
    # são "nome duplicado" e sobem como estão.
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        if "name_key" not in str(exc.orig) and "uq_tarefas_workspaces_nome_ativo" not in str(exc.orig):
            log.error("tarefas.workspace_integridade", erro=str(exc.orig))
            raise
        raise WorkspaceDuplicadoError(f"Já existe um workspace chamado '{nome}'") from exc


def listar(limit: int = 100, offset: int = 0) -> tuple[list[Workspace], int]:
    """Lista workspaces ativos em ordem alfabética.

    Args:
        limit: Máximo de itens; ajustado por ``normalizar_paginacao``.
        offset: Deslocamento; ajustado por ``normalizar_paginacao``.

    Returns:
        Tupla ``(itens, total)``.
    """
    limit, offset = normalizar_paginacao(limit, offset)
    total = db.session.execute(select(func.count()).select_from(_ativos().subquery())).scalar_one()
    itens = db.session.execute(_ativos().order_by(Workspace.name_key).limit(limit).offset(offset)).scalars().all()
    return list(itens), total


def criar(dados: WorkspaceIn, user_id: int) -> Workspace:
    """Cria o workspace e o board padrão na mesma transação.

    Args:
        dados: Nome validado.
        user_id: Usuário que está criando.

    Returns:
        Workspace criado.

    Raises:
        WorkspaceDuplicadoError: Nome já usado por um workspace ativo.
    """
    if _nome_em_uso(dados.name):
        raise WorkspaceDuplicadoError(f"Já existe um workspace chamado '{dados.name}'")
    ws = Workspace(name=dados.name, created_by=user_id)
    ws.boards.append(Board(name=BOARD_PADRAO))
    db.session.add(ws)
    _commit_ou_duplicado(dados.name)
    log.info("tarefas.workspace_criado", workspace_id=ws.id, user_id=user_id)
    return ws


def renomear(workspace_id: int, dados: WorkspaceIn, user_id: int) -> Workspace:
    """Renomeia um workspace ativo.

    Args:
        workspace_id: Workspace alvo.
        dados: Novo nome validado.
        user_id: Usuário que está renomeando.

    Returns:
        Workspace atualizado.

    Raises:
        WorkspaceNaoEncontradoError: Workspace inexistente ou excluído.
        WorkspaceDuplicadoError: Nome já usado por outro workspace ativo.
    """
    ws = _buscar(workspace_id)
    if _nome_em_uso(dados.name, ignorar_id=ws.id):
        raise WorkspaceDuplicadoError(f"Já existe um workspace chamado '{dados.name}'")
    ws.name = dados.name
    _commit_ou_duplicado(dados.name)
    log.info("tarefas.workspace_renomeado", workspace_id=ws.id, user_id=user_id)
    return ws


def excluir(workspace_id: int, user_id: int) -> None:
    """Exclui (soft delete) o workspace e os boards dele.

    Args:
        workspace_id: Workspace alvo.
        user_id: Usuário que está excluindo.

    Raises:
        WorkspaceNaoEncontradoError: Workspace inexistente ou já excluído.
    """
    ws = _buscar(workspace_id)
    agora = datetime.now(UTC)
    ws.deleted_at = agora
    for board in ws.boards_ativos:
        board.deleted_at = agora
    db.session.commit()
    log.info("tarefas.workspace_excluido", workspace_id=ws.id, user_id=user_id)
