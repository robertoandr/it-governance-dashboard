"""Sessão: usuário desativado perde o acesso na hora e cookies saem com SameSite=Lax."""

from __future__ import annotations

from flask import Flask

from app.extensions import db
from app.models.user import User


def _usuario(factory_app: Flask, ativo: bool) -> int:
    email = f"pytest-sessao-{'ativo' if ativo else 'inativo'}@test.local"
    with factory_app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(name="Pytest Sessao", email=email, role="admin")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
        user.is_active = ativo
        db.session.commit()
        return user.id


def _cliente_logado(factory_app: Flask, user_id: int):
    c = factory_app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
    return c


def test_sessao_de_usuario_ativo_continua_valida(factory_app: Flask) -> None:
    c = _cliente_logado(factory_app, _usuario(factory_app, ativo=True))
    assert c.get("/gov/users").status_code == 200


def test_sessao_aberta_de_usuario_desativado_vira_anonima(factory_app: Flask) -> None:
    c = _cliente_logado(factory_app, _usuario(factory_app, ativo=False))
    r = c.get("/gov/users")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_sessao_de_usuario_inexistente_vira_anonima(factory_app: Flask) -> None:
    r = _cliente_logado(factory_app, 987_654_321).get("/gov/users")
    assert r.status_code == 302


def test_cookies_de_sessao_com_samesite_lax(factory_app: Flask) -> None:
    assert factory_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert factory_app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
