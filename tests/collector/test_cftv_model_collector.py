"""Tests for collector/jobs/cftv_model_collector.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

import config as _cfg

# config.py da raiz não tem `settings`; o módulo sob teste faz `from config import settings`
if not hasattr(_cfg, "settings"):
    _cfg.settings = MagicMock()

from collector.jobs import cftv_model_collector as mod
from collector.jobs.cftv_model_collector import (
    CredencialRecusadaError,
    Equipamento,
    Resultado,
    coletar,
    consultar,
    gravar_modelo,
    listar_sem_modelo,
    modelo_hikvision,
    modelo_intelbras,
    modelo_onvif,
    protocolo_para,
)

_HOST_OK = "10.41.101.148"


def _resp(status: int = 200, text: str = "") -> MagicMock:
    r = MagicMock(status_code=status, text=text)
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(str(status))
    return r


def _eq(host: str = "cam-1", vendor: str = "Intelbras", ip: str = _HOST_OK) -> Equipamento:
    return Equipamento(
        hostid=host, host=host, ip=ip, vendor=vendor, tags=(("subcategory", "camera"), ("vendor", vendor))
    )


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    s = SimpleNamespace(
        ZABBIX_URL="https://zbx.test",
        ZABBIX_TOKEN="tok",
        ZBX_VERIFY_TLS=False,
        CFTV_CAM_USER="admin",
        CFTV_CAM_PASS="segredo",
    )
    monkeypatch.setattr(mod, "settings", s)
    return s


# ── Protocolos ────────────────────────────────────────────────────────────────


def test_intelbras_le_type_com_digest() -> None:
    with patch.object(mod.requests, "get", return_value=_resp(text="type=VIP-3230-B\r\n")) as get:
        assert modelo_intelbras(_HOST_OK, "admin", "x") == "VIP-3230-B"
    kwargs = get.call_args.kwargs
    assert kwargs["params"] == {"action": "getDeviceType"}
    assert isinstance(kwargs["auth"], requests.auth.HTTPDigestAuth)
    assert kwargs["timeout"] == 5


def test_intelbras_401_vira_credencial_recusada() -> None:
    with patch.object(mod.requests, "get", return_value=_resp(401)), pytest.raises(CredencialRecusadaError):
        modelo_intelbras(_HOST_OK, "admin", "errada")


def test_hikvision_le_model_com_namespace() -> None:
    xml = (
        '<?xml version="1.0"?><DeviceInfo xmlns="http://www.hikvision.com/ver20/XMLSchema">'
        "<deviceName>cam</deviceName><model>DS-2CD1043G0-I</model></DeviceInfo>"
    )
    with patch.object(mod.requests, "get", return_value=_resp(text=xml)):
        assert modelo_hikvision("172.29.11.40", "admin", "x") == "DS-2CD1043G0-I"


def test_onvif_le_model_e_envia_ws_security() -> None:
    xml = (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        "<s:Body><tds:GetDeviceInformationResponse><tds:Manufacturer>ACME</tds:Manufacturer>"
        "<tds:Model>IPC-100</tds:Model></tds:GetDeviceInformationResponse></s:Body></s:Envelope>"
    )
    with patch.object(mod.requests, "post", return_value=_resp(text=xml)) as post:
        assert modelo_onvif("10.0.0.9", "admin", "segredo") == "IPC-100"
    corpo = post.call_args.kwargs["data"].decode()
    assert "<Username>admin</Username>" in corpo
    assert "PasswordDigest" in corpo
    assert "segredo" not in corpo  # senha nunca vai em texto puro


def test_onvif_not_authorized_vira_credencial_recusada() -> None:
    fault = "<s:Envelope><s:Body><s:Fault><s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode></s:Fault></s:Body></s:Envelope>"
    with patch.object(mod.requests, "post", return_value=_resp(400, fault)), pytest.raises(CredencialRecusadaError):
        modelo_onvif("10.0.0.9", "admin", "errada")


@pytest.mark.parametrize(
    ("vendor", "fonte"),
    [("Intelbras", "intelbras_cgi"), ("dahua", "intelbras_cgi"), ("Hikvision", "hikvision_isapi"), ("", "onvif")],
)
def test_protocolo_por_fabricante(vendor: str, fonte: str) -> None:
    assert protocolo_para(vendor)[0] == fonte


# ── consultar / coletar ───────────────────────────────────────────────────────


def test_consultar_classifica_erros() -> None:
    with patch.object(mod, "modelo_intelbras", side_effect=CredencialRecusadaError):
        assert consultar(_eq(), "u", "p").erro == "auth"
    with patch.object(mod, "modelo_intelbras", side_effect=requests.ConnectTimeout()):
        assert consultar(_eq(), "u", "p").erro == "ConnectTimeout"
    with patch.object(mod, "modelo_intelbras", return_value=""):
        assert consultar(_eq(), "u", "p").erro == "vazio"
    with patch.object(mod, "modelo_intelbras", return_value="VIP-1"):
        r = consultar(_eq(), "u", "p")
    assert (r.model, r.fonte, r.erro) == ("VIP-1", "intelbras_cgi", "")


def test_coletar_aborta_quando_credencial_errada_no_inicio() -> None:
    equipamentos = [_eq(f"cam-{i}") for i in range(20)]
    with patch.object(mod, "modelo_intelbras", side_effect=CredencialRecusadaError) as fn:
        resultados = coletar(equipamentos, "u", "errada")
    # Só os 5 primeiros são tentados: evita bloquear a conta em todo o parque
    assert fn.call_count == mod._LIMITE_AUTH_INICIAL
    assert len(resultados) == mod._LIMITE_AUTH_INICIAL


def test_coletar_continua_se_alguma_inicial_autentica() -> None:
    equipamentos = [_eq(f"cam-{i}") for i in range(12)]
    respostas = [CredencialRecusadaError()] * 4 + ["VIP-1"] * 8
    with patch.object(mod, "modelo_intelbras", side_effect=respostas):
        resultados = coletar(equipamentos, "u", "p")
    assert len(resultados) == 12
    assert sum(1 for r in resultados if r.model) == 8


# ── Zabbix ────────────────────────────────────────────────────────────────────


def test_listar_sem_modelo_filtra_subcategoria_e_modelo_existente(cfg: SimpleNamespace) -> None:
    def zbx(method: str, params: dict) -> list:
        if method == "hostgroup.get":
            return [{"groupid": "1", "name": "CFTV/Cameras"}, {"groupid": "2", "name": "Linux servers"}]
        assert params["groupids"] == ["1"]
        return [
            {"hostid": "a", "host": "cam-a", "interfaces": [{"ip": "10.0.0.1", "main": "1"}],
             "tags": [{"tag": "subcategory", "value": "camera"}, {"tag": "vendor", "value": "Intelbras"}]},
            {"hostid": "b", "host": "cam-b", "interfaces": [{"ip": "10.0.0.2", "main": "1"}],
             "tags": [{"tag": "subcategory", "value": "camera"}, {"tag": "model", "value": "VIP-1"}]},
            {"hostid": "c", "host": "facial-c", "interfaces": [{"ip": "10.0.0.3", "main": "1"}],
             "tags": [{"tag": "subcategory", "value": "facial"}]},
            {"hostid": "d", "host": "dvr-d", "interfaces": [],
             "tags": [{"tag": "subcategory", "value": "dvr"}]},
        ]  # fmt: skip

    with patch.object(mod, "_zbx", side_effect=zbx):
        eqs = listar_sem_modelo()
    assert [(e.host, e.ip, e.vendor) for e in eqs] == [("cam-a", "10.0.0.1", "Intelbras")]


def test_gravar_modelo_preserva_tags_e_substitui_model(cfg: SimpleNamespace) -> None:
    eq = Equipamento(
        hostid="42",
        host="cam",
        ip="10.0.0.1",
        vendor="Intelbras",
        tags=(("dvr", "Shopping 1"), ("model", "antigo"), ("canal", "3")),
    )
    with patch.object(mod, "_zbx") as zbx:
        gravar_modelo(Resultado(eq, model="VIP-3230-B", fonte="intelbras_cgi"))
    method, params = zbx.call_args.args
    assert method == "host.update"
    assert params["hostid"] == "42"
    assert params["tags"] == [
        {"tag": "dvr", "value": "Shopping 1"},
        {"tag": "canal", "value": "3"},
        {"tag": "model", "value": "VIP-3230-B"},
        {"tag": "model_source", "value": "intelbras_cgi"},
    ]


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_ignora_sem_credencial(cfg: SimpleNamespace) -> None:
    cfg.CFTV_CAM_PASS = ""
    with patch.object(mod, "listar_sem_modelo") as listar:
        mod.run()
    listar.assert_not_called()


def test_run_grava_so_quem_tem_modelo(cfg: SimpleNamespace) -> None:
    ok, falha = _eq("cam-ok"), _eq("cam-falha")
    with (
        patch.object(mod, "listar_sem_modelo", return_value=[ok, falha]),
        patch.object(
            mod, "coletar", return_value=[Resultado(ok, model="VIP-1", fonte="x"), Resultado(falha, erro="auth")]
        ),
        patch.object(mod, "gravar_modelo") as gravar,
    ):
        mod.run()
    assert [c.args[0].equipamento.host for c in gravar.call_args_list] == ["cam-ok"]


def test_run_sobrevive_a_falha_do_zabbix(cfg: SimpleNamespace) -> None:
    with patch.object(mod, "listar_sem_modelo", side_effect=requests.ConnectionError("down")):
        mod.run()  # não propaga
