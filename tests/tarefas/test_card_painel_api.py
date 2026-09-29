"""Painel do card (Sprint 3): detalhes, edição, documento com versão e comentários, por perfil."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from flask.testing import FlaskClient

from tests.tarefas.conftest import AJAX

Cliente = Callable[..., FlaskClient]
API = "/api/v1/tarefas"


@pytest.fixture
def card(cliente: Cliente) -> dict:
    c = cliente("admin")
    ws = c.post(f"{API}/workspaces", json={"name": "Infraestrutura"}, headers=AJAX).get_json()
    board_id = ws["boards"][0]["id"]
    r = c.post(f"{API}/boards/{board_id}/cards", json={"title": "Trocar switch do CPD"}, headers=AJAX)
    return {**r.get_json(), "board_id": board_id}


def _doc(c: FlaskClient, card_id: int, texto: str, versao: int):
    return c.put(f"{API}/cards/{card_id}/document", json={"content_md": texto, "version": versao}, headers=AJAX)


# ── Detalhes e edição ────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["admin", "gestor", "operador", "visualizador"])
def test_todos_veem_o_card(cliente: Cliente, card: dict, role: str) -> None:
    r = cliente(role).get(f"{API}/cards/{card['id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["title"] == "Trocar switch do CPD"
    assert body["workspace"]["name"] == "Infraestrutura"
    assert body["board"]["id"] == card["board_id"]
    assert body["created_by"]["name"] == "Pytest admin"
    assert body["created_at"].endswith("+00:00")
    assert [a["action"] for a in body["activity"]] == ["created"]


def test_card_inexistente(cliente: Cliente) -> None:
    c = cliente("admin")
    for caminho in ("", "/document", "/comments"):
        r = c.get(f"{API}/cards/999999{caminho}")
        assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")
    r = _doc(c, 999_999, "texto", 0)
    assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")


def test_editar_titulo_e_responsavel(cliente: Cliente, card: dict, usuario_id: Callable[..., int]) -> None:
    c = cliente("operador")
    responsavel = usuario_id("gestor")
    r = c.patch(
        f"{API}/cards/{card['id']}",
        json={"version": card["version"], "title": "  Trocar switch  ", "assignee_id": responsavel},
        headers=AJAX,
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["title"] == "Trocar switch"
    assert body["assignee"] == {"id": responsavel, "name": "Pytest gestor"}
    assert body["version"] == card["version"] + 1
    assert {a["action"] for a in body["activity"]} == {"created", "edited", "assigned"}

    r = c.patch(f"{API}/cards/{card['id']}", json={"version": body["version"], "assignee_id": None}, headers=AJAX)
    assert r.get_json()["assignee"] is None


def test_editar_sem_mudanca_nao_gera_versao(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    r = c.patch(f"{API}/cards/{card['id']}", json={"version": card["version"], "title": card["title"]}, headers=AJAX)
    assert r.get_json()["version"] == card["version"]


def test_editar_com_versao_velha_e_conflito(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    c.patch(f"{API}/cards/{card['id']}", json={"version": card["version"], "title": "Novo"}, headers=AJAX)
    r = c.patch(f"{API}/cards/{card['id']}", json={"version": card["version"], "title": "Outro"}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (409, "CONFLICT")


def test_responsavel_desativado_ou_inexistente(cliente: Cliente, card: dict, usuario_id: Callable[..., int]) -> None:
    c = cliente("admin")
    for invalido in (usuario_id("operador", ativo=False), 999_999):
        r = c.patch(
            f"{API}/cards/{card['id']}", json={"version": card["version"], "assignee_id": invalido}, headers=AJAX
        )
        assert (r.status_code, r.get_json()["code"]) == (400, "INVALID_PAYLOAD")


@pytest.mark.parametrize("payload", [{"title": "x"}, {"version": 1, "title": ""}, {"version": 1, "status": "done"}])
def test_editar_payload_invalido(cliente: Cliente, card: dict, payload: dict) -> None:
    r = cliente("admin").patch(f"{API}/cards/{card['id']}", json=payload, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (400, "INVALID_PAYLOAD")


def test_editar_card_inexistente(cliente: Cliente) -> None:
    r = cliente("admin").patch(f"{API}/cards/999999", json={"version": 1, "title": "x"}, headers=AJAX)
    assert r.status_code == 404


def test_visualizador_nao_edita_documento_nem_comenta(cliente: Cliente, card: dict) -> None:
    v = cliente("visualizador")
    assert v.patch(f"{API}/cards/{card['id']}", json={"version": 1, "title": "x"}, headers=AJAX).status_code == 403
    assert _doc(v, card["id"], "texto", 0).status_code == 403
    assert v.post(f"{API}/cards/{card['id']}/comments", json={"body_md": "oi"}, headers=AJAX).status_code == 403
    assert v.post(f"{API}/markdown/preview", json={"content_md": "# a"}, headers=AJAX).status_code == 403


def test_lista_de_usuarios_so_ativos(cliente: Cliente, usuario_id: Callable[..., int]) -> None:
    inativo = usuario_id("operador", ativo=False)
    body = cliente("visualizador").get(f"{API}/users").get_json()
    ids = {u["id"] for u in body["items"]}
    assert inativo not in ids
    assert set(body["items"][0]) == {"id", "name"}


# ── Documento ────────────────────────────────────────────────────────────────


def test_documento_novo_salvar_e_ler(cliente: Cliente, card: dict) -> None:
    c = cliente("operador")
    vazio = c.get(f"{API}/cards/{card['id']}/document").get_json()
    assert (vazio["version"], vazio["content_md"], vazio["html"]) == (0, "", "")

    r = _doc(c, card["id"], "## Decisão\nUsar **VLAN 20**", 0)
    assert r.status_code == 200
    salvo = r.get_json()
    assert salvo["version"] == 1
    assert "<strong>VLAN 20</strong>" in salvo["html"]
    assert salvo["updated_by"] == "Pytest operador"

    r = _doc(c, card["id"], "## Decisão\nUsar VLAN 30", 1)
    assert r.get_json()["version"] == 2
    assert (
        cliente("visualizador").get(f"{API}/cards/{card['id']}/document").get_json()["content_md"].endswith("VLAN 30")
    )


def test_conflito_de_versao_devolve_o_atual_e_nao_perde_nada(cliente: Cliente, card: dict) -> None:
    a, b = cliente("admin"), cliente("gestor")
    _doc(a, card["id"], "texto da Ana", 0)
    r = _doc(b, card["id"], "texto do Bruno", 0)  # Bruno começou com a versão 0
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "CONFLICT"
    assert body["current"]["content_md"] == "texto da Ana"
    assert body["current"]["version"] == 1
    # o documento no servidor continua o da Ana
    assert a.get(f"{API}/cards/{card['id']}/document").get_json()["content_md"] == "texto da Ana"
    # Bruno decide gravar por cima, usando a versão atual
    assert _doc(b, card["id"], "texto do Bruno", 1).status_code == 200


def test_conflito_quando_documento_ainda_nao_existe(cliente: Cliente, card: dict) -> None:
    r = _doc(cliente("admin"), card["id"], "texto", 3)
    assert r.status_code == 409
    assert r.get_json()["current"]["version"] == 0


def test_documento_com_xss_e_salvo_mas_renderizado_seguro(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    r = _doc(c, card["id"], '<script>alert(1)</script>[x](javascript:alert(1))<img src=x onerror="a()">', 0)
    html = r.get_json()["html"]
    assert "<script" not in html and "<img" not in html and 'href="javascript' not in html


def test_documento_acima_do_limite(cliente: Cliente, card: dict) -> None:
    r = _doc(cliente("admin"), card["id"], "á" * (110 * 1024), 0)  # ~220 KB em UTF-8
    assert (r.status_code, r.get_json()["code"]) == (400, "INVALID_PAYLOAD")


def test_documento_muda_revisao_do_board_so_quando_vira_documentado(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    revisao = lambda: c.get(f"{API}/boards/{card['board_id']}/revision").get_json()["revision"]  # noqa: E731
    inicial = revisao()
    _doc(c, card["id"], "primeira versão", 0)
    assert revisao() == inicial + 1  # passou a ter documento
    _doc(c, card["id"], "segunda versão", 1)
    assert revisao() == inicial + 1  # autosave comum não recarrega os outros boards
    _doc(c, card["id"], "   ", 2)
    assert revisao() == inicial + 2  # deixou de ter documento
    cards = c.get(f"{API}/boards/{card['board_id']}/cards").get_json()["columns"]["backlog"]
    assert cards[0]["has_document"] is False


def test_preview(cliente: Cliente) -> None:
    r = cliente("operador").post(
        f"{API}/markdown/preview", json={"content_md": "# Oi <script>x</script>"}, headers=AJAX
    )
    assert r.status_code == 200
    assert r.get_json()["html"] == "<h1>Oi &lt;script&gt;x&lt;/script&gt;</h1>\n"
    r = cliente("operador").post(f"{API}/markdown/preview", json={"content_md": "á" * (110 * 1024)}, headers=AJAX)
    assert r.status_code == 400


# ── Comentários ──────────────────────────────────────────────────────────────


def _comentar(c: FlaskClient, card_id: int, texto: str) -> dict:
    r = c.post(f"{API}/cards/{card_id}/comments", json={"body_md": texto}, headers=AJAX)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_comentar_listar_e_contar_no_board(cliente: Cliente, card: dict) -> None:
    op = cliente("operador")
    criado = _comentar(op, card["id"], "Precisa de **janela** de manutenção")
    assert criado["author"]["name"] == "Pytest operador"
    assert "<strong>janela</strong>" in criado["html"]
    assert (criado["can_edit"], criado["can_delete"]) == (True, True)
    _comentar(cliente("gestor"), card["id"], "Ok, sábado")

    lista = cliente("visualizador").get(f"{API}/cards/{card['id']}/comments").get_json()
    assert [c["body_md"] for c in lista["items"]] == ["Precisa de **janela** de manutenção", "Ok, sábado"]
    assert all(not c["can_edit"] and not c["can_delete"] for c in lista["items"])
    cards = op.get(f"{API}/boards/{card['board_id']}/cards").get_json()["columns"]["backlog"]
    assert cards[0]["comment_count"] == 2


def test_so_o_autor_edita(cliente: Cliente, card: dict) -> None:
    com = _comentar(cliente("operador"), card["id"], "original")
    r = cliente("gestor").patch(f"{API}/comments/{com['id']}", json={"body_md": "hackeado"}, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (403, "FORBIDDEN")
    r = cliente("operador").patch(f"{API}/comments/{com['id']}", json={"body_md": "corrigido"}, headers=AJAX)
    assert r.status_code == 200
    assert r.get_json()["body_md"] == "corrigido"
    assert r.get_json()["edited_at"]


def test_excluir_autor_ou_admin(cliente: Cliente, card: dict) -> None:
    op, gestor, admin = cliente("operador"), cliente("gestor"), cliente("admin")
    um = _comentar(op, card["id"], "um")
    dois = _comentar(op, card["id"], "dois")
    visto_pelo_admin = admin.get(f"{API}/cards/{card['id']}/comments").get_json()["items"][0]
    assert (visto_pelo_admin["can_edit"], visto_pelo_admin["can_delete"]) == (False, True)

    r = gestor.delete(f"{API}/comments/{um['id']}", headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (403, "FORBIDDEN")
    assert op.delete(f"{API}/comments/{um['id']}", headers=AJAX).status_code == 204
    assert admin.delete(f"{API}/comments/{dois['id']}", headers=AJAX).status_code == 204
    assert op.get(f"{API}/cards/{card['id']}/comments").get_json()["total"] == 0
    r = op.delete(f"{API}/comments/{um['id']}", headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (404, "NOT_FOUND")
    r = op.patch(f"{API}/comments/{um['id']}", json={"body_md": "x"}, headers=AJAX)
    assert r.status_code == 404


@pytest.mark.parametrize("payload", [{"body_md": ""}, {"body_md": "   "}, {"body_md": "x" * 10_001}, {"texto": "x"}])
def test_comentario_invalido(cliente: Cliente, card: dict, payload: dict) -> None:
    c = cliente("admin")
    r = c.post(f"{API}/cards/{card['id']}/comments", json=payload, headers=AJAX)
    assert (r.status_code, r.get_json()["code"]) == (400, "INVALID_PAYLOAD")
    com = _comentar(c, card["id"], "ok")
    r = c.patch(f"{API}/comments/{com['id']}", json=payload, headers=AJAX)
    assert r.status_code == 400


def test_comentario_com_xss_e_neutralizado(cliente: Cliente, card: dict) -> None:
    com = _comentar(cliente("admin"), card["id"], "<img src=x onerror=alert(1)> [a](javascript:alert(1))")
    assert "<img" not in com["html"] and 'href="javascript' not in com["html"]


def test_comentario_em_card_inexistente(cliente: Cliente) -> None:
    r = cliente("admin").post(f"{API}/cards/999999/comments", json={"body_md": "x"}, headers=AJAX)
    assert r.status_code == 404


def test_card_de_workspace_excluido_some(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    com = _comentar(c, card["id"], "antes")
    ws_id = c.get(f"{API}/workspaces").get_json()["items"][0]["id"]
    c.delete(f"{API}/workspaces/{ws_id}", headers=AJAX)
    assert c.get(f"{API}/cards/{card['id']}").status_code == 404
    assert c.delete(f"{API}/comments/{com['id']}", headers=AJAX).status_code == 404


def test_escritas_exigem_header_ajax(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    assert c.put(f"{API}/cards/{card['id']}/document", json={"content_md": "x", "version": 0}).status_code == 403
    assert c.post(f"{API}/cards/{card['id']}/comments", json={"body_md": "x"}).status_code == 403
    assert c.patch(f"{API}/cards/{card['id']}", json={"version": 1, "title": "x"}).status_code == 403


def test_documento_so_com_quebras_de_linha_vira_vazio(cliente: Cliente, card: dict) -> None:
    c = cliente("admin")
    _doc(c, card["id"], "texto", 0)
    r = _doc(c, card["id"], "\n\n\t  \n", 1)
    assert (r.get_json()["content_md"], r.get_json()["html"]) == ("", "")
    cards = c.get(f"{API}/boards/{card['board_id']}/cards").get_json()["columns"]["backlog"]
    assert cards[0]["has_document"] is False


def test_historico_nao_enche_com_salvamentos_automaticos(cliente: Cliente, card: dict) -> None:
    op, gestor = cliente("operador"), cliente("gestor")
    for versao in range(4):  # quatro salvamentos seguidos da mesma pessoa
        assert _doc(op, card["id"], f"versão {versao}", versao).status_code == 200
    _doc(gestor, card["id"], "versão do gestor", 4)
    _doc(op, card["id"], "de novo o operador", 5)
    acoes = [(a["action"], a["actor"]) for a in op.get(f"{API}/cards/{card['id']}").get_json()["activity"]]
    assert acoes == [
        ("edited", "Pytest operador"),
        ("edited", "Pytest gestor"),
        ("edited", "Pytest operador"),
        ("created", "Pytest admin"),
    ]


def test_logs_nao_contem_conteudo_de_documento_nem_comentario(cliente: Cliente, card: dict) -> None:
    """Critério da Sprint 3: só ids, ações e tamanho vão para o log, nunca o texto."""
    from structlog.testing import capture_logs

    segredo_doc = "SEGREDO-DOC-senha-do-switch"
    segredo_com = "SEGREDO-COMENTARIO-ip-interno"
    c = cliente("admin")
    with capture_logs() as eventos:
        _doc(c, card["id"], f"## Decisão\n{segredo_doc}", 0)
        com = _comentar(c, card["id"], segredo_com)
        c.patch(f"{API}/comments/{com['id']}", json={"body_md": segredo_com + " editado"}, headers=AJAX)
    assert {e["event"] for e in eventos} >= {"tarefas.documento_salvo", "tarefas.comentario_criado"}
    texto_dos_logs = repr(eventos)
    assert segredo_doc not in texto_dos_logs
    assert segredo_com not in texto_dos_logs
