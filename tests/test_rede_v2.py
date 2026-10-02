"""Página Rede V2: descoberta do Zabbix, classificação, nomenclatura, latência e IA."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.extensions import db
from app.models.rede import RedeVisto
from app.models.unidade import Unidade
from app.services import rede_ia
from itgov.api.v1 import rede_monitoring
from itgov.api.v1.rede_descoberta import (
    classificar,
    faixas_fora_da_varredura,
    hosts_da_descoberta,
    latencia_por_unidade,
    precisa_ia,
    resumo_por_unidade,
    sigla_unidade,
    sugerir_nome,
)

_DCHECKS = {"1": ("12", "0"), "2": ("8", "443"), "3": ("11", "161"), "4": ("8", "3389"), "5": ("9", "10050")}


def _svc(ip: str, dcheck: str, status: str = "0", value: str = "", lastup: int = 100) -> dict:
    return {"ip": ip, "dcheckid": dcheck, "status": status, "value": value, "dns": "", "lastup": str(lastup)}


# ── Descoberta e classificação ───────────────────────────────────────────────


def test_hosts_da_descoberta_agrupa_por_ip_e_cruza_com_zabbix() -> None:
    dservices = [
        _svc("172.29.1.5", "1", lastup=100),
        _svc("172.29.1.5", "3", value="RICOH IM C2000 / RICOH Network Printer", lastup=200),
        _svc("172.29.1.6", "1", status="1"),
        _svc("172.29.1.6", "4", status="1"),
        _svc("172.29.11.14", "1"),
    ]
    monitorados = {"172.29.11.14": {"nome": "cam-loja-d17", "grupos": ["CFTV/Cameras"]}}
    hosts = {h["ip"]: h for h in hosts_da_descoberta(dservices, _DCHECKS, monitorados)}

    impressora = hosts["172.29.1.5"]
    assert impressora["online"] and impressora["has_snmp"] and impressora["ultimo_visto"] == 200
    assert impressora["vendor"] == "Ricoh"
    assert (impressora["tipo_sugerido"], impressora["motivo"]) == ("impressora", "descrição SNMP de impressora")

    windows = hosts["172.29.1.6"]
    assert not windows["online"] and windows["portas"] == "3389"
    assert windows["tipo_sugerido"] == "servidor"

    camera = hosts["172.29.11.14"]
    assert camera["hostname"] == "cam-loja-d17" and camera["tipo_sugerido"] == "camera"
    assert "CFTV/Cameras" in camera["motivo"]


@pytest.mark.parametrize(
    ("host", "tipo"),
    [
        ({"snmp_descr": "FortiGate-60F v7.2", "has_snmp": True}, "firewall"),
        ({"snmp_descr": "Cisco IOS Software, C2960", "has_snmp": True}, "switch"),
        ({"snmp_descr": "UniFi AP-AC-Pro", "has_snmp": True}, "ap"),
        ({"snmp_descr": "Hardware: Intel64 - Software: Windows 10", "has_snmp": True}, "endpoint"),
        ({"snmp_descr": "Linux srv 5.15", "has_snmp": True}, "servidor"),
        ({"portas": "22,443"}, "servidor"),
        ({"has_snmp": True}, "switch"),
        ({"portas": "443"}, "outro"),
        ({}, "outro"),
    ],
)
def test_classificar(host: dict, tipo: str) -> None:
    assert classificar(host)[0] == tipo


def test_precisa_ia_so_com_algum_sinal() -> None:
    assert precisa_ia({"tipo_sugerido": "outro", "portas": "443"})
    assert not precisa_ia({"tipo_sugerido": "outro"})  # só ping: nada a analisar
    assert not precisa_ia({"tipo_sugerido": "impressora", "portas": "9100"})
    assert not precisa_ia({"tipo_sugerido": "outro", "portas": "443", "zabbix_grupos": ["CFTV/Controle de acesso"]})


# ── Nomenclatura ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("nome", "sigla"),
    [("Shopping", "SHO"), ("Sede Centro", "SCE"), ("Triunfo Fábrica", "TFA"), ("Autoshop", "AUT"), ("", "GER")],
)
def test_sigla_unidade(nome: str, sigla: str) -> None:
    assert sigla_unidade(nome) == sigla


def test_sugerir_nome_usa_proximo_numero_livre() -> None:
    existentes = ["SHO-CAM-001", "SHO-CAM-007", "SHO-IMP-020", "SHO-CAM-x"]
    assert sugerir_nome("camera", "SHO", existentes) == "SHO-CAM-008"
    assert sugerir_nome("impressora", "SCE", existentes) == "SCE-IMP-001"
    assert sugerir_nome("desconhecido", "SHO", []) == "SHO-DSP-001"


# ── Por unidade ──────────────────────────────────────────────────────────────

_FAIXAS = [(1, "10.41.0.0/16"), (2, "172.29.0.0/16")]
_NOMES = {1: "Shopping", 2: "Sede Centro"}


def test_resumo_por_unidade() -> None:
    hosts = [
        {"ip": "10.41.1.1", "online": True, "recente": True},
        {"ip": "10.41.1.2", "online": False},
        {"ip": "172.29.1.1", "online": True},
        {"ip": "8.8.8.8", "online": True},
    ]
    cards = resumo_por_unidade(hosts, _FAIXAS, _NOMES, cadastrados={"10.41.1.2"})
    assert [c["nome"] for c in cards] == ["Sede Centro", "Shopping", "Sem unidade"]
    shopping = cards[1]
    assert (shopping["total"], shopping["online"], shopping["sem_cadastro"], shopping["recentes"]) == (2, 1, 1, 1)


def test_latencia_por_unidade() -> None:
    medidas = [
        {"ip": "10.41.1.1", "nome": "a", "ms": 2.0, "perda": 0.0, "up": True, "disp_24h": 100.0},
        {"ip": "10.41.1.2", "nome": "b", "ms": 150.0, "perda": 20.0, "up": True, "disp_24h": 90.0},
        {"ip": "10.41.1.3", "nome": "c", "ms": None, "perda": 100.0, "up": False, "disp_24h": 50.0},
        {"ip": "172.29.1.1", "nome": "d", "ms": 1.0, "perda": 0.0, "up": True, "disp_24h": None},
    ]
    r = latencia_por_unidade(medidas, _FAIXAS, _NOMES)
    sede, shopping = r["unidades"]
    assert sede["nome"] == "Sede Centro" and sede["disp_24h"] is None and sede["media_ms"] == 1.0
    assert (shopping["hosts"], shopping["offline"], shopping["com_perda"]) == (3, 1, 2)
    assert shopping["media_ms"] == 76.0 and shopping["max_ms"] == 150.0 and shopping["disp_24h"] == 80.0
    assert [m["nome"] for m in r["piores"]] == ["b"]  # offline não entra em "piores agora"


def test_faixas_fora_da_varredura() -> None:
    faixas = [("Sede Centro", "172.29.0.0/22"), ("Shopping", "10.41.0.0/16"), ("Casa", "192.168.0.0/24")]
    fora = faixas_fora_da_varredura(faixas, ["172.29.0.0/22", "192.168.0.1-254", "lixo"])
    assert fora == [("Shopping", "10.41.0.0/16"), ("Casa", "192.168.0.0/24")]  # .0 e .255 fora do intervalo


# ── Montagem (rede_monitoring) ───────────────────────────────────────────────


def test_juntar_nmap_completa_zabbix() -> None:
    zabbix = [
        {"ip": "172.29.1.5", "hostname": "", "vendor": "", "mac": "", "portas": "", "tipo_sugerido": "outro",
         "motivo": "só responde a ping", "zabbix_grupos": [], "online": True},
        {"ip": "172.29.11.14", "hostname": "cam", "vendor": "", "mac": "", "portas": "", "tipo_sugerido": "camera",
         "motivo": "Zabbix", "zabbix_grupos": ["CFTV/Cameras"], "online": True},
    ]  # fmt: skip
    nmap = [
        {
            "ip": "172.29.1.5",
            "hostname": "srv-app",
            "mac": "00:50:56:AA:BB:CC",
            "tipo_sugerido": "vm",
            "motivo": "MAC VMware",
        },
        {"ip": "172.29.11.14", "tipo_sugerido": "outro", "motivo": ""},
        {"ip": "172.29.1.99", "tipo_sugerido": "endpoint", "motivo": "x"},
    ]
    hosts = {h["ip"]: h for h in rede_monitoring._juntar(zabbix, nmap)}
    assert hosts["172.29.1.5"]["tipo_sugerido"] == "vm" and hosts["172.29.1.5"]["mac"] == "00:50:56:AA:BB:CC"
    assert hosts["172.29.11.14"]["tipo_sugerido"] == "camera"  # grupo do Zabbix prevalece
    assert hosts["172.29.1.99"]["origem"] == "nmap"


def test_enriquecer_marca_recente_e_aplica_ia() -> None:
    agora = datetime.now(UTC)
    vistos = {
        "1.1.1.1": SimpleNamespace(baseline=False, primeiro_visto=agora - timedelta(days=1), ia_tipo="ap",
                                   ia_nome="AP Ubiquiti", ia_motivo="porta 8443"),
        "2.2.2.2": SimpleNamespace(baseline=True, primeiro_visto=agora, ia_tipo="", ia_nome="", ia_motivo=""),
        "3.3.3.3": SimpleNamespace(baseline=False, primeiro_visto=(agora - timedelta(days=30)).replace(tzinfo=None),
                                   ia_tipo="servidor", ia_nome="", ia_motivo="x"),
    }  # fmt: skip
    hosts = [
        {"ip": "1.1.1.1", "tipo_sugerido": "outro"},
        {"ip": "2.2.2.2", "tipo_sugerido": "outro"},
        {"ip": "3.3.3.3", "tipo_sugerido": "impressora"},
    ]
    rede_monitoring._enriquecer(hosts, vistos, agora)
    a, b, c = hosts
    assert a["recente"] and a["tipo_sugerido"] == "ap" and a["por_ia"] and a["ia_nome"] == "AP Ubiquiti"
    assert not b["recente"]  # baseline: já estava na rede
    assert not c["recente"] and c["tipo_sugerido"] == "impressora"  # regra vale mais que a IA


def test_registrar_vistos_primeira_carga_e_baseline(factory_app) -> None:
    ips = ["198.51.100.1", "198.51.100.2"]
    with factory_app.app_context():
        RedeVisto.query.filter(RedeVisto.ip.like("198.51.100.%")).delete(synchronize_session=False)
        semente = RedeVisto.query.count() == 0
        if semente:
            db.session.add(RedeVisto(ip="198.51.100.250", baseline=True))
        db.session.commit()
        try:
            vistos = rede_monitoring._registrar_vistos([{"ip": ip, "online": True} for ip in ips])
            assert all(not vistos[ip].baseline for ip in ips)  # tabela já tinha dados: são novos
            assert RedeVisto.query.filter(RedeVisto.ip.in_(ips)).count() == 2
        finally:
            RedeVisto.query.filter(RedeVisto.ip.like("198.51.100.%")).delete(synchronize_session=False)
            db.session.commit()


def test_buscar_descoberta_e_medidas_ping() -> None:
    respostas = {
        "dcheck.get": [{"dcheckid": "1", "type": "12", "ports": "0"}],
        "dservice.get": [_svc("10.41.1.1", "1")],
        "host.get": [{"hostid": "10", "name": "cam-1", "interfaces": [{"ip": "10.41.1.1"}],
                      "hostgroups": [{"name": "CFTV/Cameras"}]}],
        "item.get": [
            {"itemid": "1", "key_": "icmpping", "lastvalue": "1", "hostid": "10"},
            {"itemid": "2", "key_": "icmppingsec", "lastvalue": "0.0042", "hostid": "10"},
            {"itemid": "3", "key_": "icmppingloss", "lastvalue": "10", "hostid": "10"},
        ],
        "trend.get": [{"itemid": "1", "value_avg": "1"}, {"itemid": "1", "value_avg": "0.5"}],
    }  # fmt: skip
    with patch.object(rede_monitoring, "_zbx", side_effect=lambda metodo, _p: respostas[metodo]):
        (host,) = rede_monitoring._buscar_descoberta()
        (medida,) = rede_monitoring._buscar_medidas_ping()
    assert host["tipo_sugerido"] == "camera" and host["hostname"] == "cam-1"
    assert medida["up"] and medida["ms"] == pytest.approx(4.2) and medida["perda"] == 10.0
    assert medida["disp_24h"] == 75.0


# ── IA ───────────────────────────────────────────────────────────────────────


def _resposta(conteudo: str) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": conteudo}}]}
    return resp


def test_ia_desligada_sem_configuracao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDE_IA_URL", raising=False)
    monkeypatch.delenv("REDE_IA_KEY", raising=False)
    assert not rede_ia.ia_ativa()


def test_classificar_lote_aceita_so_tipos_validos_e_ips_pedidos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "fake-key-for-tests")
    conteudo = """Aqui está: {"hosts": [
        {"ip": "1.1.1.1", "tipo": "ap", "nome": "AP Ubiquiti", "motivo": "porta 8443"},
        {"ip": "2.2.2.2", "tipo": "torradeira", "nome": "x", "motivo": "y"},
        {"ip": "9.9.9.9", "tipo": "servidor", "nome": "intruso", "motivo": "z"}]}"""
    with patch.object(rede_ia.requests, "post", return_value=_resposta(conteudo)) as post:
        r = rede_ia.classificar_lote([{"ip": "1.1.1.1", "portas": "8443"}, {"ip": "2.2.2.2", "portas": "80"}])
    assert r == {"1.1.1.1": {"tipo": "ap", "nome": "AP Ubiquiti", "motivo": "porta 8443"}}
    assert post.call_args.args[0] == "http://ia.test/v1/chat/completions"


def test_classificar_lote_sem_json_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "fake-key-for-tests")
    with patch.object(rede_ia.requests, "post", return_value=_resposta("não sei")), pytest.raises(ValueError):
        rede_ia.classificar_lote([{"ip": "1.1.1.1"}])


def test_assinatura_muda_com_os_sinais() -> None:
    a = rede_ia.assinatura({"portas": "443"})
    assert a == rede_ia.assinatura({"portas": "443", "ip": "outro"})
    assert a != rede_ia.assinatura({"portas": "443,22"})


def test_processar_grava_sugestao_e_nao_repete(factory_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "fake-key-for-tests")
    host = {"ip": "198.51.100.9", "tipo_sugerido": "outro", "portas": "8443", "snmp_descr": "", "hostname": ""}
    with factory_app.app_context():
        db.session.add(RedeVisto(ip=host["ip"]))
        db.session.commit()
    try:
        conteudo = '{"hosts": [{"ip": "198.51.100.9", "tipo": "ap", "nome": "AP", "motivo": "8443"}]}'
        with patch.object(rede_ia.requests, "post", return_value=_resposta(conteudo)) as post:
            rede_ia._processar(factory_app, [host])
            rede_ia._processar(factory_app, [host])  # mesmos sinais: não pergunta de novo
        assert post.call_count == 1
        with factory_app.app_context():
            v = db.session.get(RedeVisto, host["ip"])
            assert (v.ia_tipo, v.ia_nome) == ("ap", "AP") and v.ia_assinatura
    finally:
        with factory_app.app_context():
            RedeVisto.query.filter_by(ip=host["ip"]).delete()
            db.session.commit()


def test_processar_falha_de_rede_espera_antes_de_tentar(factory_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "fake-key-for-tests")
    host = {"ip": "198.51.100.10", "tipo_sugerido": "outro", "portas": "80", "snmp_descr": "", "hostname": ""}
    with factory_app.app_context():
        db.session.add(RedeVisto(ip=host["ip"]))
        db.session.commit()
    monkeypatch.setattr(rede_ia, "_falhou_em", 0.0)
    try:
        with patch.object(rede_ia.requests, "post", side_effect=requests.ConnectionError("fora")):
            rede_ia._processar(factory_app, [host])
        assert rede_ia._falhou_em > 0
        with patch.object(rede_ia.threading, "Thread") as thread:
            rede_ia.processar_em_segundo_plano(factory_app, [host])
        thread.assert_not_called()
    finally:
        with factory_app.app_context():
            RedeVisto.query.filter_by(ip=host["ip"]).delete()
            db.session.commit()


# ── Página ───────────────────────────────────────────────────────────────────


@pytest.fixture
def pagina(monkeypatch: pytest.MonkeyPatch, factory_app) -> Iterator[dict[str, int]]:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    hosts = [
        {"ip": "10.41.1.7", "hostname": "", "vendor": "", "os_guess": "", "mac": "", "portas": "8443",
         "tipo_sugerido": "ap", "motivo": "IA: porta 8443", "por_ia": True, "ia_nome": "AP Ubiquiti",
         "online": True, "recente": True},
        {"ip": "172.29.1.5", "hostname": "", "vendor": "Ricoh", "os_guess": "RICOH", "mac": "", "portas": "",
         "tipo_sugerido": "impressora", "motivo": "descrição SNMP de impressora", "online": False, "recente": False},
    ]  # fmt: skip
    dados = {
        "enabled": True, "hosts": hosts, "total": 2, "online": 1, "recentes": 1,
        "fontes": {"zabbix": 2, "nmap": 0, "nmap_ultimo": None}, "ia_ativa": True, "erro_descoberta": "",
        "drules": [{"id": "3", "name": "Scan Rede Corporativa", "enabled": True, "ranges": ["172.29.0.0/22"],
                    "delay": "1h", "checks": ["ICMP ping"], "nextcheck": 0}],
        "scan_ranges": ["172.29.0.0/22"],
    }  # fmt: skip
    dados["active_drules"] = dados["drules"]
    latencia = {
        "unidades": [{"id": None, "nome": "Shopping", "hosts": 3, "offline": 1, "media_ms": 4.2, "p95_ms": 9.0,
                      "max_ms": 9.0, "perda_media": 0.5, "com_perda": 1, "disp_24h": 98.5}],
        "piores": [{"ip": "10.41.1.9", "nome": "cam-lenta", "ms": 180.0, "perda": 5.0}],
    }  # fmt: skip
    with factory_app.app_context():
        ids = {u.nome: u.id for u in Unidade.query.filter_by(parent_id=None)}
        db.session.get(Unidade, ids["Sede Centro"]).faixas_ip = "172.29.0.0/22"
        db.session.get(Unidade, ids["Shopping"]).faixas_ip = "10.41.0.0/16"
        db.session.commit()
    with (
        patch.object(rede_monitoring, "get_cached_rede_summary", return_value=dados),
        patch.object(rede_monitoring, "get_latencia", return_value=latencia),
        patch("app.views.dashboards._listar_ativos", return_value=[]),
    ):
        yield ids
    with factory_app.app_context():
        for nome in ("Sede Centro", "Shopping"):
            db.session.get(Unidade, ids[nome]).faixas_ip = ""
        db.session.commit()


def test_pagina_rede_mostra_unidades_latencia_e_novos(authed_client, pagina) -> None:
    html = authed_client.get("/gov/rede").get_data(as_text=True)
    for trecho in (
        "1 respondendo agora",
        "1 novo na rede (7 dias)",
        "IA ligada",
        "Latência e estabilidade por unidade",
        "98.50%",
        "cam-lenta",
        "novo na rede",
        "AP Ubiquiti",
        "SHO",  # sigla no padrão de nomes
        "10.41.0.0/16",  # faixa do Shopping fora da regra do Zabbix
    ):
        assert trecho in html, trecho


def test_pagina_rede_filtra_novos_na_rede(authed_client, pagina) -> None:
    html = authed_client.get("/gov/rede?status=recentes").get_data(as_text=True)
    assert "10.41.1.7" in html
    assert "172.29.1.5" not in html
