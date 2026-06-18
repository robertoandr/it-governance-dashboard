"""User management CRUD — admin only."""

from __future__ import annotations

import secrets
import string

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.auth.rbac import require_role
from app.extensions import db
from app.models.user import ROLES, User

bp = Blueprint("users", __name__, template_folder="../templates/users")


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

    user = User(name=name, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"Usuário '{name}' criado com sucesso.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/edit", methods=["POST"])
@login_required
@require_role("admin")
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
def toggle_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    if user.id == current_user.id:
        flash("Você não pode desativar sua própria conta.", "error")
        return redirect(url_for("users.list_users"))

    if user.is_active and user.role == "admin" and _count_active_admins() <= 1:
        flash("Não é possível desativar o único admin ativo.", "error")
        return redirect(url_for("users.list_users"))

    user.is_active = not user.is_active
    db.session.commit()
    state = "ativado" if user.is_active else "desativado"
    flash(f"Usuário '{user.name}' {state}.", "success")
    return redirect(url_for("users.list_users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@require_role("admin")
def delete_user(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("users.list_users"))

    if user.id == current_user.id:
        flash("Você não pode excluir sua própria conta.", "error")
        return redirect(url_for("users.list_users"))

    if user.role == "admin" and _count_active_admins() <= 1:
        flash("Não é possível excluir o único admin ativo.", "error")
        return redirect(url_for("users.list_users"))

    name = user.name
    db.session.delete(user)
    db.session.commit()
    flash(f"Usuário '{name}' excluído.", "success")
    return redirect(url_for("users.list_users"))
