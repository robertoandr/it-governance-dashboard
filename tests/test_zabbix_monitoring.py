"""Testes de itgov/api/v1/zabbix_monitoring.py — KPIs do InfluxDB e problemas do Zabbix."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from itgov.api.v1 import zabbix_monitoring as zm

_DISP = {"_time": "t1", "uptime_pct": 99.5, "score": 88, "hosts_up": 40, "hosts_down": 2, "top_host_down": None}
_RISK = {"_time": "t2", "score": 70, "criticos": 1, "altos": 3, "total_problemas": 9, "top_incidente": "Link caiu"}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local/zabbix")
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    zm._cache_summary = None
    zm._cache_summary_ts = 0.0
    zm._cache_problems = None
    zm._cache_problems_ts = 0.0


def _resp(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_ler_influx_converte_campos_e_preenche_padroes() -> None:
    with patch.object(zm, "_query_influx", side_effect=[[{}, _DISP], [_RISK]]):
        dados = zm._ler_influx()

    assert dados["uptime_pct"] == 99.5
    assert dados["score_disp"] == 88.0
    assert dados["hosts_down"] == 2
    assert dados["hosts_unknown"] == 0
    assert dados["top_host_down"] == "—"
    assert dados["score_risco"] == 70.0
    assert dados["medios"] == 0
    assert dados["top_incidente"] == "Link caiu"
    assert (dados["_time_disp"], dados["_time_risk"]) == ("t1", "t2")


def test_ler_influx_sem_dados_zera_tudo() -> None:
    with patch.object(zm, "_query_influx", return_value=[]):
        dados = zm._ler_influx()
    assert dados["total_monitorado"] == 0
    assert dados["top_incidente"] == "—"
    assert dados["_time_disp"] is None


def test_zbx_exige_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "")
    with pytest.raises(RuntimeError, match="ZABBIX_URL"):
        zm._zbx("problem.get", {})


def test_zbx_monta_endpoint_e_retorna_result() -> None:
    with patch.object(zm.requests, "post", return_value=_resp({"result": []})) as post:
        assert zm._zbx("problem.get", {}) == []
    assert post.call_args.args[0] == "https://zbx.local/zabbix/api_jsonrpc.php"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_zbx_erro_da_api() -> None:
    with (
        patch.object(zm.requests, "post", return_value=_resp({"error": "boom"})),
        pytest.raises(RuntimeError, match="boom"),
    ):
        zm._zbx("problem.get", {})


def test_buscar_problemas_ordena_por_severidade_com_hosts() -> None:
    problemas = [
        {"eventid": "1", "objectid": "a", "name": "Aviso", "severity": "2", "clock": "1759200000", "acknowledged": "0"},
        {"eventid": "2", "objectid": "b", "name": "Desastre", "severity": "5", "clock": "0", "acknowledged": "1"},
    ]
    triggers = [{"triggerid": "a", "hosts": [{"name": "srv1"}, {"name": "srv2"}]}]
    with patch.object(zm, "_zbx", side_effect=[problemas, triggers]):
        resultado = zm._buscar_problemas()

    assert [p["eventid"] for p in resultado] == ["2", "1"]
    assert resultado[0]["severity_label"] == "disaster"
    assert resultado[0]["acknowledged"] is True
    assert resultado[0]["clock_fmt"] == "—"
    assert resultado[0]["hosts"] == []
    assert resultado[1]["hosts"] == ["srv1", "srv2"]


def test_buscar_problemas_vazio() -> None:
    with patch.object(zm, "_zbx", return_value=[]) as zbx:
        assert zm._buscar_problemas() == []
    assert zbx.call_count == 1


def test_summary_usa_cache_na_segunda_chamada() -> None:
    with patch.object(zm, "_ler_influx", return_value={"uptime_pct": 1.0}) as ler:
        primeiro = zm.get_cached_zabbix_summary()
        segundo = zm.get_cached_zabbix_summary()
    assert primeiro is segundo
    assert ler.call_count == 1


def test_problems_usa_cache_e_tolera_falha() -> None:
    with patch.object(zm, "_buscar_problemas", side_effect=requests.Timeout("lento")) as buscar:
        assert zm.get_cached_problems() == []
        assert zm.get_cached_problems() == []
    assert buscar.call_count == 1
