"""Testes de itgov/api/v1/pmo_clickup.py — tarefas ClickUp e resumo do PMO."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from itgov.api.v1 import pmo_clickup as pmo


def _ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


_ONTEM = datetime.now(UTC) - timedelta(days=1)
_AMANHA = datetime.now(UTC) + timedelta(days=1)

_TAREFA_ATRASADA = {
    "id": "a1",
    "name": "Migrar firewall",
    "status": {"status": "In Progress"},
    "priority": {"orderindex": "1"},
    "assignees": [{"username": "ana"}, {"email": "bruno@x"}],
    "due_date": _ms(_ONTEM),
    "custom_fields": [
        {"id": pmo._FIELD_CONCLUIDO, "value": {"percent_complete": 40}},
        {"name": "Equipe", "value": 1},
        {
            "name": "Projeto",
            "type_config": {"options": [{"id": "p1", "label": "Rede"}]},
            "value": ["p1", "p-desconhecido"],
        },
    ],
    "url": "https://app.clickup.com/t/a1",
}
_TAREFA_CONCLUIDA = {
    "id": "a2",
    "name": "Renovar certificado",
    "status": {"status": "complete"},
    "priority": None,
    "due_date": _ms(_ONTEM),
    "date_closed": _ms(_ONTEM),
    "custom_fields": [{"name": "Equipe", "value": 0}],
}
_TAREFA_NOVA = {
    "id": "a3",
    "name": "Inventário",
    "status": {"status": "status-desconhecido"},
    "due_date": _ms(_AMANHA),
}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_TOKEN", "pk_teste")
    pmo._CACHE.clear()


def _resp(status: int, payload: dict | None = None) -> MagicMock:
    resp = MagicMock(status_code=status)
    resp.json.return_value = payload or {}
    return resp


def test_parse_task_atrasada_com_campos_customizados() -> None:
    t = pmo._parse_task(_TAREFA_ATRASADA)
    assert t["status"] == "in_progress"
    assert t["priority"] == "urgent"
    assert t["assignees"] == ["ana", "bruno@x"]
    assert t["overdue"] is True
    assert t["pct_complete"] == 40
    assert t["equipe"] == "Infraestrutura e Suporte"
    assert t["projeto"] == ["Rede", "p-desconhecido"]


def test_parse_task_concluida_e_desconhecida() -> None:
    feita = pmo._parse_task(_TAREFA_CONCLUIDA)
    assert feita["status"] == "done"
    assert feita["priority"] == "normal"
    assert feita["overdue"] is False
    assert feita["pct_complete"] == 100
    assert feita["date_closed"] == _ONTEM.strftime("%d/%m/%Y")
    assert feita["equipe"] == "Sistemas e Integração"

    nova = pmo._parse_task(_TAREFA_NOVA)
    assert nova["status"] == "todo"
    assert nova["overdue"] is False
    assert nova["equipe"] is None
    assert nova["projeto"] == []


def test_fetch_tasks_sem_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_TOKEN", "")
    with patch.object(pmo.requests, "get") as get:
        assert pmo._fetch_tasks() == []
    get.assert_not_called()


def test_fetch_tasks_pagina_ate_last_page() -> None:
    respostas = [
        _resp(200, {"tasks": [_TAREFA_ATRASADA]}),
        _resp(200, {"tasks": [_TAREFA_NOVA], "last_page": True}),
    ]
    with patch.object(pmo.requests, "get", side_effect=respostas) as get:
        tarefas = pmo._fetch_tasks()
    assert [t["id"] for t in tarefas] == ["a1", "a3"]
    assert [c.kwargs["params"]["page"] for c in get.call_args_list] == [0, 1]
    assert get.call_args.kwargs["headers"]["Authorization"] == "pk_teste"


@pytest.mark.parametrize(
    "efeito",
    [_resp(401), _resp(500), requests.ConnectionError("fora"), _resp(200, {"tasks": []})],
    ids=["401", "500", "rede", "vazio"],
)
def test_fetch_tasks_para_em_erro_ou_lista_vazia(efeito) -> None:
    with patch.object(pmo.requests, "get", side_effect=[efeito]):
        assert pmo._fetch_tasks() == []


def test_get_cached_pmo_monta_resumo_e_usa_cache() -> None:
    tarefas = [_TAREFA_ATRASADA, _TAREFA_CONCLUIDA, _TAREFA_NOVA]
    with patch.object(pmo, "_fetch_tasks", return_value=tarefas) as fetch:
        r = pmo.get_cached_pmo()
        assert pmo.get_cached_pmo() is r
    assert fetch.call_count == 1

    assert r["has_data"] is True
    assert (r["total"], r["done"], r["in_progress"], r["todo"], r["overdue"]) == (3, 1, 1, 1, 1)
    assert r["pct_medio"] == 46
    assert r["taxa_conclusao"] == 33.3
    assert (r["infra_count"], r["sistemas_count"], r["sem_equipe_count"]) == (1, 1, 1)
    assert [t["id"] for t in r["ativos"]] == ["a1"]
    assert [t["id"] for t in r["concluidos"]] == ["a2"]

    pmo.invalidar_cache()
    with patch.object(pmo, "_fetch_tasks", return_value=[]):
        assert pmo.get_cached_pmo()["reason"] == "no_tasks"


def test_get_cached_pmo_sem_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKUP_TOKEN", "")
    r = pmo.get_cached_pmo()
    assert r["has_data"] is False
    assert r["token_ok"] is False
    assert r["reason"] == "token_missing"


def test_por_usuario_usa_tipo_do_status_e_conta_sem_responsavel() -> None:
    def tarefa(id_: str, tipo: str, nome: str, due: datetime | None, pessoas: list[str]) -> dict:
        return {
            "id": id_,
            "name": id_,
            "status": {"status": nome, "type": tipo},
            "due_date": _ms(due) if due else None,
            "assignees": [{"username": p, "email": f"{p}@x"} for p in pessoas],
        }

    brutas = [
        tarefa("t1", "custom", "atrasado", _ONTEM - timedelta(days=1), ["ana"]),
        tarefa("t2", "custom", "cancelado", _ONTEM - timedelta(days=1), ["ana"]),
        tarefa("t3", "closed", "finalizado", None, ["ana", "bia"]),
        tarefa("t4", "open", "a fazer", _AMANHA, ["bia"]),
        tarefa("t5", "open", "a fazer", _ONTEM - timedelta(days=1), []),
    ]
    with patch.object(pmo, "_fetch_tasks", return_value=brutas):
        r = pmo.get_cached_pmo()

    assert r["por_usuario"] == [
        {"nome": "ana", "abertas": 1, "concluidas": 2, "atrasadas": 1},
        {"nome": "bia", "abertas": 1, "concluidas": 1, "atrasadas": 0},
    ]
    assert (r["sem_responsavel_abertas"], r["sem_responsavel_atrasadas"]) == (1, 1)


def test_pagina_pmo_mostra_tabela_por_responsavel(authed_client) -> None:
    brutas = [
        {
            "id": "p1",
            "name": "Projeto atrasado",
            "status": {"status": "in progress", "type": "custom"},
            "due_date": _ms(_ONTEM - timedelta(days=1)),
            "assignees": [{"username": "Ana Lima", "email": "ana@x"}],
        }
    ]
    with patch.object(pmo, "_fetch_tasks", return_value=brutas):
        html = authed_client.get("/gov/pmo").get_data(as_text=True)
    assert "Por responsável" in html
    assert "Ana Lima" in html
    assert 'href="/gov/triggers"' in html
