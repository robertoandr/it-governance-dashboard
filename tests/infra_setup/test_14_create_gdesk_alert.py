"""Testes do infra-setup/14_create_gdesk_alert.py (GDesk somado à ação 12 sem perder o Zendesk)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "infra-setup" / "14_create_gdesk_alert.py"
_spec = importlib.util.spec_from_file_location("gdesk_alert_script", _MODULE_PATH)
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)  # type: ignore[union-attr]

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
    r = gd._somar_midia([_ZENDESK_MIDIA], "120", "T.I.")

    assert r[0] == {k: _ZENDESK_MIDIA[k] for k in ("mediatypeid", "sendto", "active", "severity", "period")}
    assert r[1]["mediatypeid"] == "120"
    assert r[1]["sendto"] == "T.I."
    assert r[1]["severity"] == "63"


def test_midia_existente_e_substituida_sem_duplicar() -> None:
    antiga = {"mediatypeid": "120", "sendto": "velho", "active": "1"}

    r = gd._somar_midia([_ZENDESK_MIDIA, antiga], "120", "T.I.")

    gdesk = [m for m in r if m["mediatypeid"] == "120"]
    assert gdesk == [
        {"mediatypeid": "120", "sendto": "T.I.", "active": "0", "severity": "63", "period": "1-7,00:00-24:00"}
    ]


def test_operacao_somada_no_passo_2_mantendo_zendesk() -> None:
    r = gd._somar_operacao([_ZENDESK_OP], "120", "3")

    assert r is not None
    assert len(r) == 2
    zendesk, gdesk = r
    assert "operationid" not in zendesk and "actionid" not in zendesk
    assert "opmessage_grp" not in zendesk
    assert zendesk["opmessage"]["mediatypeid"] == "101"
    assert gdesk["opmessage"] == {"default_msg": "1", "mediatypeid": "120"}
    assert gdesk["esc_step_from"] == "2" and gdesk["esc_step_to"] == "2"
    assert gdesk["opmessage_usr"] == [{"userid": "3"}]


def test_operacao_nao_duplica_se_ja_existe() -> None:
    ja = dict(_ZENDESK_OP, opmessage={"default_msg": "1", "mediatypeid": "120"})
    assert gd._somar_operacao([ja], "120", "3") is None


def test_parametros_tem_macros_e_mapa_de_prioridade() -> None:
    params = {p["name"]: p["value"] for p in gd.parametros_webhook()}

    assert params["gdesk_url"] == "{$GDESK.URL}"
    assert params["gdesk_apikey"] == "{$GDESK.APIKEY}"
    assert params["queue_id"] == "{$GDESK.QUEUE_ID}"
    assert params["event_value"] == "{EVENT.VALUE}"
    assert params["event_update_status"] == "{EVENT.UPDATE.STATUS}"
    assert params["event_tags"] == "{EVENT.TAGS}"
    assert params["severity"] == "{EVENT.NSEVERITY}"
    # mapa severidade -> prioridade
    assert params["severity_disaster"] == "urgente"
    assert params["severity_high"] == "alta"
    assert params["severity_average"] == "normal"
    assert params["severity_warning"] == "normal"
    assert params["severity_information"] == "baixa"
    assert params["severity_not_classified"] == "baixa"


def test_webhook_usa_alerta_js_e_grava_tag() -> None:
    assert "/api/public/tickets" in gd.WEBHOOK_SCRIPT
    assert "gdesk_ticket" in gd.WEBHOOK_SCRIPT


def test_defaults_de_url_e_fila() -> None:
    # Sem env, cai nos padrões combinados (IP do vm-sistemas e fila T.I.).
    assert gd.GDESK_URL.endswith(":4000")
    assert gd.GDESK_QUEUE_ID == "2"
