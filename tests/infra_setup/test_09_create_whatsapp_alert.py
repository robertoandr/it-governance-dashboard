"""Tests for infra-setup/09_create_whatsapp_alert.py (mídia e operação somadas sem perder o Zendesk)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "09_create_whatsapp_alert.py"
_spec = importlib.util.spec_from_file_location("whatsapp_alert_script", _MODULE_PATH)
wa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wa)  # type: ignore[union-attr]

_ZENDESK_MIDIA = {
    "mediaid": "2",
    "mediatypeid": "101",
    "sendto": "zendesk",
    "active": "0",
    "severity": "63",
    "period": "1-7,00:00-24:00",
    "userdirectory_mediaid": "0",
    "provisioned": "0",
}

_ZENDESK_OP = {
    "operationid": "25",
    "actionid": "12",
    "operationtype": "0",
    "esc_period": "0",
    "esc_step_from": "2",
    "esc_step_to": "2",
    "evaltype": "0",
    "opconditions": [],
    "opmessage": {"default_msg": "1", "subject": "", "message": "", "mediatypeid": "101"},
    "opmessage_grp": [],
    "opmessage_usr": [{"userid": "3"}],
}


def test_midia_preserva_zendesk_e_remove_campos_somente_leitura() -> None:
    r = wa._somar_midia([_ZENDESK_MIDIA], "110", "123-456@g.us")

    assert r[0] == {k: _ZENDESK_MIDIA[k] for k in ("mediatypeid", "sendto", "active", "severity", "period")}
    assert r[1]["mediatypeid"] == "110"
    assert r[1]["sendto"] == "123-456@g.us"
    assert r[1]["severity"] == "63"


def test_midia_existente_e_substituida_sem_duplicar() -> None:
    antiga = {"mediatypeid": "110", "sendto": "grupo-velho@g.us", "active": "1"}

    r = wa._somar_midia([_ZENDESK_MIDIA, antiga], "110", "novo@g.us")

    whats = [m for m in r if m["mediatypeid"] == "110"]
    assert whats == [
        {"mediatypeid": "110", "sendto": "novo@g.us", "active": "0", "severity": "63", "period": "1-7,00:00-24:00"}
    ]


def test_operacao_somada_no_mesmo_passo_mantendo_zendesk() -> None:
    r = wa._somar_operacao([_ZENDESK_OP], "110", "3")

    assert r is not None
    assert len(r) == 2
    zendesk, whats = r
    assert "operationid" not in zendesk  # Zabbix 7.4 recusa na escrita
    assert "actionid" not in zendesk
    assert "opmessage_grp" not in zendesk  # lista vazia não é reenviada
    assert zendesk["opmessage"]["mediatypeid"] == "101"
    assert whats["opmessage"] == {"default_msg": "1", "mediatypeid": "110"}
    assert whats["opmessage_usr"] == [{"userid": "3"}]
    assert (whats["esc_step_from"], whats["esc_step_to"]) == ("2", "2")


def test_operacao_ja_existente_nao_altera_acao() -> None:
    ja = dict(_ZENDESK_OP, operationid="30", opmessage={"default_msg": "1", "mediatypeid": "110"})

    assert wa._somar_operacao([_ZENDESK_OP, ja], "110", "3") is None


def test_templates_cobrem_problema_resolucao_e_atualizacao() -> None:
    assert {t["recovery"] for t in wa.MESSAGE_TEMPLATES} == {"0", "1", "2"}
    assert all(t["eventsource"] == "0" for t in wa.MESSAGE_TEMPLATES)


def test_operacao_remove_ids_aninhados() -> None:
    op = dict(_ZENDESK_OP, opmessage_usr=[{"operationid": "25", "userid": "3"}])

    r = wa._somar_operacao([op], "110", "3")

    assert r is not None
    assert r[0]["opmessage_usr"] == [{"userid": "3"}]


def test_operacao_so_reenvia_assunto_com_mensagem_propria() -> None:
    propria = dict(_ZENDESK_OP, opmessage={"default_msg": "0", "subject": "S", "message": "M", "mediatypeid": "101"})

    r = wa._somar_operacao([_ZENDESK_OP, propria], "110", "3")

    assert r is not None
    assert r[0]["opmessage"] == {"default_msg": "1", "mediatypeid": "101"}
    assert r[1]["opmessage"]["subject"] == "S"
