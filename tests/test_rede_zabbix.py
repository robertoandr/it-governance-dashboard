"""r2: template do Zabbix sugerido/automático para dispositivos descobertos."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from app.extensions import db
from app.services import rede_zabbix
from itgov.services import zabbix_templates as zt


@pytest.mark.parametrize(
    ("host", "template", "confianca"),
    [
        ({"tipo_sugerido": "camera", "vendor": "Intelbras"}, "ICMP Ping", "alta"),
        ({"tipo_sugerido": "camera", "hostname": "NVR-SEDE", "has_snmp": True}, "Intelbras NVR SNMP", "alta"),
        ({"tipo_sugerido": "camera", "hostname": "NVR-SEDE"}, "Intelbras NVR SNMP", "baixa"),
        ({"tipo_sugerido": "impressora", "snmp_descr": "RICOH IM C2000"}, "SNMP Ricoh Printers", "alta"),
        ({"tipo_sugerido": "impressora", "vendor": "Lexmark", "has_snmp": True}, "SNMP - Lexmark COLOR", "alta"),
        ({"tipo_sugerido": "impressora", "vendor": "Xerox", "has_snmp": True}, "Generic by SNMP", "baixa"),
        ({"tipo_sugerido": "switch", "snmp_descr": "RouterOS RB4011"}, "Mikrotik by SNMP", "alta"),
        ({"tipo_sugerido": "switch", "has_snmp": True}, "Network Generic Device by SNMP", "baixa"),
        ({"tipo_sugerido": "ap", "vendor": "Ubiquiti", "has_snmp": True}, "Ubiquiti AirOS by SNMP", "alta"),
        ({"tipo_sugerido": "outro", "snmp_descr": "APC Web/SNMP Smart-UPS 1500"}, "APC UPS by SNMP", "alta"),
        ({"tipo_sugerido": "servidor", "portas": "3389"}, "Windows by Zabbix agent active", "baixa"),
        ({"tipo_sugerido": "servidor", "portas": "22"}, "Linux by Zabbix agent active", "baixa"),
        ({"tipo_sugerido": "outro"}, "ICMP Ping", "baixa"),
        ({"tipo_sugerido": "relogio_ponto"}, "ICMP Ping", "baixa"),
    ],
)
def test_sugerir(host: dict, template: str, confianca: str) -> None:
    s = zt.sugerir(host)
    assert s is not None and (s.template, s.confianca) == (template, confianca)


@pytest.mark.parametrize("tipo", ["movel", "endpoint"])
def test_nao_sugere_para_aparelho_pessoal(tipo: str) -> None:
    assert zt.sugerir({"tipo_sugerido": tipo}) is None


def test_nome_host_unico_pelo_ip() -> None:
    assert zt.nome_host("Impressora Recepção", "10.0.0.5") == (
        "Impressora-Recep-o_10.0.0.5",
        "Impressora Recepção (10.0.0.5)",
    )
    assert zt.nome_host("", "10.0.0.5") == ("dispositivo_10.0.0.5", "10.0.0.5")


class _Zbx:
    """Zabbix falso: guarda as chamadas e responde o mínimo."""

    def __init__(self, existente: dict | None = None) -> None:
        self.chamadas: list[tuple[str, dict]] = []
        self.existente = existente

    def __call__(self, metodo: str, params: dict) -> object:
        self.chamadas.append((metodo, params))
        if metodo == "template.get":
            nome = params.get("filter", {}).get("name", [""])[0]
            return [] if nome == "Nao existe" else [{"templateid": "77", "name": nome}]
        if metodo == "hostgroup.get":
            return []
        if metodo == "hostgroup.create":
            return {"groupids": ["9"]}
        if metodo == "hostinterface.get":
            return [{"hostid": "500"}] if self.existente else []
        if metodo == "host.get":
            return [self.existente]
        if metodo == "host.create":
            return {"hostids": ["600"]}
        return {}

    def params(self, metodo: str) -> dict:
        return next(p for m, p in self.chamadas if m == metodo)


def test_monitorar_cria_host_snmp_com_grupo_novo() -> None:
    zbx = _Zbx()
    with patch.object(zt, "_zbx", zbx):
        assert zt.monitorar("10.0.0.5", "IMP-01", "SNMP Ricoh Printers", "Impressoras", "snmp") == ("600", True)
    criado = zbx.params("host.create")
    assert criado["groups"] == [{"groupid": "9"}] and criado["templates"] == [{"templateid": "77"}]
    assert criado["interfaces"][0]["type"] == zt.TIPO_SNMP and criado["interfaces"][0]["port"] == "161"
    assert {"tag": "origem", "value": "dashboard-rede"} in criado["tags"]


def test_monitorar_troca_template_de_host_do_dashboard() -> None:
    existente = {"hostid": "500", "name": "x", "tags": [{"tag": "origem", "value": "dashboard-rede"}],
                 "parentTemplates": [{"templateid": "10", "name": "ICMP Ping"}]}  # fmt: skip
    zbx = _Zbx(existente)
    with patch.object(zt, "_zbx", zbx):
        assert zt.monitorar("10.0.0.5", "x", "Generic by SNMP", "Impressoras", "snmp") == ("500", False)
    upd = zbx.params("host.update")
    assert upd["templates"] == [{"templateid": "77"}] and upd["templates_clear"] == [{"templateid": "10"}]


def test_monitorar_nao_mexe_em_host_de_outra_origem() -> None:
    zbx = _Zbx({"hostid": "500", "name": "cam-loja", "tags": [], "parentTemplates": []})
    with patch.object(zt, "_zbx", zbx), pytest.raises(ValueError, match="já é monitorado"):
        zt.monitorar("10.0.0.5", "x", "ICMP Ping", "CFTV/Cameras", "agente")
    with patch.object(zt, "_zbx", _Zbx()), pytest.raises(ValueError, match="não existe"):
        zt.monitorar("10.0.0.5", "x", "Nao existe", "g", "agente")


# ── Estado, automático e telas ──────────────────────────────────────────────


@pytest.fixture
def limpo(factory_app) -> Iterator[None]:
    from app.models.rede import RedeConfig, RedeMonitoramento

    yield
    with factory_app.app_context():
        RedeMonitoramento.query.delete()
        RedeConfig.query.delete()
        db.session.commit()


_CAM = {"ip": "10.41.1.20", "tipo_sugerido": "camera", "vendor": "Intelbras", "hostname": "CAM-20"}
_IMP = {"ip": "172.29.1.9", "tipo_sugerido": "impressora", "vendor": "Xerox", "has_snmp": True}
_MON = {"ip": "172.29.1.2", "tipo_sugerido": "camera", "zabbix_grupos": ["CFTV/Cameras"]}


def test_situacao(factory_app, limpo) -> None:
    from types import SimpleNamespace as Dec

    assert rede_zabbix.situacao(_MON, None)["estado"] == "monitorado"
    assert rede_zabbix.situacao(_CAM, None)["estado"] == "auto"
    assert rede_zabbix.situacao(_IMP, None)["estado"] == "sugestao"
    assert rede_zabbix.situacao({"ip": "1", "tipo_sugerido": "movel"}, None)["estado"] == "nao_se_aplica"
    assert rede_zabbix.situacao(_CAM, Dec(decisao="recusado", hostid=""))["estado"] == "recusado"
    feito = rede_zabbix.situacao(
        {**_CAM, "zabbix_grupos": ["CFTV/Cameras"]}, Dec(decisao="auto", hostid="1", template="ICMP Ping")
    )
    assert feito["estado"] == "pelo_dashboard" and feito["template"] == "ICMP Ping"


def test_processar_so_com_auto_ligado_e_so_confianca_alta(factory_app, limpo) -> None:
    with factory_app.app_context():
        with patch.object(zt, "monitorar", return_value=("600", True)) as criar:
            assert rede_zabbix._processar([_CAM, _IMP, _MON]) == 0
            rede_zabbix.definir_auto(True)
            assert rede_zabbix._processar([_CAM, _IMP, _MON]) == 1
            criar.assert_called_once_with("10.41.1.20", "CAM-20", "ICMP Ping", "CFTV/Cameras", "agente")
            assert rede_zabbix._processar([_CAM, _IMP, _MON]) == 0  # já decidido
        assert rede_zabbix.decisoes()["10.41.1.20"].hostid == "600"


def test_processar_grava_erro(factory_app, limpo) -> None:
    with factory_app.app_context():
        rede_zabbix.definir_auto(True)
        with patch.object(zt, "monitorar", side_effect=RuntimeError("Zabbix API: boom")):
            assert rede_zabbix._processar([_CAM]) == 0
        d = rede_zabbix.decisoes()["10.41.1.20"]
        assert d.decisao == "erro" and "boom" in d.erro
        with patch.object(zt, "monitorar", return_value=("600", True)) as criar:
            assert rede_zabbix._processar([_CAM]) == 0  # espera antes de tentar de novo
            criar.assert_not_called()


@pytest.fixture
def pagina(monkeypatch: pytest.MonkeyPatch, factory_app, limpo) -> Iterator[None]:
    from itgov.api.v1 import rede_monitoring

    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    hosts = [
        {**_CAM, "mac": "", "portas": "", "online": True, "recente": False, "os_guess": "", "motivo": ""},
        {
            **_IMP,
            "hostname": "",
            "mac": "",
            "portas": "",
            "online": True,
            "recente": False,
            "os_guess": "",
            "motivo": "",
        },
    ]
    dados = {"enabled": True, "hosts": hosts, "total": 2, "online": 2, "recentes": 0,
             "fontes": {"zabbix": 2, "nmap": 0, "nmap_ultimo": None}, "ia_ativa": False, "erro_descoberta": "",
             "drules": [], "active_drules": [], "scan_ranges": []}  # fmt: skip
    with (
        patch.object(rede_monitoring, "get_cached_rede_summary", return_value=dados),
        patch.object(rede_monitoring, "get_latencia", return_value={"unidades": [], "piores": []}),
        patch("app.views.dashboards._listar_ativos", return_value=[]),
        patch.object(zt, "templates_disponiveis", return_value=["Generic by SNMP", "ICMP Ping", "SNMP Ricoh Printers"]),
    ):
        yield


def test_pagina_mostra_coluna_zabbix_e_liga_auto(authed_client, pagina) -> None:
    html = authed_client.get("/gov/rede").get_data(as_text=True)
    assert "Pronto para entrar: ICMP Ping" in html and "Confirmar: Generic by SNMP" in html
    assert "1 com template certo" in html and "1 para confirmar" in html
    assert 'datalist id="zbx-templates"' in html and "desligado" in html
    assert authed_client.post("/gov/rede/zabbix/auto", data={"ligar": "1"}).status_code == 302
    html = authed_client.get("/gov/rede").get_data(as_text=True)
    assert "Entra sozinho: ICMP Ping" in html and "Monitorar sozinho no Zabbix: ligado" in html


def test_monitorar_recusar_e_trocar_pela_tela(authed_client, pagina) -> None:
    with patch.object(zt, "monitorar", return_value=("600", True)) as criar:
        resp = authed_client.post("/gov/rede/zabbix/172.29.1.9/monitorar", data={"template": "SNMP Ricoh Printers"})
        assert resp.status_code == 302
        criar.assert_called_once_with("172.29.1.9", "", "SNMP Ricoh Printers", "Impressoras", "snmp")
    assert "✓ SNMP Ricoh Printers" in authed_client.get("/gov/rede").get_data(as_text=True)

    with patch.object(zt, "monitorar") as criar:
        authed_client.post("/gov/rede/zabbix/172.29.1.9/monitorar", data={"template": "Inexistente"})
        criar.assert_not_called()
    assert "não existe no Zabbix" in authed_client.get("/gov/rede").get_data(as_text=True)

    assert authed_client.post("/gov/rede/zabbix/10.41.1.20/recusar").status_code == 302
    assert "não monitorar" in authed_client.get("/gov/rede").get_data(as_text=True)
    assert authed_client.post("/gov/rede/zabbix/9.9.9.9/monitorar", data={"template": "ICMP Ping"}).status_code == 404
