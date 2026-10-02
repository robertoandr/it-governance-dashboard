"""Super admin: alterações de cadastro/configuração esperam aprovação."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text

from app.extensions import db
from app.models.aprovacao import APROVADA, FALHOU, PENDENTE, REJEITADA, Solicitacao
from app.models.unidade import Unidade
from app.models.user import User, definir_super_admin, garantir_coluna_super_admin

_SUPER = "pytest-super@test.local"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _cliente(factory_app, uid: int) -> Iterator:
    # Sem "with": um cliente em "with" deixa o contexto da última requisição
    # aberto, e o próximo cliente herdaria o usuário guardado em "g".
    c = factory_app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    yield c


def _usuario(factory_app, email: str, role: str, super_admin: bool = False) -> int:
    with factory_app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(name=email.split("@")[0], email=email, role=role)
            user.set_password("pytest-only-not-real")
            db.session.add(user)
        user.role, user.super_admin, user.is_active = role, super_admin, True
        db.session.commit()
        return user.id


@pytest.fixture
def super_id(factory_app) -> Iterator[int]:
    uid = _usuario(factory_app, _SUPER, "admin", super_admin=True)
    yield uid
    with factory_app.app_context():
        db.session.get(User, uid).super_admin = False
        Solicitacao.query.delete()
        for email in ("pytest-novo-aprov@test.local", "pytest-novo-direto@test.local"):
            User.query.filter_by(email=email).delete()
        Unidade.query.filter(Unidade.nome.like("Aprov %")).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture
def super_client(factory_app, super_id: int) -> Iterator:
    yield from _cliente(factory_app, super_id)


@pytest.fixture
def admin_client(factory_app) -> Iterator:
    yield from _cliente(factory_app, _usuario(factory_app, "pytest-admin-aprov@test.local", "admin"))


@pytest.fixture
def gestor_client(factory_app) -> Iterator:
    yield from _cliente(factory_app, _usuario(factory_app, "pytest-gestor-aprov@test.local", "gestor"))


def _pendentes(factory_app) -> list[Solicitacao]:
    with factory_app.app_context():
        lista = Solicitacao.query.filter_by(status=PENDENTE).order_by(Solicitacao.id).all()
        for s in lista:
            db.session.expunge(s)
        return lista


def _sol(factory_app, sol_id: int) -> Solicitacao:
    with factory_app.app_context():
        s = db.session.get(Solicitacao, sol_id)
        db.session.expunge(s)
        return s


def _criar_usuario(client, email: str) -> object:
    return client.post(
        "/gov/users",
        data={
            "name": "Novo Aprov",
            "email": email,
            "role": "operador",
            "password": "fake-password-123",
            "confirm_password": "fake-password-123",
        },
        headers={"Referer": "http://localhost/gov/users"},
    )


# ── Sem super admin: tudo vale direto ─────────────────────────────────────────


def test_sem_super_admin_ativo_aplica_direto(admin_client, factory_app) -> None:
    with factory_app.app_context():
        User.query.filter_by(super_admin=True).update({"super_admin": False})
        db.session.commit()
    try:
        _criar_usuario(admin_client, "pytest-novo-direto@test.local")
        with factory_app.app_context():
            assert User.query.filter_by(email="pytest-novo-direto@test.local").one()
            assert Solicitacao.query.count() == 0
    finally:
        with factory_app.app_context():
            User.query.filter_by(email="pytest-novo-direto@test.local").delete()
            db.session.commit()


# ── Fila ─────────────────────────────────────────────────────────────────────


def test_admin_cria_usuario_fica_pendente_sem_senha(admin_client, factory_app, super_id: int) -> None:
    resp = _criar_usuario(admin_client, "pytest-novo-aprov@test.local")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/gov/users")
    with factory_app.app_context():
        assert User.query.filter_by(email="pytest-novo-aprov@test.local").first() is None
    (sol,) = _pendentes(factory_app)
    assert sol.endpoint == "users.create_user"
    assert "pytest-novo-aprov@test.local" in sol.resumo
    assert "segredo-digitado" not in sol.form_json
    assert {k for k, _ in sol.form} == {"name", "email", "role"}


def test_super_admin_aprova_e_recebe_senha_gerada(admin_client, super_client, factory_app, super_id: int) -> None:
    _criar_usuario(admin_client, "pytest-novo-aprov@test.local")
    (sol,) = _pendentes(factory_app)

    html = super_client.post(f"/gov/aprovacoes/{sol.id}/aprovar", follow_redirects=True).get_data(as_text=True)

    senha = re.search(r"Senha gerada \(anote, não fica guardada\): (\S+)", html)
    assert senha, html
    with factory_app.app_context():
        novo = User.query.filter_by(email="pytest-novo-aprov@test.local").one()
        assert novo.role == "operador"
        assert novo.check_password(senha.group(1).replace("&lt;", "<").replace("&amp;", "&"))
    decidida = _sol(factory_app, sol.id)
    assert decidida.status == APROVADA
    assert decidida.decidido_por == "pytest-super"
    assert senha.group(1) not in decidida.resultado


def test_super_admin_altera_direto(super_client, factory_app, super_id: int) -> None:
    _criar_usuario(super_client, "pytest-novo-aprov@test.local")
    with factory_app.app_context():
        assert User.query.filter_by(email="pytest-novo-aprov@test.local").one()
    assert _pendentes(factory_app) == []


def test_rejeitar_nao_aplica(admin_client, super_client, factory_app, super_id: int) -> None:
    _criar_usuario(admin_client, "pytest-novo-aprov@test.local")
    (sol,) = _pendentes(factory_app)
    super_client.post(f"/gov/aprovacoes/{sol.id}/rejeitar", data={"motivo": "não precisa"})
    decidida = _sol(factory_app, sol.id)
    assert (decidida.status, decidida.resultado) == (REJEITADA, "não precisa")
    with factory_app.app_context():
        assert User.query.filter_by(email="pytest-novo-aprov@test.local").first() is None
    # Decidida não pode ser aprovada depois
    html = super_client.post(f"/gov/aprovacoes/{sol.id}/aprovar", follow_redirects=True).get_data(as_text=True)
    assert "já foi decidida" in html


def test_aprovacao_de_dado_invalido_vira_falhou(gestor_client, super_client, factory_app, super_id: int) -> None:
    gestor_client.post("/gov/unidades/nova", data={"nome": "Aprov Cep Ruim", "cep": "123"})
    (sol,) = _pendentes(factory_app)
    assert sol.resumo == "Unidades: criar Aprov Cep Ruim"
    super_client.post(f"/gov/aprovacoes/{sol.id}/aprovar")
    decidida = _sol(factory_app, sol.id)
    assert decidida.status == FALHOU
    assert "CEP inválido" in decidida.resultado


def test_aprovacao_repete_upload_de_arquivo(gestor_client, super_client, factory_app, super_id: int) -> None:
    gestor_client.post(
        "/gov/unidades/nova",
        data={"nome": "Aprov Com Logo", "logo": (io.BytesIO(_PNG), "logo.png", "image/png")},
        content_type="multipart/form-data",
    )
    (sol,) = _pendentes(factory_app)
    assert sol.arquivos[0]["nome"] == "logo.png"
    super_client.post(f"/gov/aprovacoes/{sol.id}/aprovar")
    assert _sol(factory_app, sol.id).status == APROVADA
    with factory_app.app_context():
        unidade = Unidade.query.filter_by(nome="Aprov Com Logo").one()
        assert unidade.logo == _PNG and unidade.logo_mime == "image/png"


def test_rota_json_responde_202_pendente(gestor_client, factory_app, super_id: int) -> None:
    resp = gestor_client.post("/gov/licenses/update", json={"sku_name": "SPB", "custo_mensal": 10})
    assert resp.status_code == 202
    assert resp.get_json()["pendente"] is True
    (sol,) = _pendentes(factory_app)
    assert sol.corpo == {"sku_name": "SPB", "custo_mensal": 10}
    assert "SPB" in sol.resumo


def test_solicitante_desativado_faz_aprovacao_falhar(gestor_client, super_client, factory_app, super_id: int) -> None:
    gestor_client.post("/gov/unidades/nova", data={"nome": "Aprov Gestor Saiu"})
    (sol,) = _pendentes(factory_app)
    with factory_app.app_context():
        User.query.filter_by(email="pytest-gestor-aprov@test.local").update({"is_active": False})
        db.session.commit()
    try:
        super_client.post(f"/gov/aprovacoes/{sol.id}/aprovar")
        decidida = _sol(factory_app, sol.id)
        assert decidida.status == FALHOU and "desativado" in decidida.resultado
    finally:
        with factory_app.app_context():
            User.query.filter_by(email="pytest-gestor-aprov@test.local").update({"is_active": True})
            db.session.commit()


def test_so_super_admin_decide_e_cada_um_ve_os_seus(
    admin_client, gestor_client, super_client, factory_app, super_id: int
) -> None:
    gestor_client.post("/gov/unidades/nova", data={"nome": "Aprov Do Gestor"})
    (sol,) = _pendentes(factory_app)

    assert admin_client.post(f"/gov/aprovacoes/{sol.id}/aprovar").status_code == 403
    assert gestor_client.post(f"/gov/aprovacoes/{sol.id}/aprovar").status_code == 403

    assert "Aprov Do Gestor" in gestor_client.get("/gov/aprovacoes").get_data(as_text=True)
    html_admin = admin_client.get("/gov/aprovacoes").get_data(as_text=True)
    assert "Aprov Do Gestor" not in html_admin
    html_super = super_client.get("/gov/aprovacoes").get_data(as_text=True)
    assert "Aprov Do Gestor" in html_super and "Aprovar" in html_super


def test_menu_mostra_contador_so_para_super_admin(gestor_client, super_client, factory_app, super_id: int) -> None:
    gestor_client.post("/gov/unidades/nova", data={"nome": "Aprov Contador"})
    assert 'title="1 aguardando aprovação"' in super_client.get("/gov/aprovacoes").get_data(as_text=True)
    assert "aguardando aprovação" not in gestor_client.get("/gov/aprovacoes").get_data(as_text=True)


def test_get_de_formulario_nao_e_interceptado(gestor_client, super_id: int) -> None:
    assert gestor_client.get("/gov/unidades/nova").status_code == 200


# ── Conta do super admin ──────────────────────────────────────────────────────


def test_super_admin_nao_perde_admin_nem_e_desativado_ou_excluido(super_client, factory_app, super_id: int) -> None:
    alvo = _usuario(factory_app, "pytest-outro-super@test.local", "admin", super_admin=False)
    with factory_app.app_context():
        db.session.get(User, alvo).super_admin = True
        db.session.commit()
    try:
        r = super_client.post(
            f"/gov/users/{alvo}/edit",
            data={"name": "x", "email": "pytest-outro-super@test.local", "role": "gestor"},
            follow_redirects=True,
        )
        assert "precisa continuar com perfil Admin" in r.get_data(as_text=True)
        r = super_client.post(f"/gov/users/{alvo}/toggle", follow_redirects=True)
        assert "não pode ser desativado" in r.get_data(as_text=True)
        r = super_client.post(f"/gov/users/{alvo}/delete", follow_redirects=True)
        assert "não pode ser excluído" in r.get_data(as_text=True)
    finally:
        with factory_app.app_context():
            User.query.filter_by(email="pytest-outro-super@test.local").delete()
            db.session.commit()


def test_definir_super_admin_marca_um_so_e_garante_admin(factory_app) -> None:
    a = _usuario(factory_app, "pytest-def-a@test.local", "gestor")
    b = _usuario(factory_app, "pytest-def-b@test.local", "admin", super_admin=True)
    try:
        with factory_app.app_context():
            definir_super_admin("  PYTEST-DEF-A@test.local ")
            ua, ub = db.session.get(User, a), db.session.get(User, b)
            assert (ua.super_admin, ua.role, ua.role_label) == (True, "admin", "Super admin")
            assert ub.super_admin is False
            definir_super_admin("ninguem@test.local")  # conta inexistente: não muda nada
            assert db.session.get(User, a).super_admin is True
    finally:
        with factory_app.app_context():
            User.query.filter(User.email.in_(["pytest-def-a@test.local", "pytest-def-b@test.local"])).delete()
            db.session.commit()


def test_garantir_coluna_super_admin(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(text("INSERT INTO users (email) VALUES ('a@b')"))
    garantir_coluna_super_admin(engine)
    garantir_coluna_super_admin(engine)
    assert "super_admin" in {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT super_admin FROM users")).scalar() == 0


def test_resumo_de_edicao_mostra_antes_e_depois(admin_client, factory_app, super_id: int) -> None:
    alvo = _usuario(factory_app, "pytest-editado@test.local", "operador")
    try:
        admin_client.post(
            f"/gov/users/{alvo}/edit", data={"name": "Editado", "email": "pytest-editado@test.local", "role": "gestor"}
        )
        (sol,) = _pendentes(factory_app)
        assert "<pytest-editado@test.local>" in sol.resumo and "perfil gestor" in sol.resumo
        assert json.loads(sol.view_args_json) == {"user_id": str(alvo)}
    finally:
        with factory_app.app_context():
            User.query.filter_by(email="pytest-editado@test.local").delete()
            db.session.commit()
