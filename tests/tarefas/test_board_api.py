"""API do board: listar cards, revisão, criar e mover, com permissões por perfil."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from tests.tarefas.conftest import AJAX

Cliente = Callable[..., FlaskClient]


@pytest.fixture
def board_id(cliente: Cliente) -> int:
    r = cliente("admin").post("/api/v1/tarefas/workspaces", json={"name": "Infraestrutura"}, headers=AJAX)
    return r.get_json()["boards"][0]["id"]


def _url(board_id: int, sufixo: str = "cards") -> str:
    return f"/api/v1/tarefas/boards/{board_id}/{sufixo}"


def _criar(c: FlaskClient, board_id: int, titulo: str, status: str = "backlog") -> dict:
    r = c.post(_url(board_id), json={"title": titulo, "status": status}, headers=AJAX)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _mover(c: FlaskClient, card: dict, status: str, **vizinhos: int | None):
    corpo = {"status": status, "version": card["version"], **vizinhos}
    return c.patch(f"/api/v1/tarefas/cards/{card['id']}/move", json=corpo, headers=AJAX)


@pytest.mark.parametrize("role", ["admin", "gestor", "operador", "visualizador"])
def test_todos_os_perfis_veem_o_board(cliente: Cliente, board_id: int, role: str) -> None:
    _criar(cliente("admin"), board_id, "Trocar switch")
    r = cliente(role).get(_url(board_id))
    assert r.status_code == 200
    body = r.get_json()
    assert body["board"]["name"] == "Principal"
    assert body["board"]["workspace"]["name"] == "Infraestrutura"
    assert body["board"]["revision"] == 1
    assert list(body["columns"]) == ["backlog", "todo", "doing", "done"]
    card = body["columns"]["backlog"][0]
    assert card["title"] == "Trocar switch"
    assert set(card) == {"id", "title", "status", "position", "version", "assignee", "has_document", "comment_count"}
    assert cliente(role).get(_url(board_id, "revision")).get_json() == {"revision": 1}


def test_board_inexistente(cliente: Cliente) -> None:
    c = cliente("admin")
    assert c.get(_url(999_999)).get_json()["code"] == "NOT_FOUND"
    assert c.get(_url(999_999, "revision")).status_code == 404
    r = c.post(_url(999_999), json={"title": "x"}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")


@pytest.mark.parametrize("role", ["admin", "gestor", "operador"])
def test_quem_edita_cria_card(cliente: Cliente, board_id: int, role: str) -> None:
    card = _criar(cliente(role), board_id, "Novo", status="todo")
    assert (card["status"], card["assignee"], card["comment_count"]) == ("todo", None, 0)


def test_visualizador_nao_cria_nem_move(cliente: Cliente, board_id: int) -> None:
    card = _criar(cliente("admin"), board_id, "A")
    v = cliente("visualizador")
    assert v.post(_url(board_id), json={"title": "x"}, headers=AJAX).status_code == 403
    assert _mover(v, card, "done").status_code == 403


def test_escritas_exigem_header_ajax(cliente: Cliente, board_id: int) -> None:
    c = cliente("admin")
    card = _criar(c, board_id, "A")
    assert c.post(_url(board_id), json={"title": "x"}).status_code == 403
    r = c.patch(f"/api/v1/tarefas/cards/{card['id']}/move", json={"status": "todo", "version": 1})
    assert r.status_code == 403


@pytest.mark.parametrize("payload", [{"title": ""}, {"title": "ok", "status": "arquivado"}, {"titulo": "ok"}])
def test_criar_payload_invalido(cliente: Cliente, board_id: int, payload: dict) -> None:
    r = cliente("admin").post(_url(board_id), json=payload, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (400, "INVALID_PAYLOAD")


def test_mover_persiste_ordem_apos_recarregar(cliente: Cliente, board_id: int) -> None:
    c = cliente("operador")
    b = _criar(c, board_id, "B", status="todo")
    a = _criar(c, board_id, "A", status="todo")
    novo = _criar(c, board_id, "Novo")
    r = _mover(c, novo, "todo", before_id=a["id"], after_id=b["id"])
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "todo"
    assert body["version"] == novo["version"] + 1
    assert body["board_revision"] == 4
    assert body["reload"] is False

    recarregado = c.get(_url(board_id)).get_json()["columns"]
    assert [x["title"] for x in recarregado["todo"]] == ["A", "Novo", "B"]
    assert recarregado["backlog"] == []


def test_mover_com_versao_velha_da_409(cliente: Cliente, board_id: int) -> None:
    c = cliente("admin")
    card = _criar(c, board_id, "A")
    assert _mover(c, card, "todo").status_code == 200
    r = _mover(c, card, "doing")  # ainda com a versão antiga
    assert (r.status_code, r.get_json()["code"]) == (409, "CONFLICT")
    assert "Recarregue" in r.get_json()["error"]


def test_mover_payload_invalido_e_card_inexistente(cliente: Cliente, board_id: int) -> None:
    c = cliente("admin")
    card = _criar(c, board_id, "A")
    r = c.patch(f"/api/v1/tarefas/cards/{card['id']}/move", json={"status": "fim"}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (400, "INVALID_PAYLOAD")
    r = c.patch("/api/v1/tarefas/cards/999999/move", json={"status": "todo", "version": 1}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")
