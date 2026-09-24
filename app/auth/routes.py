"""Login and logout routes."""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.auth import bp
from app.extensions import db, login_manager
from app.models.user import User


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


def _safe_next(target: str | None) -> str | None:
    """Aceita só caminhos locais em ``next`` (evita open redirect).

    Args:
        target: Valor bruto do parâmetro ``next``.

    Returns:
        O caminho quando começa com uma única "/", senão ``None``.
    """
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboards.overview"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=remember)
            next_page = _safe_next(request.args.get("next")) or url_for("dashboards.overview")
            return redirect(next_page)

        flash("Email ou senha inválidos.", "error")

    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("auth.login"))
