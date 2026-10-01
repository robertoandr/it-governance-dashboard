"""Testes de itgov/api/v1/zabbix_triggers.py — problemas ativos, cache e ack."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from itgov.api.v1 import zabbix_triggers as zt

_PROBLEMS = [
    {
        "eventid": "10",
        "objectid": "t1",
        "name": "Disco cheio",
        "severity": "2",
        "clock": "1759200000",
        "acknowledged": "0",
    },
    {
        "eventid": "11",
        "objectid": "t2",
        "name": "Host fora",
        "severity": "5",
        "clock": "1759100000",
        "acknowledged": "1",
    },
    {"eventid": "12", "objectid": "t3", "name": "Sem trigger", "severity": "9", "clock": "0"},
]
_TRIGGERS = [
    {"triggerid": "t1", "hosts": [{"hostid": "1", "name": "srv-arquivos"}]},
    {"triggerid": "t2", "hosts": [{"hostid": "2", "name": "fw-borda"}]},
]


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local/zabbix")
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    monkeypatch.delenv("ZABBIX_FRONT_URL", raising=False)
    zt._cache_dados = None
    zt._cache_ts = 0.0


def _resp(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_zbx_url_acrescenta_endpoint_e_nao_inventa_url_vazia(monkeypatch: pytest.MonkeyPatch) -> None:
    assert zt._zbx_url() == "https://zbx.local/zabbix/api_jsonrpc.php"
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local/api_jsonrpc.php")
    assert zt._zbx_url() == "https://zbx.local/api_jsonrpc.php"
    monkeypatch.setenv("ZABBIX_URL", "")
    assert zt._zbx_url() == ""


def test_zbx_envia_bearer_e_retorna_result() -> None:
    with patch.object(zt.requests, "post", return_value=_resp({"result": [1]})) as post:
        assert zt._zbx("host.get", {}) == [1]
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert post.call_args.kwargs["json"]["method"] == "host.get"


def test_zbx_erro_da_api_vira_runtime_error() -> None:
    with (
        patch.object(zt.requests, "post", return_value=_resp({"error": {"code": -32602}})),
        pytest.raises(RuntimeError, match="Zabbix API"),
    ):
        zt._zbx("host.get", {})


def test_fetch_problems_enriquece_ordena_e_monta_link() -> None:
    with patch.object(zt, "_zbx", side_effect=[_PROBLEMS, _TRIGGERS]):
        result = zt._fetch_problems()

    assert [p["eventid"] for p in result] == ["12", "11", "10"]
    desastre = result[1]
    assert desastre["host"] == "fw-borda"
    assert desastre["severity_label"] == "Desastre"
    assert desastre["acknowledged"] is True
    assert desastre["zabbix_url"] == "https://zbx.local/zabbix/tr_events.php?triggerid=t2&eventid=11"
    sem_trigger = result[0]
    assert sem_trigger["host"] == "—"
    assert sem_trigger["severity_label"] == "Não classificado"
    assert sem_trigger["since"] == "—"
    assert sem_trigger["since_iso"] is None


def test_fetch_problems_vazio_nao_consulta_triggers() -> None:
    with patch.object(zt, "_zbx", return_value=[]) as zbx:
        assert zt._fetch_problems() == []
    assert zbx.call_count == 1


def test_build_event_url_prefere_front_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_FRONT_URL", "https://noc.local/")
    assert zt._build_event_url("7", "70") == "https://noc.local/tr_events.php?triggerid=70&eventid=7"


def test_get_cached_triggers_sem_configuracao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "")
    with patch.object(zt, "_fetch_problems") as fetch:
        result = zt.get_cached_triggers()
    fetch.assert_not_called()
    assert result == {"enabled": False, "reason": "zabbix_not_configured", "problems": [], "counts": {}}


def test_get_cached_triggers_falha_na_api_nao_quebra() -> None:
    with patch.object(zt, "_fetch_problems", side_effect=requests.ConnectionError("recusado")):
        result = zt.get_cached_triggers()
    assert result["enabled"] is False
    assert "recusado" in result["reason"]


def test_get_cached_triggers_conta_por_severidade_e_usa_cache() -> None:
    with patch.object(zt, "_zbx", side_effect=[_PROBLEMS, _TRIGGERS]):
        result = zt.get_cached_triggers()
    assert result["enabled"] is True
    assert result["total"] == 3
    assert result["counts"]["Desastre"] == 1
    assert result["counts"]["Aviso"] == 1
    assert result["counts"]["Alto"] == 0

    with patch.object(zt, "_fetch_problems") as fetch:
        assert zt.get_cached_triggers() is result
    fetch.assert_not_called()


def test_ack_problem_sucesso_invalida_cache() -> None:
    zt._cache_dados = {"enabled": True}
    with patch.object(zt, "_zbx", return_value={"eventids": ["10"]}) as zbx:
        assert zt.ack_problem("10") is True
    params = zbx.call_args.args[1]
    assert params["eventids"] == ["10"]
    assert params["message"] == "Acknowledged via IT Gov Dashboard"
    assert zt._cache_dados is None


def test_ack_problem_falha_retorna_false() -> None:
    zt._cache_dados = {"enabled": True}
    with patch.object(zt, "_zbx", side_effect=RuntimeError("sem permissão")):
        assert zt.ack_problem("10", "olhando") is False
    assert zt._cache_dados == {"enabled": True}
