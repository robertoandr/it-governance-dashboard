"""Tests for infra-setup/15_acronis_triggers.py (alertas do Acronis → triggers só no dashboard)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

from app.services import acronis_triggers

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "15_acronis_triggers.py"
_spec = importlib.util.spec_from_file_location("acronis_triggers_script", _MODULE_PATH)
s15 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s15)  # type: ignore[union-attr]

_FILTRO_12 = {
    "evaltype": "3",
    "formula": "A and B and (C or D) and E",
    "conditions": [
        {"conditiontype": "16", "operator": "11", "value": "", "value2": "", "formulaid": "A"},
        {"conditiontype": "26", "operator": "1", "value": "pmo", "value2": "origem", "formulaid": "B"},
        {"conditiontype": "26", "operator": "1", "value": "performance", "value2": "scope", "formulaid": "C"},
        {"conditiontype": "0", "operator": "1", "value": "35", "value2": "", "formulaid": "D"},
        {"conditiontype": "26", "operator": "1", "value": "so-whatsapp", "value2": "alerta", "formulaid": "E"},
    ],
}


def test_categorias_iguais_as_do_agente() -> None:
    assert [(c.chave, c.nome, c.severidade) for c in acronis_triggers.CATEGORIAS] == list(s15.CATEGORIAS)
    assert s15.HOST == acronis_triggers.HOST_ZABBIX
    assert s15.chave_item("cota") == acronis_triggers.chave_item("cota")


def test_expressao_e_nome_do_trigger() -> None:
    assert s15.expr_trigger("cota") == "length(last(/acronis-alertas-dashboard/acronis.alerta[cota]))>0"
    assert s15.nome_trigger("Agente offline") == "Acronis — Agente offline: {ITEM.LASTVALUE}"


def test_filtro_sem_chamado_soma_condicao_f() -> None:
    novo = s15.filtro_sem_chamado(_FILTRO_12)
    assert novo["formula"] == "(A and B and (C or D) and E) and F"
    assert novo["conditions"][-1] == {
        "conditiontype": "26",
        "operator": "1",
        "value": "so-dashboard",
        "value2": "alerta",
        "formulaid": "F",
    }
    assert "value" not in novo["conditions"][0]  # manutenção sem value


def test_filtro_sem_chamado_idempotente() -> None:
    novo = s15.filtro_sem_chamado(_FILTRO_12)
    assert s15.filtro_sem_chamado(novo) is None


def test_dry_run_nao_cria_nada() -> None:
    api = MagicMock()
    api.hostgroup.get.return_value = []
    api.host.get.return_value = []
    api.action.get.return_value = [{"actionid": "12", "filter": _FILTRO_12}]
    s15.ajustar_acao_chamado(api, dry=True)
    hostid = s15.garantir_host(api, s15.garantir_grupo(api, dry=True), dry=True)
    for chave, nome, sev in s15.CATEGORIAS:
        s15.garantir_categoria(api, hostid, chave, nome, sev, dry=True)
    api.action.update.assert_not_called()
    api.hostgroup.create.assert_not_called()
    api.host.create.assert_not_called()
    api.item.create.assert_not_called()
    api.trigger.create.assert_not_called()


def test_cria_item_e_trigger_com_tag_so_dashboard() -> None:
    api = MagicMock()
    api.item.get.return_value = []
    api.trigger.get.return_value = []
    s15.garantir_categoria(api, "999", "cota", "Sem licença (cota)", 3, dry=False)
    item = api.item.create.call_args.kwargs
    assert item["key_"] == "acronis.alerta[cota]" and item["type"] == 2 and item["value_type"] == 4
    trig = api.trigger.create.call_args.kwargs
    assert trig["priority"] == "3"
    assert {"tag": "alerta", "value": "so-dashboard"} in trig["tags"]


def test_ja_existe_nao_recria() -> None:
    api = MagicMock()
    api.item.get.return_value = [{"itemid": "1"}]
    api.trigger.get.return_value = [{"triggerid": "2"}]
    s15.garantir_categoria(api, "999", "cota", "Sem licença (cota)", 3, dry=False)
    api.item.create.assert_not_called()
    api.trigger.create.assert_not_called()


def test_remove_categoria_backup() -> None:
    assert "backup" in s15.REMOVIDAS
    assert all(chave != "backup" for chave, _, _ in s15.CATEGORIAS)
    api = MagicMock()
    api.item.get.return_value = [{"itemid": "77"}]
    s15.remover_categoria(api, "11225", "backup", dry=True)
    api.item.delete.assert_not_called()
    s15.remover_categoria(api, "11225", "backup", dry=False)
    api.item.delete.assert_called_once_with("77")
    api.item.get.return_value = []
    s15.remover_categoria(api, "11225", "backup", dry=False)
    assert api.item.delete.call_count == 1
