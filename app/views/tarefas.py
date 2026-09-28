"""Páginas HTML do módulo Tarefas (``/gov/tarefas``).

As páginas só renderizam; escritas passam pela API JSON em
``/api/v1/tarefas`` (``itgov/api/v1/tarefas.py``).
"""

from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import current_user

from app.auth.rbac import require_role
from app.services.tarefas import workspace_service as ws_svc
from app.services.tarefas.permissions import Acao, perfis, pode

bp = Blueprint("tarefas", __name__)


@bp.route("")
@require_role(*perfis(Acao.VER))
def workspaces() -> str:
    """Lista de workspaces com criação, renomeação e exclusão (admin/gestor)."""
    itens, total = ws_svc.listar(limit=1000)
    return render_template(
        "tarefas/workspaces.html",
        workspaces=itens,
        total=total,
        pode_gerenciar=pode(current_user.role, Acao.GERENCIAR_WORKSPACE),
    )
