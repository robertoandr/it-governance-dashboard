"""Tests for infra-setup/12_cftv_verificacao.py (sem gravar/sem imagem → só WhatsApp)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "12_cftv_verificacao.py"
_spec = importlib.util.spec_from_file_location("cftv_verificacao_script", _MODULE_PATH)
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)  # type: ignore[union-attr]

_FILTRO_12 = {
    "evaltype": "3",
    "formula": "A and B and (C or D)",
    "conditions": [
        {"conditiontype": "16", "operator": "11", "value": "", "value2": "", "formulaid": "A"},
        {"conditiontype": "26", "operator": "1", "value": "pmo", "value2": "origem", "formulaid": "B"},
        {"conditiontype": "26", "operator": "1", "value": "performance", "value2": "scope", "formulaid": "C"},
        {"conditiontype": "0", "operator": "1", "value": "35", "value2": "", "formulaid": "D"},
    ],
}


def test_filtro_sem_chamado_soma_condicao_e() -> None:
    novo = cv.filtro_sem_chamado(_FILTRO_12)
    assert novo["formula"] == "(A and B and (C or D)) and E"
    assert novo["conditions"][-1] == {
        "conditiontype": "26",
        "operator": "1",
        "value": "so-whatsapp",
        "value2": "alerta",
        "formulaid": "E",
    }
    # Manutenção (16) sem value: o Zabbix 7.4 recusa a condição reenviada com ele
    assert "value" not in novo["conditions"][0]
    assert novo["conditions"][3] == {"conditiontype": "0", "operator": "1", "formulaid": "D", "value": "35"}


def test_filtro_sem_chamado_idempotente() -> None:
    novo = cv.filtro_sem_chamado(_FILTRO_12)
    assert cv.filtro_sem_chamado({**_FILTRO_12, "conditions": novo["conditions"]}) is None


def test_filtro_sem_formula_usa_and() -> None:
    filtro = {"evaltype": "0", "formula": "", "conditions": [_FILTRO_12["conditions"][0] | {"formulaid": "A"}]}
    assert cv.filtro_sem_chamado(filtro)["formula"] == "A and B"


def test_operacoes_escrita_tira_ids() -> None:
    ops = [
        {
            "operationid": "9",
            "actionid": "13",
            "operationtype": "0",
            "esc_step_from": "1",
            "opmessage": {"default_msg": "1", "mediatypeid": "105", "subject": "x", "operationid": "9"},
            "opmessage_usr": [{"operationid": "9", "userid": "5"}],
        }
    ]
    assert cv.operacoes_escrita(ops) == [
        {
            "operationtype": "0",
            "esc_step_from": "1",
            "opmessage": {"default_msg": "1", "mediatypeid": "105"},
            "opmessage_usr": [{"userid": "5"}],
        }
    ]


def test_triggers_usam_as_chaves_do_dashboard() -> None:
    from app.services import cftv_verificacao

    chaves = {c for c, _ in cv.ITENS}
    assert chaves == {cftv_verificacao.CHAVE_SEM_GRAVAR, cftv_verificacao.CHAVE_SEM_IMAGEM}
    assert {t[0] for t in cv.TRIGGERS} == chaves
    assert cv.expr_trigger("cftv.verif.sem_gravar") == (
        "length(last(/CFTV Verificação (dashboard)/cftv.verif.sem_gravar))>0"
    )
