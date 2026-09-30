"""Testes de itgov/api/v1/infra_monitoring.py — hosts não-CFTV do Zabbix, KPIs do InfluxDB e cache.

A temperatura do datacenter tem arquivo próprio (test_infra_monitoring_datacenter_temp.py).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from itgov.api.v1 import infra_monitoring as im

_GRUPOS = [
    {"groupid": "1", "name": "Linux servers"},
    {"groupid": "2", "name": "CFTV/Shopping"},
    {"groupid": "3", "name": "Switch Core"},
]
_HOSTS = [
    {
        "hostid": "10",
        "host": "srv-b",
        "name": "Servidor B",
        "maintenance_status": "0",
        "groups": [{"name": "Linux servers"}],
        "interfaces": [{"ip": "10.0.0.2"}],
        "items": [{"key_": "icmpping", "lastvalue": "0", "lastclock": "1759200000"}],
    },
    {
        "hostid": "11",
        "host": "srv-a",
        "name": "Servidor A",
        "maintenance_status": "0",
        "groups": [{"name": "Grupo X"}, {"name": "Switch Core"}],
        "interfaces": [],
        "items": [{"key_": "icmpping", "lastvalue": "1", "lastclock": "1759200000"}],
    },
    {
        "hostid": "12",
        "host": "srv-manut",
        "name": "Em manutenção",
        "maintenance_status": "1",
        "groups": [{"name": "Linux servers"}],
        "interfaces": [{"ip": "10.0.0.3"}],
        "items": [{"key_": "icmpping", "lastvalue": "0", "lastclock": "1759200000"}],
    },
    {
        "hostid": "13",
        "host": "sem-ping",
        "name": "Sem ping",
        "groups": [{"name": "Grupo Qualquer"}],
        "items": [],
    },
]
_PROBLEMAS = [
    {"eventid": "1", "objectid": "t1", "name": "Host fora", "severity": "5", "clock": "1759200000"},
    {"eventid": "2", "objectid": "t1", "name": "Ping alto", "severity": "2", "clock": "0", "acknowledged": "1"},
]
_TRIGGERS = [{"triggerid": "t1", "hosts": [{"hostid": "10", "name": "Servidor B"}]}]


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local")
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    im._cache_data = None
    im._cache_ts = 0.0


def _resp(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_zbx(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch.object(im.requests, "post", return_value=_resp({"result": "ok"})) as post:
        assert im._zbx("host.get", {}) == "ok"
    assert post.call_args.args[0] == "https://zbx.local/api_jsonrpc.php"

    with (
        patch.object(im.requests, "post", return_value=_resp({"error": "negado"})),
        pytest.raises(RuntimeError, match="negado"),
    ):
        im._zbx("host.get", {})

    monkeypatch.setenv("ZABBIX_URL", "")
    with pytest.raises(RuntimeError, match="ZABBIX_URL"):
        im._zbx("host.get", {})


def test_ler_summary_influx() -> None:
    soma = {"hosts_total": 50, "problems_disaster": 1, "problems_total": None}
    disp = {"uptime_pct": 98.7, "hosts_up": 45}
    with patch.object(im, "_query_influx", side_effect=[[soma], [disp]]):
        r = im._ler_summary_influx()
    assert r["hosts_total"] == 50
    assert r["problems_disaster"] == 1
    assert r["problems_total"] == 0
    assert r["uptime_pct"] == 98.7
    assert r["hosts_up"] == 45

    with patch.object(im, "_query_influx", return_value=[]):
        assert im._ler_summary_influx()["hosts_total"] == 0


@pytest.mark.parametrize(
    ("grupo", "categoria"),
    [
        ("Linux servers", "Servidores"),
        ("Servidores Filiais", "Servidores"),
        ("meu servidor", "Servidores"),
        ("Rede Loja 1", "Firewall / Rede"),
        ("Switch Core", "Firewall / Rede"),
        ("Lojas Centro", "Lojas"),
        ("Qualquer", "Outros"),
    ],
)
def test_categoria(grupo: str, categoria: str) -> None:
    assert im._categoria(grupo) == categoria


def test_buscar_hosts_infra_classifica_hosts_e_problemas() -> None:
    with patch.object(im, "_zbx", side_effect=[_GRUPOS, _HOSTS, _PROBLEMAS, _TRIGGERS]) as zbx:
        r = im._buscar_hosts_infra()

    assert zbx.call_args_list[1].args[1]["groupids"] == ["1", "3"]
    assert r["by_category"]["Servidores"] == {"total": 2, "up": 0, "down": 2, "nodata": 0, "maint": 1}
    assert r["by_category"]["Firewall / Rede"]["up"] == 1
    assert r["by_category"]["Outros"]["nodata"] == 1
    assert r["down_list"] == [
        {"host": "srv-b", "name": "Servidor B", "ip": "10.0.0.2", "category": "Servidores", "problems": 2}
    ]
    assert [p["severity_label"] for p in r["problems"]] == ["disaster", "warning"]
    assert r["problems"][1]["clock_fmt"] == "—"
    assert r["problems"][1]["acknowledged"] is True
    assert r["total_problems"] == 2


def test_buscar_hosts_infra_sem_grupos_sem_hosts_sem_problemas() -> None:
    vazio = {"hosts": [], "by_category": {}, "down_list": [], "problems": [], "total_problems": 0}
    with patch.object(im, "_zbx", return_value=[{"groupid": "2", "name": "CFTV/X"}]):
        assert im._buscar_hosts_infra() == vazio
    with patch.object(im, "_zbx", side_effect=[_GRUPOS, []]):
        assert im._buscar_hosts_infra() == vazio
    with patch.object(im, "_zbx", side_effect=[_GRUPOS, _HOSTS[:1], []]):
        r = im._buscar_hosts_infra()
    assert r["down_list"][0]["problems"] == 0
    assert r["problems"] == []


def test_buscar_infra_consolida() -> None:
    zabbix = {
        "hosts": [{}, {}, {}, {}],
        "by_category": {
            "Servidores": {"total": 3, "up": 2, "down": 1, "nodata": 0, "maint": 1},
            "Outros": {"total": 1, "up": 0, "down": 0, "nodata": 1, "maint": 0},
        },
        "down_list": [{"host": "x"}],
        "problems": [],
        "total_problems": 0,
    }
    with (
        patch.object(im, "_ler_summary_influx", return_value={"hosts_total": 4}),
        patch.object(im, "_buscar_hosts_infra", return_value=zabbix),
        patch.object(im, "_ler_datacenter_temp", return_value={"disponivel": False}),
    ):
        r = im._buscar_infra()
    assert (r["total"], r["up"], r["down"], r["nodata"], r["maint"]) == (4, 2, 1, 1, 1)
    assert r["up_pct"] == 50.0
    assert r["influx"] == {"hosts_total": 4}


def test_get_cached_infra_summary_cache_e_falha() -> None:
    with patch.object(im, "_buscar_infra", return_value={"enabled": True}) as buscar:
        primeiro = im.get_cached_infra_summary()
        assert im.get_cached_infra_summary() is primeiro
    assert buscar.call_count == 1

    im._cache_data = None
    with patch.object(im, "_buscar_infra", side_effect=RuntimeError("zabbix fora")):
        r = im.get_cached_infra_summary()
    assert r["enabled"] is False
    assert r["_erro"] == "zabbix fora"
    assert r["datacenter_temp"]["disponivel"] is False
