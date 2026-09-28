"""Página /gov/tarefas, menu lateral, matriz de permissões e cookies de sessão."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.services.tarefas.permissions import Acao, perfis, pode
from tests.tarefas.conftest import AJAX

PAGINA = "/gov/tarefas"
Cliente = Callable[..., FlaskClient]


@pytest.mark.parametrize(
    ("role", "gerencia", "edita", "modera"),
    [
        ("admin", True, True, True),
        ("gestor", True, True, False),
        ("operador", False, True, False),
        ("visualizador", False, False, False),
    ],
)
def test_matriz_de_permissoes(role: str, gerencia: bool, edita: bool, modera: bool) -> None:
    assert pode(role, Acao.VER)
    assert pode(role, Acao.GERENCIAR_WORKSPACE) is gerencia
    assert pode(role, Acao.EDITAR_CARD) is edita
    assert pode(role, Acao.COMENTAR) is edita
    assert pode(role, Acao.MODERAR_COMENTARIO) is modera


def test_perfil_desconhecido_ou_anonimo_nao_pode_nada() -> None:
    for acao in Acao:
        assert not pode(None, acao)
        assert not pode("convidado", acao)
    assert perfis(Acao.VER) == ("admin", "gestor", "operador", "visualizador")


@pytest.mark.parametrize("role", ["admin", "gestor", "operador", "visualizador"])
def test_pagina_abre_para_todos_os_perfis_com_item_no_menu(cliente: Cliente, role: str) -> None:
    r = cliente(role).get(PAGINA)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'href="/gov/tarefas"' in html
    assert "Nenhum workspace ainda" in html


@pytest.mark.parametrize(("role", "ve_botao"), [("admin", True), ("gestor", True), ("operador", False)])
def test_botoes_de_gestao_so_para_admin_e_gestor(cliente: Cliente, role: str, ve_botao: bool) -> None:
    cliente("admin").post("/api/v1/tarefas/workspaces", json={"name": "Infraestrutura"}, headers=AJAX)
    html = cliente(role).get(PAGINA).get_data(as_text=True)
    assert "Infraestrutura" in html
    assert "Principal" in html
    assert ('@click="abrirCriar"' in html) is ve_botao
    assert ('@click="abrirExcluir"' in html) is ve_botao


def test_pagina_usa_url_da_api_gerada_pelo_flask(cliente: Cliente) -> None:
    html = cliente("admin").get(PAGINA).get_data(as_text=True)
    assert 'var _tarefasApi = "/api/v1/tarefas/workspaces";' in html


def test_data_de_criacao_no_horario_de_brasilia(factory_app: Flask, usuario_id: Callable[..., int]) -> None:
    from datetime import UTC, datetime

    from app.models.tarefas import Workspace

    ws = Workspace(name="Infra", created_by=usuario_id("admin"))
    ws.created_at = datetime(2026, 9, 29, 1, 30)  # 01:30 UTC ingênuo, como volta do SQLite
    assert ws.criado_em_local.strftime("%d/%m/%Y %H:%M") == "28/09/2026 22:30"
    ws.created_at = datetime(2026, 9, 29, 1, 30, tzinfo=UTC)
    assert ws.criado_em_local.strftime("%d/%m/%Y") == "28/09/2026"
    ws.created_at = None
    assert ws.criado_em_local is None


def test_estado_vazio_orienta_quem_nao_gerencia(cliente: Cliente) -> None:
    html = cliente("visualizador").get(PAGINA).get_data(as_text=True)
    assert "Peça a um gestor ou admin" in html


def test_nome_do_workspace_e_escapado(cliente: Cliente) -> None:
    cliente("admin").post("/api/v1/tarefas/workspaces", json={"name": '<img src=x onerror="a()">'}, headers=AJAX)
    html = cliente("admin").get(PAGINA).get_data(as_text=True)
    assert '<img src=x onerror="a()">' not in html
    assert "&lt;img src=x" in html


def test_anonimo_vai_para_o_login(factory_app: Flask) -> None:
    r = factory_app.test_client().get(PAGINA)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_usuario_desativado_com_sessao_aberta_perde_acesso(cliente: Cliente) -> None:
    """Cobre o user_loader: sessão de usuário desativado conta como deslogada."""
    r = cliente("admin", ativo=False).get(PAGINA)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
