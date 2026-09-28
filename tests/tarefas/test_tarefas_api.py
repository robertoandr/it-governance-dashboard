"""API /api/v1/tarefas/workspaces: permissões por perfil, validação e proteção CSRF."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from tests.tarefas.conftest import AJAX

URL = "/api/v1/tarefas/workspaces"
Cliente = Callable[..., FlaskClient]


def _criar(c: FlaskClient, nome: str = "Infraestrutura") -> dict:
    r = c.post(URL, json={"name": nome}, headers=AJAX)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


@pytest.mark.parametrize("role", ["admin", "gestor", "operador", "visualizador"])
def test_todos_os_perfis_listam(cliente: Cliente, role: str) -> None:
    _criar(cliente("admin"))
    r = cliente(role).get(URL)
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 1
    assert body["limit"] == 100 and body["offset"] == 0
    assert body["items"][0]["name"] == "Infraestrutura"
    assert body["items"][0]["boards"][0]["name"] == "Principal"


@pytest.mark.parametrize("role", ["admin", "gestor"])
def test_admin_e_gestor_criam(cliente: Cliente, role: str) -> None:
    body = _criar(cliente(role), "Redes")
    assert body["name"] == "Redes"
    assert len(body["boards"]) == 1
    assert body["created_at"]


@pytest.mark.parametrize("role", ["operador", "visualizador"])
def test_operador_e_visualizador_nao_gerenciam(cliente: Cliente, role: str) -> None:
    ws = _criar(cliente("admin"))
    c = cliente(role)
    assert c.post(URL, json={"name": "Outro"}, headers=AJAX).status_code == 403
    assert c.patch(f"{URL}/{ws['id']}", json={"name": "Outro"}, headers=AJAX).status_code == 403
    assert c.delete(f"{URL}/{ws['id']}", headers=AJAX).status_code == 403


def test_anonimo_recebe_401(factory_app) -> None:
    assert factory_app.test_client().get(URL).status_code == 401


def test_usuario_inativo_perde_acesso(cliente: Cliente) -> None:
    c = cliente("gestor", ativo=False)
    assert c.get(URL).status_code == 401
    assert c.post(URL, json={"name": "Infra"}, headers=AJAX).status_code == 401


def test_escrita_sem_header_ajax_e_recusada(cliente: Cliente) -> None:
    r = cliente("admin").post(URL, json={"name": "Infra"})
    assert r.status_code == 403
    assert r.get_json()["code"] == "FORBIDDEN"


def test_escrita_sem_json_e_recusada(cliente: Cliente) -> None:
    r = cliente("admin").post(URL, data={"name": "Infra"}, headers=AJAX)
    assert r.status_code == 400
    assert r.get_json()["code"] == "INVALID_PAYLOAD"


@pytest.mark.parametrize("payload", [{"name": "ab"}, {"name": "x" * 61}, {}, {"name": "Infra", "extra": 1}])
def test_payload_invalido(cliente: Cliente, payload: dict) -> None:
    r = cliente("admin").post(URL, json=payload, headers=AJAX)
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "INVALID_PAYLOAD"
    assert body["error"]


def test_nome_duplicado_na_criacao(cliente: Cliente) -> None:
    c = cliente("admin")
    _criar(c, "CFTV")
    r = c.post(URL, json={"name": "cftv"}, headers=AJAX)
    assert r.status_code == 409
    assert r.get_json()["code"] == "DUPLICATE"


def test_renomear(cliente: Cliente) -> None:
    c = cliente("gestor")
    ws = _criar(c)
    r = c.patch(f"{URL}/{ws['id']}", json={"name": "Infra"}, headers=AJAX)
    assert r.status_code == 200
    assert r.get_json()["name"] == "Infra"


def test_renomear_invalido_inexistente_e_duplicado(cliente: Cliente) -> None:
    c = cliente("admin")
    infra = _criar(c, "Infra")
    _criar(c, "Redes")
    assert c.patch(f"{URL}/{infra['id']}", json={"name": "x"}, headers=AJAX).get_json()["code"] == "INVALID_PAYLOAD"
    r = c.patch(f"{URL}/999999", json={"name": "Nada"}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")
    r = c.patch(f"{URL}/{infra['id']}", json={"name": "REDES"}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (409, "CONFLICT")


def test_excluir(cliente: Cliente) -> None:
    c = cliente("admin")
    ws = _criar(c)
    assert c.delete(f"{URL}/{ws['id']}", headers=AJAX).status_code == 204
    assert c.get(URL).get_json()["total"] == 0
    r = c.delete(f"{URL}/{ws['id']}", headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")


def test_excluir_sem_header_ajax_e_recusado(cliente: Cliente) -> None:
    c = cliente("admin")
    ws = _criar(c)
    assert c.delete(f"{URL}/{ws['id']}").status_code == 403
    assert c.get(URL).get_json()["total"] == 1
