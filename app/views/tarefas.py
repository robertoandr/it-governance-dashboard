"""Páginas HTML do módulo Tarefas (``/gov/tarefas``).

As páginas só renderizam; escritas passam pela API JSON em
``/api/v1/tarefas`` (``itgov/api/v1/tarefas.py``).
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template
from flask_login import current_user

from app.auth.rbac import require_role
from app.services.tarefas import board_service as board_svc
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


@bp.route("/b/<int:board_id>")
@require_role(*perfis(Acao.VER))
def board(board_id: int) -> str:
    """Kanban do board; os cards são carregados e movidos pela API JSON."""
    try:
        b = board_svc.obter_board(board_id)
    except board_svc.BoardNaoEncontradoError:
        abort(404)
    return render_template(
        "tarefas/board.html",
        board=b,
        pode_editar=pode(current_user.role, Acao.EDITAR_CARD),
    )
