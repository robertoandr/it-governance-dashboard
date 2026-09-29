"""Página /gov/tarefas, menu lateral, matriz de permissões e cookies de sessão."""

from __future__ import annotations

import re
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


# ── Página do board (Sprint 2) ───────────────────────────────────────────────


def _board_id(cliente: Cliente) -> int:
    r = cliente("admin").post("/api/v1/tarefas/workspaces", json={"name": "Infraestrutura"}, headers=AJAX)
    return r.get_json()["boards"][0]["id"]


def test_workspaces_linkam_para_o_board(cliente: Cliente) -> None:
    board_id = _board_id(cliente)
    html = cliente("visualizador").get(PAGINA).get_data(as_text=True)
    assert f'href="/gov/tarefas/b/{board_id}"' in html


@pytest.mark.parametrize(("role", "edita"), [("admin", True), ("operador", True), ("visualizador", False)])
def test_pagina_do_board(cliente: Cliente, role: str, edita: bool) -> None:
    board_id = _board_id(cliente)
    r = cliente(role).get(f"{PAGINA}/b/{board_id}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Infraestrutura" in html and "Principal" in html
    assert f'data-url-cards="/api/v1/tarefas/boards/{board_id}/cards"' in html
    assert f'data-url-revision="/api/v1/tarefas/boards/{board_id}/revision"' in html
    assert 'data-url-move="/api/v1/tarefas/cards/0/move"' in html
    assert f'data-pode-editar="{"true" if edita else "false"}"' in html
    assert ("Adicionar card" in html) is edita
    assert "vendor/sortable.min.js" in html and "tarefas/board.js" in html
    for coluna in ("Backlog", "To Do", "Doing", "Done"):
        assert coluna in html


def test_board_inexistente_ou_excluido_da_404(cliente: Cliente) -> None:
    board_id = _board_id(cliente)
    c = cliente("admin")
    assert c.get(f"{PAGINA}/b/999999").status_code == 404
    ws_id = c.get("/api/v1/tarefas/workspaces").get_json()["items"][0]["id"]
    c.delete(f"/api/v1/tarefas/workspaces/{ws_id}", headers=AJAX)
    assert c.get(f"{PAGINA}/b/{board_id}").status_code == 404


def test_board_anonimo_vai_para_o_login(factory_app: Flask) -> None:
    r = factory_app.test_client().get(f"{PAGINA}/b/1")
    assert r.status_code == 302


# ── Painel do card (Sprint 3) ────────────────────────────────────────────────


def _card(cliente: Cliente) -> tuple[int, int]:
    board_id = _board_id(cliente)
    r = cliente("admin").post(f"/api/v1/tarefas/boards/{board_id}/cards", json={"title": "Card"}, headers=AJAX)
    return board_id, r.get_json()["id"]


@pytest.mark.parametrize(("role", "edita"), [("admin", True), ("visualizador", False)])
def test_url_propria_do_card_abre_o_painel(cliente: Cliente, role: str, edita: bool) -> None:
    board_id, card_id = _card(cliente)
    r = cliente(role).get(f"{PAGINA}/b/{board_id}/c/{card_id}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'data-card-aberto="{card_id}"' in html
    assert f'data-url-card-page="/gov/tarefas/b/{board_id}/c/0"' in html
    assert 'data-url-documento="/api/v1/tarefas/cards/0/document"' in html
    assert 'data-url-comentario="/api/v1/tarefas/comments/0"' in html
    assert "tarefas/card.js" in html
    assert ('id="doc-texto"' in html) is edita
    assert ('id="com-form"' in html) is edita
    assert f'data-pode-comentar="{"true" if edita else "false"}"' in html


def test_board_sem_card_aberto(cliente: Cliente) -> None:
    board_id = _board_id(cliente)
    assert 'data-card-aberto=""' in cliente("admin").get(f"{PAGINA}/b/{board_id}").get_data(as_text=True)


def test_card_de_outro_board_ou_inexistente_da_404(cliente: Cliente) -> None:
    board_id, card_id = _card(cliente)
    c = cliente("admin")
    outro = c.post("/api/v1/tarefas/workspaces", json={"name": "Redes"}, headers=AJAX).get_json()["boards"][0]["id"]
    assert c.get(f"{PAGINA}/b/{outro}/c/{card_id}").status_code == 404
    assert c.get(f"{PAGINA}/b/{board_id}/c/999999").status_code == 404


# ── Sprint 4: métricas e acessibilidade ──────────────────────────────────────


def test_board_tem_faixa_de_metricas(cliente: Cliente) -> None:
    board_id = _board_id(cliente)
    html = cliente("visualizador").get(f"/gov/tarefas/b/{board_id}").get_data(as_text=True)
    assert '<dl id="tarefas-metricas" aria-label="Resumo do board"' in html
    for metrica in ("total", "doing", "done", "done-doc"):
        assert f'data-metrica="{metrica}"' in html


def test_painel_do_card_e_dialog_em_div(cliente: Cliente) -> None:
    # <aside> não aceita role="dialog" (axe: aria-allowed-role).
    board_id = _board_id(cliente)
    html = cliente("admin").get(f"/gov/tarefas/b/{board_id}").get_data(as_text=True)
    assert '<div id="card-painel" hidden role="dialog"' in html
    assert '<aside id="card-painel"' not in html


@pytest.mark.parametrize("caminho", ["", "/b/{board_id}"])
def test_topbar_identifica_o_modulo_e_botao_do_menu_tem_nome(cliente: Cliente, caminho: str) -> None:
    board_id = _board_id(cliente)
    html = cliente("operador").get(PAGINA + caminho.format(board_id=board_id)).get_data(as_text=True)
    assert 'aria-label="Mostrar ou esconder o menu"' in html
    assert re.search(r'<h1 class="text-base[^"]*">\s*Tarefas\s*</h1>', html)
