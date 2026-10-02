"""Páginas HTML do módulo Tarefas (``/gov/tarefas``).

As páginas só renderizam; escritas passam pela API JSON em
``/api/v1/tarefas`` (``itgov/api/v1/tarefas.py``).
"""

from __future__ import annotations

from flask import Blueprint, Response, redirect, render_template, url_for
from flask_login import current_user
from werkzeug.exceptions import NotFound

from app.auth.rbac import require_role
from app.services import clickup_tarefas
from app.services.tarefas import board_service as board_svc
from app.services.tarefas import card_service as card_svc
from app.services.tarefas import workspace_service as ws_svc
from app.services.tarefas.permissions import Acao, perfis, pode

bp = Blueprint("tarefas", __name__)


@bp.route("")
@require_role(*perfis(Acao.VER))
def workspaces() -> str:
    """Lista de workspaces com criação, renomeação e exclusão (admin/gestor)."""
    # Público de ~10 pessoas: o limite máximo cobre todos os workspaces.
    itens, _ = ws_svc.listar(limit=ws_svc.LIMITE_MAXIMO)
    return render_template(
        "tarefas/workspaces.html",
        workspaces=itens,
        pode_gerenciar=pode(current_user.role, Acao.GERENCIAR_WORKSPACE),
    )


@bp.route("/")
def workspaces_barra() -> Response:
    """``/gov/tarefas/`` (com barra, comum em link colado) leva à URL canônica."""
    return redirect(url_for("tarefas.workspaces"), code=308)


@bp.route("/clickup")
@require_role(*perfis(Acao.VER))
def clickup() -> str:
    """Tarefas do ClickUp: as do próprio usuário, ou de todos por pessoa (admin)."""
    resultado = clickup_tarefas.obter()
    ver_todos = pode(current_user.role, Acao.VER_CLICKUP_TODOS)
    grupos = clickup_tarefas.agrupar_por_responsavel(resultado.tarefas)
    if not ver_todos:
        email = current_user.email.lower()
        grupos = [g for g in grupos if g.email == email]
    return render_template(
        "tarefas/clickup.html",
        resultado=resultado,
        grupos=grupos,
        ver_todos=ver_todos,
    )


@bp.route("/b/<int:board_id>")
@require_role(*perfis(Acao.VER))
def board(board_id: int) -> str:
    """Kanban do board; os cards são carregados e movidos pela API JSON."""
    return _pagina_board(board_id, card_aberto=None)


@bp.route("/b/<int:board_id>/c/<int:card_id>")
@require_role(*perfis(Acao.VER))
def card(board_id: int, card_id: int) -> str:
    """Board com o painel do card aberto — URL própria para compartilhar a decisão técnica."""
    try:
        c = card_svc.obter_card(card_id)
    except board_svc.CardNaoEncontradoError as exc:
        raise NotFound() from exc
    if c.board_id != board_id:
        raise NotFound()
    return _pagina_board(board_id, card_aberto=card_id)


def _pagina_board(board_id: int, card_aberto: int | None) -> str:
    try:
        b = board_svc.obter_board(board_id)
    except board_svc.BoardNaoEncontradoError as exc:
        # raise explícito (e não abort): o CodeQL não sabe que abort() sempre levanta
        raise NotFound() from exc
    return render_template(
        "tarefas/board.html",
        board=b,
        card_aberto=card_aberto,
        pode_editar=pode(current_user.role, Acao.EDITAR_CARD),
        pode_comentar=pode(current_user.role, Acao.COMENTAR),
    )
