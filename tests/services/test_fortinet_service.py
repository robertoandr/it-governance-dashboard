"""Testes do serviço de monitoramento FortiGate (itgov.services.fortinet_service).

Cobre a resolução de FortiGates por template (em vez de grupo único), a montagem
de métricas de sistema, o parsing de SD-WAN, o mapeamento de problemas e o cache.
As chamadas à API do Zabbix são mockadas via ``_zbx``.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from itgov.services import fortinet_service as fs


@pytest.fixture(autouse=True)
def _limpar_cache():
    """Zera o cache do módulo antes e depois de cada teste."""
    fs._cache_data = None
    fs._cache_ts = 0.0
    yield
    fs._cache_data = None
    fs._cache_ts = 0.0


def _agora() -> str:
    return str(int(time.time()))


def _fake_zbx_factory(
    *,
    templates: list[dict] | None = None,
    hosts: list[dict] | None = None,
    system_items: list[dict] | None = None,
    sdwan_items: list[dict] | None = None,
    alias_items: list[dict] | None = None,
    problems: list[dict] | None = None,
    triggers: list[dict] | None = None,
):
    """Cria um substituto de ``_zbx`` que despacha pela method + params."""

    templates = (
        templates
        if templates is not None
        else [
            {"templateid": "10603", "name": "FortiGate by HTTP"},
            {"templateid": "10604", "name": "FortiGate by SNMP"},
            {"templateid": "10001", "name": "Linux by Zabbix agent"},
        ]
    )

    def _fake(method: str, params: dict):
        if method == "template.get":
            return templates
        if method == "host.get":
            return hosts or []
        if method == "item.get":
            if "filter" in params and "key_" in params.get("filter", {}):
                return system_items or []
            search = params.get("search", {})
            if "name" in search:
                return sdwan_items or []
            if "key_" in search:
                return alias_items or []
            return []
        if method == "problem.get":
            return problems or []
        if method == "trigger.get":
            return triggers or []
        raise AssertionError(f"método inesperado: {method}")

    return _fake


# --------------------------------------------------------------------------- #
# _resolver_template_ids
# --------------------------------------------------------------------------- #
def test_resolver_template_ids_filtra_por_nome():
    fake = _fake_zbx_factory()
    with patch.object(fs, "_zbx", side_effect=fake):
        ids = fs._resolver_template_ids()
    assert ids == ["10603", "10604"]


def test_resolver_template_ids_vazio_quando_sem_match():
    fake = _fake_zbx_factory(templates=[{"templateid": "1", "name": "Linux by Zabbix agent"}])
    with patch.object(fs, "_zbx", side_effect=fake):
        assert fs._resolver_template_ids() == []


# --------------------------------------------------------------------------- #
# _load_iface_labels
# --------------------------------------------------------------------------- #
def test_load_iface_labels_parseia_env(monkeypatch):
    monkeypatch.setenv("FORTINET_IFACE_LABELS", "wan1=Algar, b = Starlink ,internal1=Vivo")
    labels = fs._load_iface_labels()
    assert labels == {"wan1": "Algar", "b": "Starlink", "internal1": "Vivo"}


def test_load_iface_labels_vazio(monkeypatch):
    monkeypatch.delenv("FORTINET_IFACE_LABELS", raising=False)
    assert fs._load_iface_labels() == {}


# --------------------------------------------------------------------------- #
# _buscar_fortinets — caminhos de borda
# --------------------------------------------------------------------------- #
def test_buscar_fortinets_sem_template_retorna_vazio():
    fake = _fake_zbx_factory(templates=[])
    with patch.object(fs, "_zbx", side_effect=fake):
        assert fs._buscar_fortinets() == []


def test_buscar_fortinets_ignora_host_sem_template_fortigate():
    hosts = [
        {
            "hostid": "1",
            "host": "FGT-Sede",
            "name": "FortiGate Sede",
            "parentTemplates": [{"name": "FortiGate by HTTP"}],
        },
        {
            "hostid": "2",
            "host": "srv-linux",
            "name": "Servidor Linux",
            "parentTemplates": [{"name": "Linux by Zabbix agent"}],
        },
    ]
    fake = _fake_zbx_factory(hosts=hosts)
    with patch.object(fs, "_zbx", side_effect=fake):
        resultado = fs._buscar_fortinets()
    assert [r["hostid"] for r in resultado] == ["1"]


def test_buscar_fortinets_todos_hosts_sem_match_retorna_vazio():
    hosts = [{"hostid": "9", "host": "x", "name": "x", "parentTemplates": [{"name": "Linux"}]}]
    fake = _fake_zbx_factory(hosts=hosts)
    with patch.object(fs, "_zbx", side_effect=fake):
        assert fs._buscar_fortinets() == []


# --------------------------------------------------------------------------- #
# _buscar_fortinets — caminho feliz com métricas + SD-WAN
# --------------------------------------------------------------------------- #
def test_buscar_fortinets_monta_metricas_e_sdwan():
    agora = _agora()
    hosts = [
        {
            "hostid": "10",
            "host": "FGT-Centro",
            "name": "FortiGate Centro",
            "parentTemplates": [{"name": "FortiGate by HTTP"}],
        }
    ]
    system_items = [
        {"hostid": "10", "key_": "fgate.api.status", "name": "API", "lastvalue": "1", "lastclock": agora},
        {"hostid": "10", "key_": "fgate.cpu.util", "name": "CPU", "lastvalue": "12.4", "lastclock": agora},
        {"hostid": "10", "key_": "fgate.memory.util", "name": "Mem", "lastvalue": "40", "lastclock": agora},
        {"hostid": "10", "key_": "fgate.uptime", "name": "Uptime", "lastvalue": "7200", "lastclock": agora},
        {"hostid": "10", "key_": "fgate.name", "name": "Nome", "lastvalue": "FGT80F-Primary", "lastclock": agora},
    ]
    sdwan_items = [
        {
            "hostid": "10",
            "key_": "k1",
            "name": "SD-WAN [SLA_Padrao]:[wan1]: Latency",
            "lastvalue": "15.2",
            "lastclock": agora,
        },
        {
            "hostid": "10",
            "key_": "k2",
            "name": "SD-WAN [SLA_Padrao]:[wan1]: Packet loss",
            "lastvalue": "0",
            "lastclock": agora,
        },
        {
            "hostid": "10",
            "key_": "k3",
            "name": "SD-WAN [SLA_Padrao]:[wan1]: Interface status",
            "lastvalue": "0",
            "lastclock": agora,
        },
    ]
    alias_items = [
        {"hostid": "10", "key_": "fgate.netif.get_data[wan1]", "name": "Interface [wan1(ALGAR-EMPRESA)]: Get data"},
    ]
    fake = _fake_zbx_factory(hosts=hosts, system_items=system_items, sdwan_items=sdwan_items, alias_items=alias_items)
    with patch.object(fs, "_zbx", side_effect=fake):
        resultado = fs._buscar_fortinets()

    assert len(resultado) == 1
    fw = resultado[0]
    assert fw["hostid"] == "10"
    assert fw["host"] == "FGT-Centro"
    assert fw["name"] == "FGT80F-Primary"
    assert fw["api_up"] is True
    assert fw["cpu_pct"] == 12.4
    assert fw["mem_pct"] == 40.0
    assert fw["uptime_h"] == 2
    assert fw["has_data"] is True
    assert fw["problem_count"] == 0

    assert len(fw["sdwan"]) == 1
    grupo = fw["sdwan"][0]
    assert grupo["sla"] == "SLA_Padrao"
    membro = grupo["members"][0]
    assert membro["iface"] == "wan1"
    assert membro["label"] == "ALGAR-EMPRESA"  # alias do Zabbix
    assert membro["latency_ms"] == 15.2
    assert membro["status"] == "up"


def test_buscar_fortinets_env_label_sobrescreve_alias(monkeypatch):
    agora = _agora()
    monkeypatch.setenv("FORTINET_IFACE_LABELS", "wan1=Link Primario")
    hosts = [{"hostid": "10", "host": "FGT", "name": "FGT", "parentTemplates": [{"name": "FortiGate by HTTP"}]}]
    sdwan_items = [
        {"hostid": "10", "key_": "k", "name": "SD-WAN [S]:[wan1]: Latency", "lastvalue": "1", "lastclock": agora},
    ]
    alias_items = [
        {"hostid": "10", "key_": "fgate.netif.get_data[wan1]", "name": "Interface [wan1(ZBX-ALIAS)]: Get data"},
    ]
    fake = _fake_zbx_factory(hosts=hosts, sdwan_items=sdwan_items, alias_items=alias_items)
    with patch.object(fs, "_zbx", side_effect=fake):
        resultado = fs._buscar_fortinets()
    assert resultado[0]["sdwan"][0]["members"][0]["label"] == "Link Primario"


def test_buscar_fortinets_dados_stale_sao_ignorados():
    velho = str(int(time.time()) - 5000)  # > 600s
    hosts = [{"hostid": "10", "host": "FGT", "name": "FortiGate", "parentTemplates": [{"name": "FortiGate by HTTP"}]}]
    system_items = [
        {"hostid": "10", "key_": "fgate.cpu.util", "name": "CPU", "lastvalue": "99", "lastclock": velho},
        {"hostid": "10", "key_": "fgate.api.status", "name": "API", "lastvalue": "1", "lastclock": velho},
    ]
    sdwan_items = [
        {"hostid": "10", "key_": "k", "name": "SD-WAN [S]:[wan1]: Latency", "lastvalue": "5", "lastclock": velho},
    ]
    fake = _fake_zbx_factory(hosts=hosts, system_items=system_items, sdwan_items=sdwan_items)
    with patch.object(fs, "_zbx", side_effect=fake):
        fw = fs._buscar_fortinets()[0]
    assert fw["cpu_pct"] is None
    assert fw["api_up"] is None
    assert fw["sdwan"] == []
    assert fw["has_data"] is False
    assert fw["name"] == "FortiGate"  # cai no h["name"]


# --------------------------------------------------------------------------- #
# _buscar_fortinets — problemas
# --------------------------------------------------------------------------- #
def test_buscar_fortinets_mapeia_problemas_por_host():
    agora = _agora()
    hosts = [{"hostid": "10", "host": "FGT", "name": "FortiGate", "parentTemplates": [{"name": "FortiGate by HTTP"}]}]
    problems = [
        {
            "eventid": "500",
            "name": "FortiGate: Port 4444 is unavailable",
            "severity": "3",
            "clock": agora,
            "acknowledged": "0",
            "objectid": "900",
        }
    ]
    triggers = [{"triggerid": "900", "hosts": [{"hostid": "10"}]}]
    fake = _fake_zbx_factory(hosts=hosts, problems=problems, triggers=triggers)
    with patch.object(fs, "_zbx", side_effect=fake):
        fw = fs._buscar_fortinets()[0]

    assert fw["problem_count"] == 1
    prob = fw["problems"][0]
    assert prob["name"] == "FortiGate: Port 4444 is unavailable"
    assert prob["severity"] == 3
    assert prob["severity_label"] == "average"
    assert prob["severity_color"] == "orange"
    assert prob["acknowledged"] is False


# --------------------------------------------------------------------------- #
# get_cached_fortinet
# --------------------------------------------------------------------------- #
def test_get_cached_fortinet_usa_cache():
    with patch.object(fs, "_buscar_fortinets", return_value=[{"host": "x"}]) as mock_busca:
        primeiro = fs.get_cached_fortinet()
        segundo = fs.get_cached_fortinet()
    assert primeiro == segundo == [{"host": "x"}]
    mock_busca.assert_called_once()


def test_get_cached_fortinet_erro_retorna_lista_vazia():
    with patch.object(fs, "_buscar_fortinets", side_effect=RuntimeError("Zabbix down")):
        assert fs.get_cached_fortinet() == []


def test_get_cached_fortinet_refaz_apos_expirar_ttl():
    with patch.object(fs, "_buscar_fortinets", return_value=[]) as mock_busca:
        fs.get_cached_fortinet()
        fs._cache_ts = time.monotonic() - (fs._CACHE_TTL + 1)
        fs.get_cached_fortinet()
    assert mock_busca.call_count == 2
