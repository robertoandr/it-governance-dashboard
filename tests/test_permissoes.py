"""Permissões por usuário: ajustes por página sobre o perfil."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text

from app import permissoes as perm
from app.extensions import db
from app.models.aprovacao import PENDENTE, Solicitacao
from app.models.user import User, garantir_coluna_permissoes

_PREFIXO = "pytest-perm-"


def _usuario(
    factory_app, nome: str, role: str, ajustes: dict[str, str] | None = None, super_admin: bool = False
) -> int:
    email = f"{_PREFIXO}{nome}@test.local"
    with factory_app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(name=nome, email=email, role=role)
            user.set_password("pytest-only-not-real")
            db.session.add(user)
        user.role, user.super_admin, user.is_active = role, super_admin, True
        user.permissoes = ajustes or {}
        db.session.commit()
        return user.id


def _cliente(factory_app, uid: int):
    c = factory_app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return c


@pytest.fixture(autouse=True)
def _limpa(factory_app) -> Iterator[None]:
    yield
    with factory_app.app_context():
        Solicitacao.query.delete()
        User.query.filter(User.email.like(f"{_PREFIXO}%")).delete(synchronize_session=False)
        db.session.commit()


# ── Catálogo ─────────────────────────────────────────────────────────────────


def test_padrao_do_perfil_segue_as_rotas() -> None:
    cftv = perm.POR_CHAVE["cftv"]
    assert cftv.padrao("admin") == perm.ALTERAR
    assert cftv.padrao("operador") == perm.VER
    assert cftv.padrao("visualizador") == perm.NENHUM
    assert not perm.POR_CHAVE["zendesk"].alteravel


def test_limpar_guarda_so_o_que_foge_do_perfil() -> None:
    ajustes = perm.limpar(
        {"cftv": "ver", "zendesk": "ver", "sla": "alterar", "nao_existe": "ver", "rede": "qualquer"},
        "operador",
    )
    # cftv "ver" é o padrão do operador; sla não tem alteração (vira "ver")
    assert ajustes == {"zendesk": "ver", "sla": "ver"}


def test_endpoints_do_catalogo_existem(factory_app) -> None:
    for pagina in perm.PAGINAS:
        assert pagina.endpoint in factory_app.view_functions, pagina.endpoint


# ── Regras no modelo ─────────────────────────────────────────────────────────


def test_ajuste_vence_o_perfil_nas_duas_direcoes(factory_app) -> None:
    uid = _usuario(factory_app, "op", "operador", {"zendesk": "ver", "cftv": "nenhum"})
    with factory_app.app_context():
        user = db.session.get(User, uid)
        assert user.pode("zendesk")
        assert not user.pode("zendesk", "alterar")
        assert not user.pode("cftv")
        # Sem ajuste: perfil
        assert user.pode("zabbix") and not user.pode("relatorios")
        assert user.pode("unidades", "ver", ("admin", "gestor", "operador"))
        assert not user.pode("unidades", "alterar", ("admin", "gestor"))


def test_super_admin_pode_tudo(factory_app) -> None:
    uid = _usuario(factory_app, "super", "admin", super_admin=True)
    with factory_app.app_context():
        user = db.session.get(User, uid)
        user.permissoes_json = json.dumps({"cftv": "nenhum"})
        assert user.pode("cftv", "alterar")
        assert user.nivel("cftv") == perm.ALTERAR


# ── Rotas ────────────────────────────────────────────────────────────────────


def test_liberar_pagina_que_o_perfil_nao_via(factory_app) -> None:
    sem = _cliente(factory_app, _usuario(factory_app, "vis", "visualizador"))
    assert sem.get("/gov/unidades").status_code == 403
    com = _cliente(factory_app, _usuario(factory_app, "vis2", "visualizador", {"unidades": "ver"}))
    html = com.get("/gov/unidades").get_data(as_text=True)
    assert "Unidades" in html
    # Só vê: o formulário de cadastro continua fechado
    assert com.get("/gov/unidades/nova").status_code == 403


def test_liberar_alteracao(factory_app) -> None:
    c = _cliente(factory_app, _usuario(factory_app, "vis3", "visualizador", {"unidades": "alterar"}))
    assert c.get("/gov/unidades/nova").status_code == 200


def test_tirar_pagina_que_o_perfil_via(factory_app) -> None:
    c = _cliente(factory_app, _usuario(factory_app, "gest", "gestor", {"unidades": "nenhum"}))
    assert c.get("/gov/unidades").status_code == 403
    html = c.get("/gov/links").get_data(as_text=True)
    assert 'href="/gov/unidades"' not in html
    assert 'href="/gov/links"' in html


def test_so_ve_esconde_botoes_de_edicao(factory_app) -> None:
    c = _cliente(factory_app, _usuario(factory_app, "gest2", "gestor", {"links": "ver"}))
    html = c.get("/gov/links").get_data(as_text=True)
    assert "/gov/links/novo" not in html
    assert c.get("/gov/links/novo").status_code == 403


def test_api_segue_o_ajuste(factory_app) -> None:
    barrado = _cliente(factory_app, _usuario(factory_app, "gest3", "gestor", {"pmo": "nenhum"}))
    assert barrado.get("/api/pmo/manual").status_code == 403
    liberado = _cliente(factory_app, _usuario(factory_app, "op4", "operador", {"pmo": "ver"}))
    # Passa da autorização (sem dado no banco de teste, a rota responde 404)
    assert liberado.get("/api/pmo/manual").status_code != 403


def test_entrada_vai_para_primeira_pagina_visivel(factory_app) -> None:
    c = _cliente(factory_app, _usuario(factory_app, "op2", "operador"))
    resp = c.get("/gov/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/gov/zabbix")


def test_mapa_cameras_auth_respeita_ajuste(factory_app) -> None:
    c = _cliente(factory_app, _usuario(factory_app, "op3", "operador", {"mapa_cameras": "ver"}))
    assert c.get("/gov/cameras/auth").status_code == 204
    c = _cliente(factory_app, _usuario(factory_app, "gest4", "gestor", {"mapa_cameras": "nenhum"}))
    assert c.get("/gov/cameras/auth").status_code == 403


# ── Tela de permissões ───────────────────────────────────────────────────────


def _form(ajustes: dict[str, str]) -> dict[str, str]:
    return {f"p_{p.chave}": ajustes.get(p.chave, "padrao") for p in perm.PAGINAS}


def test_tela_mostra_padrao_do_perfil(factory_app) -> None:
    admin = _cliente(factory_app, _usuario(factory_app, "adm", "admin"))
    alvo = _usuario(factory_app, "alvo", "operador", {"zendesk": "ver"})
    html = admin.get(f"/gov/users/{alvo}/permissoes").get_data(as_text=True)
    assert "Pelo perfil (só vê)" in html  # CFTV para operador
    assert "ajustado (perfil: não vê)" in html  # Zendesk
    assert 'name="p_zendesk" value="ver" class="peer sr-only" checked' in html


def test_so_admin_abre_a_tela(factory_app) -> None:
    gestor = _cliente(factory_app, _usuario(factory_app, "gest5", "gestor"))
    alvo = _usuario(factory_app, "alvo2", "operador")
    assert gestor.get(f"/gov/users/{alvo}/permissoes").status_code == 403


def test_salvar_sem_super_admin_aplica_direto(factory_app) -> None:
    with factory_app.app_context():
        User.query.filter_by(super_admin=True).update({"super_admin": False})
        db.session.commit()
    admin = _cliente(factory_app, _usuario(factory_app, "adm2", "admin"))
    alvo = _usuario(factory_app, "alvo3", "operador")
    resp = admin.post(f"/gov/users/{alvo}/permissoes", data=_form({"zendesk": "ver", "cftv": "ver"}))
    assert resp.status_code == 302
    with factory_app.app_context():
        # cftv "ver" é o padrão do operador: não vira ajuste
        assert db.session.get(User, alvo).permissoes == {"zendesk": "ver"}


def test_salvar_por_admin_vai_para_aprovacao(factory_app) -> None:
    super_id = _usuario(factory_app, "super2", "admin", super_admin=True)
    admin = _cliente(factory_app, _usuario(factory_app, "adm3", "admin"))
    alvo = _usuario(factory_app, "alvo4", "operador")

    admin.post(f"/gov/users/{alvo}/permissoes", data=_form({"zendesk": "ver", "zabbix": "nenhum"}))
    with factory_app.app_context():
        assert db.session.get(User, alvo).permissoes == {}
        sol = Solicitacao.query.filter_by(status=PENDENTE).one()
        assert "Zendesk: só vê" in sol.resumo and "Zabbix: não vê" in sol.resumo
        sol_id = sol.id

    _cliente(factory_app, super_id).post(f"/gov/aprovacoes/{sol_id}/aprovar")
    with factory_app.app_context():
        assert db.session.get(User, alvo).permissoes == {"zendesk": "ver", "zabbix": "nenhum"}


def test_voltar_tudo_ao_perfil(factory_app) -> None:
    super_cli = _cliente(factory_app, _usuario(factory_app, "super3", "admin", super_admin=True))
    alvo = _usuario(factory_app, "alvo5", "operador", {"zendesk": "ver"})
    super_cli.post(f"/gov/users/{alvo}/permissoes", data=_form({}))
    with factory_app.app_context():
        assert db.session.get(User, alvo).permissoes == {}


def test_super_admin_nao_tem_ajuste(factory_app) -> None:
    super_id = _usuario(factory_app, "super4", "admin", super_admin=True)
    html = _cliente(factory_app, super_id).get(f"/gov/users/{super_id}/permissoes").get_data(as_text=True)
    assert "sempre vê e altera tudo" in html
    assert 'name="p_' not in html


def test_lista_de_usuarios_mostra_ajustes(factory_app) -> None:
    admin = _cliente(factory_app, _usuario(factory_app, "adm4", "admin"))
    alvo = _usuario(factory_app, "alvo6", "operador", {"zendesk": "ver", "sla": "ver"})
    html = admin.get("/gov/users").get_data(as_text=True)
    assert f"/gov/users/{alvo}/permissoes" in html
    assert "+ 2 ajustes" in html


def test_garantir_coluna_permissoes(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'antigo.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255))"))
        conn.execute(text("INSERT INTO users (email) VALUES ('a@b')"))
    garantir_coluna_permissoes(engine)
    garantir_coluna_permissoes(engine)
    assert "permissoes" in {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT permissoes FROM users")).scalar() == "{}"
