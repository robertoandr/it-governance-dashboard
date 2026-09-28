"""Fixtures do módulo Tarefas.

O ``factory_app`` grava no ``data/app.db`` do checkout (caminho fixo em
``create_app``), então cada teste começa e termina com as tabelas do
módulo vazias. Em produção o app.db fica no volume ``app_data``, fora do
alcance dos testes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db
from app.models.tarefas import Board, Card, CardActivity, CardComment, CardDocument, Workspace
from app.models.user import User

AJAX = {"X-Requested-With": "XMLHttpRequest"}


def _limpar() -> None:
    for modelo in (CardActivity, CardComment, CardDocument, Card, Board, Workspace):
        db.session.query(modelo).delete()
    db.session.commit()


@pytest.fixture(autouse=True)
def tabelas_limpas(factory_app: Flask) -> Iterator[None]:
    with factory_app.app_context():
        _limpar()
    yield
    with factory_app.app_context():
        db.session.rollback()
        _limpar()


def _usuario(factory_app: Flask, role: str, ativo: bool = True) -> int:
    email = f"pytest-tarefas-{role}{'' if ativo else '-inativo'}@test.local"
    with factory_app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(name=f"Pytest {role}", email=email, role=role)
            user.set_password("pytest-only-not-real")
            db.session.add(user)
        user.is_active = ativo
        db.session.commit()
        return user.id


@pytest.fixture
def usuario_id(factory_app: Flask) -> Callable[..., int]:
    """Factory: ``usuario_id("gestor")`` → id de um usuário com esse perfil."""

    def _factory(role: str, ativo: bool = True) -> int:
        return _usuario(factory_app, role, ativo)

    return _factory


@pytest.fixture
def cliente(factory_app: Flask, usuario_id: Callable[..., int]) -> Iterator[Callable[..., FlaskClient]]:
    """Factory: ``cliente("operador")`` → test client logado com esse perfil."""

    def _factory(role: str, ativo: bool = True) -> FlaskClient:
        uid = usuario_id(role, ativo)
        c = factory_app.test_client()
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        return c

    yield _factory
