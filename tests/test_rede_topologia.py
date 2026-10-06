"""Topologia lógica da rede: destinos de SLA, redes por FortiGate, túneis, mapa e página."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from itgov.api.v1 import rede_monitoring
from itgov.api.v1.rede_topologia import destino_do_ipsec, destino_do_sla, layout_geral, montar_topologia
from itgov.services import fortigate_api

_NOMES = ["Sede", "Shopping", "Triunfo"]


@pytest.mark.parametrize(
    ("sla", "destino"),
    [
        ("Ping_Triunfo", "Triunfo"),
        ("Ping_Matriz", "Sede"),  # apelido
        ("Ping_Shopping", "Shopping"),
        ("Ping_IDC", "Opus Cloud"),  # datacenter: Nuvem 1 e 2 são a Opus Cloud
        ("Ping_Opus", "Opus Cloud"),
        ("Ping_Datacenter_X", "Datacenter X"),  # externo sem apelido
        ("Ping_Externo", None),
        ("Default_DNS", None),
        ("Teste", None),
    ],
)
def test_destino_do_sla(sla: str, destino: str | None) -> None:
    assert destino_do_sla(sla, _NOMES) == destino


def _membro(iface: str, status: str, lat: float | None) -> dict:
    return {"iface": iface, "label": iface, "latency_ms": lat, "jitter_ms": None, "loss_pct": 0.0, "status": status}


_FORTIGATES = [
    {
        "unidade": "Sede", "name": "FGT80F-Primary", "modelo": "FGT80F", "host": "172.29.3.254", "api_up": True,
        "wans": [{"iface": "wan1", "operadora": "VIVO", "link": True}],
        "redes": [
            {"iface": "VLAN10", "alias": "Rede-Corporativa", "cidr": "172.29.0.0/22", "gateway": "172.29.3.254",
             "vlan": 10, "pai": "Switch", "ativa": True},
            {"iface": "VLAN110", "alias": "Rede-CFTV", "cidr": "172.29.11.0/24", "gateway": "172.29.11.254",
             "vlan": 110, "pai": "Switch", "ativa": True},
        ],
        "sdwan": [{"sla": "Ping_Shopping", "members": [_membro("GDS-STS-SHP", "up", 3.0)]},
                  {"sla": "Ping_Externo", "members": [_membro("wan1", "up", 8.0)]}],
    },
    {
        "unidade": "Shopping", "name": "FGT60F", "modelo": "FGT60F", "host": "10.41.1.1", "api_up": True,
        "wans": [], "redes": [{"iface": "Rede-Interna", "alias": "", "cidr": "10.0.0.0/8", "gateway": "10.41.1.1",
                               "vlan": 0, "pai": "", "ativa": True}],
        "sdwan": [
            {"sla": "Ping_Matriz", "members": [_membro("GDS-STS-MTZ2", "up", 2.3), _membro("SHOP-STS-MTZ", "down", None)]},
            {"sla": "Ping_Triunfo", "members": [_membro("GDS-SHP-TRF", "down", None)]},
            {"sla": "Ping_Opus", "members": [_membro("GDS-SHP-OPS2", "up", 1.9)]},
        ],
    },
]  # fmt: skip

_HOSTS = [
    {"ip": "172.29.1.10", "tipo_sugerido": "endpoint", "online": True},
    {"ip": "172.29.1.11", "tipo_sugerido": "movel", "online": False},
    {"ip": "172.29.11.5", "tipo_sugerido": "camera", "online": True},
    {"ip": "10.41.100.7", "tipo_sugerido": "camera", "online": True},
    {"ip": "172.17.1.9", "tipo_sugerido": "camera", "online": True},  # Triunfo sem token: fora das redes lidas
]


def test_montar_topologia_conta_por_rede_e_monta_tuneis() -> None:
    topo = montar_topologia(_FORTIGATES, ["Triunfo"], _HOSTS)
    sede, shopping = topo["unidades"]
    corp, cftv = sede["redes"]
    assert (corp["total"], corp["online"], corp["por_tipo"]) == (2, 1, {"endpoint": 1, "movel": 1})
    assert (cftv["total"], cftv["por_tipo"]) == (1, {"camera": 1})
    assert sede["dispositivos"] == 3 and shopping["dispositivos"] == 1
    assert topo["sem_rede"] == 1 and topo["pendentes"] == ["Triunfo"]

    tuneis = {(t["de"], t["para"]): t for t in topo["tuneis"]}
    assert set(tuneis) == {
        ("Sede", "Shopping"), ("Shopping", "Sede"), ("Shopping", "Triunfo"), ("Shopping", "Opus Cloud"),
    }  # fmt: skip
    matriz = tuneis[("Shopping", "Sede")]
    assert (matriz["iface"], matriz["latency_ms"], matriz["status"]) == ("GDS-STS-MTZ2", 2.3, "up")  # o caminho de pé
    assert (matriz["membros"], matriz["caidos"]) == (2, 1)
    assert tuneis[("Shopping", "Triunfo")]["status"] == "down"


def test_layout_une_ida_e_volta_e_posiciona_externos() -> None:
    mapa = layout_geral(montar_topologia(_FORTIGATES, ["Triunfo"], _HOSTS))
    nos = {n["nome"]: n for n in mapa["nos"]}
    assert nos["Triunfo"]["tipo"] == "pendente" and nos["Opus Cloud"]["tipo"] == "externo"
    assert nos["Opus Cloud"]["y"] == mapa["altura"] - 22
    assert len(mapa["arestas"]) == 3  # Sede↔Shopping vira uma só
    rotulos = sorted(a["rotulo"] for a in mapa["arestas"])
    assert rotulos == ["2 ms", "2 ms", "sem resposta"]


def test_pagina_ativos_mostra_topologia(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    with (
        patch.object(fortigate_api, "configurados", return_value=([{"nome": "Sede"}], ["Triunfo"])),
        patch.object(fortigate_api, "get_cached_fortigates", return_value=_FORTIGATES),
        patch.object(rede_monitoring, "get_cached_rede_summary", return_value={"hosts": _HOSTS}),
        patch("app.views.dashboards._listar_ativos", return_value=[]),
    ):
        html = authed_client.get("/gov/ativos-rede").get_data(as_text=True)
    for trecho in ("Topologia da rede", "Rede-Corporativa", "VLAN 110", "172.29.11.0/24", "FGT80F-Primary", "VIVO",
                   "Shopping → Sede", "1 de 2 caminhos fora", "token pendente", "<svg"):  # fmt: skip
        assert trecho in html, trecho


def test_pagina_ativos_sem_fortigate(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    with (
        patch.object(fortigate_api, "configurados", return_value=([], [])),
        patch("app.views.dashboards._listar_ativos", return_value=[]),
    ):
        html = authed_client.get("/gov/ativos-rede").get_data(as_text=True)
    assert "Nenhum FortiGate configurado" in html


_NOMES = ["Sede", "Shopping", "Triunfo"]
_WANS = {"189.112.100.49": "Sede", "170.254.107.16": "Triunfo"}


@pytest.mark.parametrize(
    ("nome", "remoto", "origem", "destino"),
    [
        ("TRF-GDS-MTZ", "189.112.100.49", "Triunfo", "Sede"),  # pelo IP da WAN da Sede
        ("TRF-GDS-MTZ2", "45.166.249.159", "Triunfo", "Sede"),  # IP desconhecido: pela sigla
        ("GDS-STS-TRF2", "143.105.25.215", "Sede", "Triunfo"),
        ("MTZ-STS-SHOP", "179.109.122.11", "Sede", "Shopping"),  # pula a própria sigla (MTZ)
        ("TRF-STS-OPS", "200.106.168.29", "Triunfo", "Opus Cloud"),
        ("GDS-SHP-GSURF", "18.231.194.69", "Shopping", None),  # sigla desconhecida
        ("GDS-STS-SEDE", "189.112.100.49", "Sede", None),  # aponta para si mesma
    ],
)
def test_destino_do_ipsec(nome: str, remoto: str, origem: str, destino: str | None) -> None:
    assert destino_do_ipsec({"nome": nome, "remoto": remoto}, origem, _WANS, _NOMES) == destino


def test_tunel_ipsec_sem_sla_entra_na_topologia() -> None:
    fortigates = [
        {**_FORTIGATES[0], "wans": [{"iface": "internal3", "ip": "189.112.100.49"}]},
        {
            "unidade": "Triunfo", "name": "FGT60F-Triunfo", "api_up": True, "wans": [], "redes": [],
            "sdwan": [{"sla": "Ping_Shopping", "members": [_membro("TRF-GDS-SHP", "up", 5.0)]}],
            "ipsec": [
                {"nome": "TRF-GDS-MTZ", "remoto": "189.112.100.49", "status": "up"},
                {"nome": "TRF-GDS-MTZ2", "remoto": "45.166.249.159", "status": "down"},
                {"nome": "TRF-GDS-SHP", "remoto": "1.2.3.4", "status": "up"},  # já medido pelo SLA
            ],
        },
    ]  # fmt: skip
    topo = montar_topologia(fortigates, [], [])
    tuneis = {(t["de"], t["para"]): t for t in topo["tuneis"]}
    matriz = tuneis[("Triunfo", "Sede")]
    assert (matriz["iface"], matriz["status"], matriz["latency_ms"]) == ("TRF-GDS-MTZ", "up", None)
    assert (matriz["membros"], matriz["caidos"]) == (2, 1)
    assert tuneis[("Triunfo", "Shopping")]["membros"] == 1
    rotulos = {a["rotulo"] for a in layout_geral(topo)["arestas"]}
    assert "IPsec ativo" in rotulos
