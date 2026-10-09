"""Fila de aprovações: super admin decide, os demais acompanham os próprios pedidos."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.wrappers import Response

from app.auth.rbac import require_role
from app.extensions import db
from app.models.aprovacao import PENDENTE, Solicitacao
from app.services.aprovacoes import (
    CAMPOS_SENSIVEIS,
    STATUS_ROTULO,
    aprovacao_ativa,
    aprovar,
    eh_super_admin,
    marcar_ciente,
    rejeitar,
    voltar,
)

bp = Blueprint("aprovacoes", __name__)

_LIMITE_HISTORICO = 50
_TZ = ZoneInfo("America/Sao_Paulo")


@bp.app_template_filter("data_local")
def data_local(dt: datetime | None) -> str:
    """``dd/mm hh:mm`` no horário de Brasília (SQLite devolve sem fuso: é UTC)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_TZ).strftime("%d/%m %H:%M")


@bp.route("/aprovacoes")
@login_required
@require_role("admin", "gestor", "operador", "visualizador")
def lista() -> str:
    """Pendentes e histórico recente (super admin vê todos; os demais, os seus)."""
    consulta = Solicitacao.query
    if not eh_super_admin(current_user):
        consulta = consulta.filter_by(solicitante_id=current_user.id)
    # Abrir a lista conta como ter visto as decisões (o aviso some do topo)
    novas = set(marcar_ciente(current_user))
    pendentes = consulta.filter_by(status=PENDENTE).order_by(Solicitacao.criado_em).all()
    historico = (
        consulta.filter(Solicitacao.status != PENDENTE)
        .order_by(Solicitacao.decidido_em.desc())
        .limit(_LIMITE_HISTORICO)
        .all()
    )
    return render_template(
        "aprovacoes/lista.html",
        pendentes=pendentes,
        historico=historico,
        pode_decidir=eh_super_admin(current_user),
        ativa=aprovacao_ativa(),
        rotulo=STATUS_ROTULO,
        sensiveis=CAMPOS_SENSIVEIS,
        novas=novas,
    )


def _pendente_ou_404(sol_id: int) -> Solicitacao:
    if not eh_super_admin(current_user):
        abort(403)
    sol = db.session.get(Solicitacao, sol_id)
    if sol is None:
        abort(404)
    return sol


@bp.route("/aprovacoes/<int:sol_id>/aprovar", methods=["POST"])
@login_required
@require_role("admin")
def aprovar_solicitacao(sol_id: int) -> Response:
    """Aplica a alteração pedida (só super admin)."""
    sol = _pendente_ou_404(sol_id)
    ok, mensagem = aprovar(sol, current_user)
    flash(f"#{sol.id} {'aprovada' if ok else 'não pôde ser aplicada'}: {mensagem}", "success" if ok else "error")
    return redirect(url_for("aprovacoes.lista"))


@bp.route("/aprovacoes/<int:sol_id>/rejeitar", methods=["POST"])
@login_required
@require_role("admin")
def rejeitar_solicitacao(sol_id: int) -> Response:
    """Recusa a alteração pedida (só super admin)."""
    sol = _pendente_ou_404(sol_id)
    if sol.status != PENDENTE:
        flash("Esta solicitação já foi decidida.", "error")
    else:
        rejeitar(sol, current_user, request.form.get("motivo", ""))
        flash(f"#{sol.id} rejeitada.", "success")
    return redirect(url_for("aprovacoes.lista"))


@bp.route("/aprovacoes/ciente", methods=["POST"])
@login_required
@require_role("admin", "gestor", "operador", "visualizador")
def ciente() -> Response:
    """Fecha o aviso de decisão: marca como vistos os pedidos decididos do usuário."""
    marcar_ciente(current_user)
    return redirect(voltar())
