"""User management CRUD — admin only."""

from __future__ import annotations

import secrets
import string

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth.rbac import require_role
from app.extensions import db
from app.models.user import ROLES, User
from app.services.aprovacoes import requer_aprovacao

bp = Blueprint("users", __name__, template_folder="../templates/users")


def _alvo(user_id: int) -> str:
    user = db.session.get(User, user_id)
    return f"{user.name} <{user.email}>" if user else f"#{user_id}"


def _resumo_criar() -> str:
    tv = " com painel da TV" if request.form.get("ver_painel_tv") else ""
    return f"Usuários: criar {request.form.get('name', '').strip()} <{request.form.get('email', '').strip().lower()}> como {request.form.get('role', 'visualizador')}{tv}"


def _resumo_editar(user_id: int) -> str:
    return (
        f"Usuários: editar {_alvo(user_id)} → {request.form.get('name', '').strip()} "
        f"<{request.form.get('email', '').strip().lower()}>, perfil {request.form.get('role', '')}"
    )


def _resumo_toggle(user_id: int) -> str:
    user = db.session.get(User, user_id)
    acao = "desativar" if user and user.is_active else "ativar"
    return f"Usuários: {acao} {_alvo(user_id)}"


def _resumo_painel_tv(user_id: int) -> str:
    acao = "liberar" if request.form.get("ver_painel_tv") == "1" else "bloquear"
    return f"Usuários: {acao} o painel da TV para {_alvo(user_id)}"


def _count_active_admins() -> int:
    return User.query.filter_by(role="admin", is_active=True).count()


@bp.route("/users")
@login_required
@require_role("admin")
def list_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("users/list.html", users=users, roles=ROLES)


@bp.route("/users", methods=["POST"])
@login_required
@require_role("admin")
@requer_aprovacao(_resumo_criar)
def create_user():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "visualizador")
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")

    if not all([name, email, password]):
        flash("Todos os campos são obrigatórios.", "error")
        return redirect(url_for("users.list_users"))

    if password != confirm:
        flash("As senhas não coincidem.", "error")
        return redirect(url_for("users.list_users"))

    if role not in ROLES:
        flash("Perfil inválido.", "error")
        return redirect(url_for("users.list_users"))

    if User.query.filter_by(email=email).first():
        flash(f"Email '{email}' já está em uso.", "error")
        return redirect(url_for("users.list_users"))

    user = User(name=name, email=email, role=role, ver_painel_tv=bool(request.form.get("ver_painel_tv")))
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"Usuário '{name}' criado com sucesso.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/edit", methods=["POST"])
@login_required
@require_role("admin")
@requer_aprovacao(_resumo_editar)
def edit_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", user.role)

    if not name or not email:
        flash("Nome e email são obrigatórios.", "error")
        return redirect(url_for("users.list_users"))

    if role not in ROLES:
        flash("Perfil inválido.", "error")
        return redirect(url_for("users.list_users"))

    if user.super_admin and role != "admin":
        flash("O super admin precisa continuar com perfil Admin.", "error")
        return redirect(url_for("users.list_users"))

    conflict = User.query.filter(User.email == email, User.id != user_id).first()
    if conflict:
        flash(f"Email '{email}' já está em uso por outro usuário.", "error")
        return redirect(url_for("users.list_users"))

    user.name = name
    user.email = email
    user.role = role
    db.session.commit()
    flash(f"Usuário '{name}' atualizado.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@require_role("admin")
@requer_aprovacao(lambda user_id: f"Usuários: redefinir senha de {_alvo(user_id)} (senha gerada na aprovação)")
def reset_password(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    new_password = request.form.get("new_password", "").strip()
    if not new_password:
        alphabet = string.ascii_letters + string.digits + "!@#$"
        new_password = "".join(secrets.choice(alphabet) for _ in range(12))
        flash(f"Nova senha gerada para '{user.name}': {new_password}", "success")
    else:
        flash(f"Senha de '{user.name}' redefinida.", "success")

    user.set_password(new_password)
    db.session.commit()
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@require_role("admin")
@requer_aprovacao(_resumo_toggle)
def toggle_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    if user.id == current_user.id:
        flash("Você não pode desativar sua própria conta.", "error")
        return redirect(url_for("users.list_users"))

    if user.is_active and user.super_admin:
        flash("O super admin não pode ser desativado.", "error")
        return redirect(url_for("users.list_users"))

    if user.is_active and user.role == "admin" and _count_active_admins() <= 1:
        flash("Não é possível desativar o único admin ativo.", "error")
        return redirect(url_for("users.list_users"))

    user.is_active = not user.is_active
    db.session.commit()
    state = "ativado" if user.is_active else "desativado"
    flash(f"Usuário '{user.name}' {state}.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/painel-tv", methods=["POST"])
@login_required
@require_role("admin")
@requer_aprovacao(_resumo_painel_tv)
def toggle_painel_tv(user_id: int):
    """Libera (``ver_painel_tv=1``) ou bloqueia o painel da TV (/gov/tv) para o usuário.

    O formulário leva o estado desejado, não "inverter": um pedido que espera
    na fila de aprovação continua fazendo o que o resumo diz.
    """
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    if user.role == "admin":
        flash("Admin sempre vê o painel da TV.", "error")
        return redirect(url_for("users.list_users"))

    user.ver_painel_tv = request.form.get("ver_painel_tv") == "1"
    db.session.commit()
    estado = "liberado" if user.ver_painel_tv else "bloqueado"
    flash(f"Painel da TV {estado} para '{user.name}'.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@require_role("admin")
@requer_aprovacao(lambda user_id: f"Usuários: excluir {_alvo(user_id)}")
def delete_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    if user.id == current_user.id:
        flash("Você não pode excluir sua própria conta.", "error")
        return redirect(url_for("users.list_users"))

    if user.super_admin:
        flash("O super admin não pode ser excluído.", "error")
        return redirect(url_for("users.list_users"))

    if user.role == "admin" and _count_active_admins() <= 1:
        flash("Não é possível excluir o único admin ativo.", "error")
        return redirect(url_for("users.list_users"))

    name = user.name
    db.session.delete(user)
    db.session.commit()
    flash(f"Usuário '{name}' excluído.", "success")
    return redirect(url_for("users.list_users"))
