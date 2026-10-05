"""FortiGates pela REST API: configuração, SD-WAN, WANs, clientes DHCP/ARP e página Links WAN."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from itgov.services import fortigate_api as fa


@pytest.fixture
def env_fortigates(monkeypatch: pytest.MonkeyPatch) -> None:
    for chave in list(__import__("os").environ):
        if chave.startswith("FORTIGATE_"):
            monkeypatch.delenv(chave)
    monkeypatch.setenv("FORTIGATE_SEDE_URL", "https://172.29.3.254:5443/")
    monkeypatch.setenv("FORTIGATE_SEDE_TOKEN", "fake-token-sede")
    monkeypatch.setenv("FORTIGATE_TRIUNFO_URL", "https://172.17.253.254:4444")
    monkeypatch.setenv("FORTIGATE_TRIUNFO_TOKEN", "")


def test_configurados_separa_prontos_e_pendentes(env_fortigates) -> None:
    prontos, pendentes = fa.configurados()
    assert prontos == [{"nome": "Sede", "url": "https://172.29.3.254:5443", "token": "fake-token-sede", "sha256": ""}]
    assert pendentes == ["Triunfo"]


def test_montar_sdwan_ignora_quem_nao_participa_do_sla() -> None:
    health = {
        "Ping_Triunfo": {
            "wan1": {"status": "error"},
            "GDS-SHP-TRF2": {"status": "up", "latency": 5.11, "jitter": 0.69, "packet_loss": 0.0},
            "GDS-SHP-TRF": {"status": "up", "latency": 120.0, "jitter": 3.0, "packet_loss": 0.0},
            "GDS-SHP-OPS": {"status": "down"},
        },
        "Default_DNS": {"wan1": {"status": "error"}},
    }
    (sla,) = fa.montar_sdwan(health, {"GDS-SHP-TRF2": "Triunfo 2"})
    membros = {m["iface"]: m for m in sla["members"]}
    assert sla["sla"] == "Ping_Triunfo" and set(membros) == {"GDS-SHP-TRF2", "GDS-SHP-TRF", "GDS-SHP-OPS"}
    assert membros["GDS-SHP-TRF2"] == {
        "iface": "GDS-SHP-TRF2", "label": "Triunfo 2", "latency_ms": 5.1, "jitter_ms": 0.7, "loss_pct": 0.0,
        "status": "up",
    }  # fmt: skip
    assert membros["GDS-SHP-TRF"]["status"] == "warn" and membros["GDS-SHP-OPS"]["status"] == "down"


@pytest.mark.parametrize(
    ("lat", "perda", "caiu", "esperado"),
    [(5, 0, False, "up"), (150, 0, False, "warn"), (10, 2, False, "warn"), (400, 0, False, "degraded"),
     (10, 10, False, "degraded"), (None, None, True, "down")],
)  # fmt: skip
def test_status_link(lat, perda, caiu: bool, esperado: str) -> None:
    assert fa.status_link(lat, perda, caiu) == esperado


def test_montar_wans() -> None:
    interfaces = {
        "wan1": {"alias": "(ALGAR)", "link": True, "speed": 1000.0, "rx_bytes": 694_413_153_720, "tx_bytes": 1e9,
                 "rx_errors": 2, "tx_errors": 1},
        "wan2": {"link": False, "speed": 0.0},
    }  # fmt: skip
    wans = [
        {"name": "wan1", "alias": "ALGAR", "ip": "189.112.203.34 255.255.255.252"},
        {"name": "wan2", "alias": "STARLINK", "ip": "0.0.0.0 0.0.0.0"},
    ]
    algar, starlink = fa.montar_wans(interfaces, wans)
    assert (algar["operadora"], algar["ip"], algar["link"], algar["rx_gb"], algar["erros"]) == (
        "ALGAR", "189.112.203.34", True, 694.4, 3,
    )  # fmt: skip
    assert (starlink["ip"], starlink["link"], starlink["speed_mbps"]) == ("", False, 0)


def test_montar_clientes_junta_dhcp_e_arp() -> None:
    dhcp = [
        {"ip": "10.41.100.5", "mac": "aa:bb:cc:00:00:01", "hostname": "DESKTOP-ABC", "vci": "MSFT 5.0",
         "interface": "internal1", "status": "leased"},
        {"ip": "10.41.100.6", "mac": "aa:bb:cc:00:00:02", "hostname": "", "vci": "", "status": "expired"},
    ]  # fmt: skip
    arp = [{"ip": "10.41.100.5", "mac": "aa:bb:cc:00:00:01"}, {"ip": "10.41.1.20", "mac": "11:22:33:44:55:66"}]
    clientes = {c["ip"]: c for c in fa.montar_clientes("Shopping", dhcp, arp)}
    assert set(clientes) == {"10.41.100.5", "10.41.1.20"}  # concessão expirada fica de fora
    assert clientes["10.41.100.5"]["hostname"] == "DESKTOP-ABC" and clientes["10.41.100.5"]["fonte"] == "dhcp"
    assert clientes["10.41.1.20"] == {
        "ip": "10.41.1.20", "mac": "11:22:33:44:55:66", "hostname": "", "vci": "", "interface": "", "fonte": "arp",
        "fortigate": "Shopping",
    }  # fmt: skip


_RESPOSTAS = {
    "monitor/system/status": {"hostname": "FGT60F-Gadens-SHP", "model": "FGT60F"},
    "monitor/system/performance/status": {"cpu": {"idle": 93}, "mem": {"total": 2000, "used": 1000}},
    "cmdb/system/interface?format=name|alias|role|ip|vlanid|interface|type|status": [
        {"name": "wan1", "alias": "ALGAR", "role": "wan", "ip": "189.112.203.34 255.255.255.252"},
        {
            "name": "VLAN10",
            "alias": "Rede-Corporativa",
            "role": "lan",
            "type": "vlan",
            "ip": "172.29.3.254 255.255.252.0",
            "vlanid": 10,
            "interface": "Switch",
            "status": "up",
        },
    ],
    "monitor/system/interface": {"wan1": {"link": True, "speed": 1000}},
    "monitor/virtual-wan/health-check": {"Ping_Externo": {"wan1": {"status": "up", "latency": 3.0}}},
}


def test_ler_fortigate() -> None:
    fw = {"nome": "Shopping", "url": "https://10.41.1.1", "token": "fake-token"}
    respostas = {**_RESPOSTAS, "monitor/vpn/ipsec": [{"name": "GDS-STS-MTZ2", "rgwy": "45.166.249.159",
                                                       "proxyid": [{"status": "up"}], "incoming_bytes": 2e9}]}  # fmt: skip
    with patch.object(fa, "_get", side_effect=lambda _fw, caminho: respostas[caminho]):
        r = fa._ler_fortigate(fw)
    assert (r["name"], r["modelo"], r["host"], r["origem"]) == ("FGT60F-Gadens-SHP", "FGT60F", "10.41.1.1", "api")
    assert r["ipsec"] == [
        {"nome": "GDS-STS-MTZ2", "remoto": "45.166.249.159", "status": "up", "rx_gb": 2.0, "tx_gb": 0.0}
    ]
    assert (r["cpu_pct"], r["mem_pct"], r["api_up"], r["has_data"]) == (7.0, 50.0, True, True)
    assert r["sdwan"][0]["members"][0]["label"] == "ALGAR" and r["wans"][0]["operadora"] == "ALGAR"
    assert [w["iface"] for w in r["wans"]] == ["wan1"] and r["redes"][0]["cidr"] == "172.29.0.0/22"


def test_montar_redes_ignora_wan_tunel_e_sistema() -> None:
    cfg = [
        {"name": "wan1", "role": "wan", "ip": "189.112.203.34 255.255.255.252"},
        {"name": "GDS-SHP-TRF2", "type": "tunnel", "ip": "10.0.0.1 255.255.255.255"},
        {"name": "nac_segment", "type": "vlan", "ip": "10.255.13.1 255.255.255.0"},
        {"name": "a", "type": "physical", "ip": "0.0.0.0 0.0.0.0"},
        {"name": "VLAN110", "alias": "Rede-CFTV", "role": "lan", "type": "vlan", "ip": "172.29.11.254 255.255.255.0",
         "vlanid": 110, "interface": "Switch", "status": "up"},
        {"name": "Rede-Interna", "role": "lan", "type": "switch", "ip": "10.41.1.1 255.0.0.0", "status": "up"},
    ]  # fmt: skip
    redes = fa.montar_redes(cfg)
    assert [r["cidr"] for r in redes] == ["10.0.0.0/8", "172.29.11.0/24"]
    assert redes[1] == {"iface": "VLAN110", "alias": "Rede-CFTV", "cidr": "172.29.11.0/24", "gateway": "172.29.11.254",
                        "vlan": 110, "pai": "Switch", "ativa": True}  # fmt: skip


def test_montar_ipsec_ignora_antigos_e_le_fase_2() -> None:
    tuneis = fa.montar_ipsec(
        [
            {"name": "TRF-GDS-MTZ2", "proxyid": [{"status": "down"}]},
            {"name": "GDS-STS-MTZ-OLD", "proxyid": [{"status": "down"}]},
            {"name": "TRF-GDS-MTZ", "rgwy": "189.112.100.49", "proxyid": [{"status": "down"}, {"status": "up"}]},
        ]
    )
    assert [(t["nome"], t["status"]) for t in tuneis] == [("TRF-GDS-MTZ", "up"), ("TRF-GDS-MTZ2", "down")]


def test_ipsec_sem_permissao_nao_derruba_a_leitura() -> None:
    fw = {"nome": "Shopping", "url": "https://10.41.1.1", "token": "fake-token"}

    def _get(_fw: dict, caminho: str) -> object:
        if caminho == "monitor/vpn/ipsec":
            raise ValueError("403")
        return _RESPOSTAS[caminho]

    with patch.object(fa, "_get", side_effect=_get):
        r = fa._ler_fortigate(fw)
    assert r["api_up"] and r["ipsec"] == []


def test_ler_fortigate_com_falha_mostra_erro() -> None:
    fw = {"nome": "Shopping", "url": "https://10.41.1.1", "token": "fake-token"}
    with patch.object(fa, "_get", side_effect=requests.ConnectionError("sem rota")):
        r = fa._ler_fortigate(fw)
    assert not r["has_data"] and not r["api_up"] and "sem rota" in r["erro"]


def test_get_recusa_status_diferente_de_success() -> None:
    fw = {"nome": "Sede", "url": "https://fw.test", "token": "fake-token"}
    resp = requests.Response()
    resp.status_code = 200
    resp._content = b'{"status": "error", "http_status": 403}'
    with patch.object(fa.requests.Session, "get", return_value=resp) as get, pytest.raises(ValueError):
        fa._get(fw, "monitor/system/status")
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer fake-token"}


def test_impressao_digital_fixa_o_certificado(monkeypatch: pytest.MonkeyPatch, env_fortigates) -> None:
    monkeypatch.setenv("FORTIGATE_SEDE_SHA256", "1F:90:A2:88")
    (fw,), _ = fa.configurados()
    assert fw["sha256"] == "1F:90:A2:88"
    sessao = fa._sessao(fw)
    adaptador = sessao.get_adapter("https://172.29.3.254:5443")
    assert isinstance(adaptador, fa._CertificadoFixo)
    assert adaptador.poolmanager.connection_pool_kw["assert_fingerprint"] == "1f90a288"
    assert not isinstance(fa._sessao({**fw, "sha256": ""}).get_adapter("https://x"), fa._CertificadoFixo)


def test_sem_fortigate_configurado_nao_busca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fa, "configurados", lambda: ([], []))
    assert fa.get_cached_clientes() == []
    assert fa._buscar_fortigates() == []


def test_pagina_links_mostra_fortigate_da_api_e_pendentes(authed_client) -> None:
    fw = {
        "hostid": "api:Shopping", "host": "10.41.1.1", "name": "FGT60F-Gadens-SHP", "unidade": "Shopping",
        "origem": "api", "has_data": True, "api_up": True, "cpu_pct": 7.0, "mem_pct": 50.0, "uptime_h": None,
        "sdwan": [{"sla": "Ping_Triunfo", "members": [{"iface": "GDS-SHP-TRF2", "label": "GDS-SHP-TRF2",
                   "latency_ms": 5.1, "jitter_ms": 0.7, "loss_pct": 0.0, "status": "up"}]}],
        "wans": [{"iface": "wan1", "operadora": "ALGAR", "ip": "189.112.203.34", "link": True, "speed_mbps": 1000,
                  "rx_gb": 694.4, "tx_gb": 190.6, "erros": 0}],
        "problems": [], "problem_count": 0, "modelo": "FGT60F", "erro": "",
    }  # fmt: skip
    vazio = {"links": [], "total": 0, "up": 0, "down": 0, "degraded": 0, "nodata": 0, "all_ok": False,
             "has_zabbix": True}  # fmt: skip
    with (
        patch("itgov.api.v1.links_manager.get_cached_links", return_value=vazio),
        patch("itgov.services.fortinet_service.get_cached_fortinet", return_value=[]),
        patch.object(fa, "get_cached_fortigates", return_value=[fw]),
        patch.object(fa, "configurados", return_value=([], ["Triunfo"])),
    ):
        html = authed_client.get("/gov/links").get_data(as_text=True)
    for trecho in ("FGT60F-Gadens-SHP", "FGT60F", "ALGAR", "189.112.203.34", "1000 Mbps", "Ping_Triunfo",
                   "Token de API pendente: Triunfo"):  # fmt: skip
        assert trecho in html, trecho
