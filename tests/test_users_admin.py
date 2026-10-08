"""Testes de app/views/users.py — CRUD de usuários (somente admin)."""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.user import User

_PREFIXO = "pytest-users-"


def _email(nome: str) -> str:
    return f"{_PREFIXO}{nome}@test.local"


@pytest.fixture(autouse=True)
def _limpa_usuarios(factory_app):
    yield
    with factory_app.app_context():
        User.query.filter(User.email.like(f"{_PREFIXO}%")).delete(synchronize_session=False)
        db.session.commit()


def _criar(factory_app, nome: str, role: str = "visualizador", ativo: bool = True) -> int:
    with factory_app.app_context():
        user = User(name=nome, email=_email(nome), role=role, is_active=ativo)
        user.set_password("senha-teste-123")
        db.session.add(user)
        db.session.commit()
        return user.id


def _buscar(factory_app, user_id: int) -> User | None:
    with factory_app.app_context():
        return db.session.get(User, user_id)


def _flash(client) -> list[tuple[str, str]]:
    with client.session_transaction() as sess:
        return list(sess.get("_flashes", []))


def test_listagem_exige_login(factory_client) -> None:
    resp = factory_client.get("/gov/users")
    assert resp.status_code in (302, 401)


def test_listagem_admin(authed_client, factory_app) -> None:
    _criar(factory_app, "listado")
    resp = authed_client.get("/gov/users")
    assert resp.status_code == 200
    assert _email("listado").encode() in resp.data


def test_criar_usuario(authed_client, factory_app) -> None:
    resp = authed_client.post(
        "/gov/users",
        data={
            "name": "Novo",
            "email": "  " + _email("novo").upper(),
            "role": "visualizador",
            "password": "abc12345",
            "confirm_password": "abc12345",
        },
    )
    assert resp.status_code == 302
    with factory_app.app_context():
        user = User.query.filter_by(email=_email("novo")).one()
        assert user.check_password("abc12345")


@pytest.mark.parametrize(
    ("dados", "mensagem"),
    [
        ({"name": "", "password": "x", "confirm_password": "x"}, "obrigatórios"),
        ({"name": "A", "password": "x", "confirm_password": "y"}, "não coincidem"),
        ({"name": "A", "password": "x", "confirm_password": "x", "role": "root"}, "Perfil inválido"),
    ],
    ids=["campos-vazios", "senhas-diferentes", "perfil-invalido"],
)
def test_criar_usuario_validacoes(authed_client, factory_app, dados, mensagem) -> None:
    authed_client.post("/gov/users", data={"email": _email("invalido"), **dados})
    assert any(mensagem in msg for cat, msg in _flash(authed_client) if cat == "error")
    with factory_app.app_context():
        assert User.query.filter_by(email=_email("invalido")).first() is None


def test_criar_usuario_email_duplicado(authed_client, factory_app) -> None:
    _criar(factory_app, "dup")
    authed_client.post(
        "/gov/users",
        data={"name": "Outro", "email": _email("dup"), "password": "x", "confirm_password": "x"},
    )
    assert any("já está em uso" in msg for _, msg in _flash(authed_client))


def test_editar_usuario(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "editar")
    authed_client.post(
        f"/gov/users/{uid}/edit",
        data={"name": "Editado", "email": _email("editado"), "role": "gestor"},
    )
    user = _buscar(factory_app, uid)
    assert (user.name, user.email, user.role) == ("Editado", _email("editado"), "gestor")


def test_editar_usuario_erros(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "e1")
    _criar(factory_app, "e2")

    authed_client.post("/gov/users/999999/edit", data={"name": "X", "email": "x@x"})
    assert any("não encontrado" in msg for _, msg in _flash(authed_client))

    authed_client.post(f"/gov/users/{uid}/edit", data={"name": "", "email": _email("e1")})
    assert any("obrigatórios" in msg for _, msg in _flash(authed_client))

    authed_client.post(f"/gov/users/{uid}/edit", data={"name": "X", "email": _email("e1"), "role": "root"})
    assert any("Perfil inválido" in msg for _, msg in _flash(authed_client))

    authed_client.post(f"/gov/users/{uid}/edit", data={"name": "X", "email": _email("e2")})
    assert any("outro usuário" in msg for _, msg in _flash(authed_client))
    assert _buscar(factory_app, uid).email == _email("e1")


def test_reset_senha_informada_e_gerada(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "reset")

    authed_client.post(f"/gov/users/{uid}/reset-password", data={"new_password": "nova-senha-1"})
    assert _buscar(factory_app, uid).check_password("nova-senha-1")

    authed_client.post(f"/gov/users/{uid}/reset-password", data={})
    gerada = next(msg for _, msg in _flash(authed_client) if "Nova senha gerada" in msg).rsplit(": ", 1)[1]
    assert len(gerada) == 12
    assert _buscar(factory_app, uid).check_password(gerada)

    authed_client.post("/gov/users/999999/reset-password", data={})
    assert any("não encontrado" in msg for _, msg in _flash(authed_client))


def test_toggle_ativa_e_desativa(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "toggle")
    authed_client.post(f"/gov/users/{uid}/toggle")
    assert _buscar(factory_app, uid).is_active is False
    authed_client.post(f"/gov/users/{uid}/toggle")
    assert _buscar(factory_app, uid).is_active is True


def test_toggle_protecoes(authed_client, factory_app, authed_user, monkeypatch) -> None:
    authed_client.post(f"/gov/users/{authed_user}/toggle")
    assert any("própria conta" in msg for _, msg in _flash(authed_client))

    authed_client.post("/gov/users/999999/toggle")
    assert any("não encontrado" in msg for _, msg in _flash(authed_client))

    admin = _criar(factory_app, "unico-admin", role="admin")
    monkeypatch.setattr("app.views.users._count_active_admins", lambda: 1)
    authed_client.post(f"/gov/users/{admin}/toggle")
    assert any("único admin" in msg for _, msg in _flash(authed_client))
    assert _buscar(factory_app, admin).is_active is True


def test_excluir_usuario(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "excluir")
    authed_client.post(f"/gov/users/{uid}/delete")
    assert _buscar(factory_app, uid) is None


def test_excluir_protecoes(authed_client, factory_app, authed_user, monkeypatch) -> None:
    authed_client.post(f"/gov/users/{authed_user}/delete")
    assert any("própria conta" in msg for _, msg in _flash(authed_client))
    assert _buscar(factory_app, authed_user) is not None

    authed_client.post("/gov/users/999999/delete")
    assert any("não encontrado" in msg for _, msg in _flash(authed_client))

    admin = _criar(factory_app, "admin-del", role="admin")
    monkeypatch.setattr("app.views.users._count_active_admins", lambda: 1)
    authed_client.post(f"/gov/users/{admin}/delete")
    assert any("único admin" in msg for _, msg in _flash(authed_client))
    assert _buscar(factory_app, admin) is not None


def test_painel_tv_libera_e_bloqueia_pelo_estado_enviado(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "tv")
    assert _buscar(factory_app, uid).ver_painel_tv is False

    authed_client.post(f"/gov/users/{uid}/painel-tv", data={"ver_painel_tv": "1"})
    assert _buscar(factory_app, uid).ver_painel_tv is True
    authed_client.post(f"/gov/users/{uid}/painel-tv", data={"ver_painel_tv": "1"})  # repetir não inverte
    assert _buscar(factory_app, uid).ver_painel_tv is True

    authed_client.post(f"/gov/users/{uid}/painel-tv", data={"ver_painel_tv": "0"})
    assert _buscar(factory_app, uid).ver_painel_tv is False


def test_painel_tv_admin_sempre_ve(authed_client, factory_app) -> None:
    uid = _criar(factory_app, "admin-tv", role="admin")

    authed_client.post(f"/gov/users/{uid}/painel-tv", data={"ver_painel_tv": "0"})

    assert any("Admin sempre vê" in msg for _, msg in _flash(authed_client))
    assert _buscar(factory_app, uid).pode_ver_painel_tv is True


def test_criar_usuario_ja_liberado_para_tv(authed_client, factory_app) -> None:
    authed_client.post(
        "/gov/users",
        data={
            "name": "TV NOC",
            "email": _email("tv-noc"),
            "role": "visualizador",
            "password": "abc12345",
            "confirm_password": "abc12345",
            "ver_painel_tv": "1",
        },
    )

    with factory_app.app_context():
        user = User.query.filter_by(email=_email("tv-noc")).first()
        assert user is not None and user.ver_painel_tv is True


def test_listagem_mostra_coluna_painel_tv(authed_client, factory_app) -> None:
    _criar(factory_app, "coluna-tv")

    html = authed_client.get("/gov/users").get_data(as_text=True)

    assert "Painel TV" in html
    assert "Bloqueado" in html


def test_garantir_coluna_painel_tv_em_banco_antigo(tmp_path) -> None:
    from sqlalchemy import create_engine, inspect, text

    from app.models.user import garantir_coluna_painel_tv

    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(text("INSERT INTO users (email) VALUES ('a@b')"))
    garantir_coluna_painel_tv(engine)
    garantir_coluna_painel_tv(engine)  # idempotente

    assert "ver_painel_tv" in {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT ver_painel_tv FROM users")).scalar() == 0
