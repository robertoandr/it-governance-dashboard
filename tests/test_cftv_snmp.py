"""CFTV: IP e SNMP (v2c/v3) dos gravadores gravados no Zabbix pelo admin (Conferência V2.0 cf7)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.extensions import db
from app.services.aprovacoes import CAMPOS_SENSIVEIS
from itgov.api.v1 import cftv_monitoring
from itgov.api.v1.cftv_monitoring import _ip_principal, gravador_do_dispositivo
from itgov.services import cftv_snmp as sn
from itgov.services.cftv_snmp import ConfigSnmp

V3 = {"ip": "10.0.0.9", "versao": "3", "usuario": "zbxro", "auth_senha": "autenticar1", "priv_senha": "privado123"}


class FakeZabbix:
    """Responde ao ``_zbx`` do serviço e guarda as chamadas."""

    def __init__(self, hosts: list[dict[str, Any]] | None = None, itens: int = 2) -> None:
        self.hosts = hosts or []
        self.itens = itens
        self.chamadas: list[tuple[str, Any]] = []

    def __call__(self, metodo: str, params: Any) -> Any:
        self.chamadas.append((metodo, params))
        if metodo == "host.get":
            if "tags" in params:
                return [h for h in self.hosts if h.get("_tag_dvr") == params["tags"][0]["value"]]
            return [h for h in self.hosts if h["host"] in params["filter"]["host"]]
        if metodo == "hostgroup.get":
            return [{"groupid": "30"}]
        if metodo == "template.get":
            return [{"templateid": "10563"}]
        if metodo == "host.create":
            return {"hostids": ["999"]}
        if metodo == "item.get":
            return [{"itemid": str(i)} for i in range(self.itens)]
        return {}

    def metodos(self) -> list[str]:
        return [m for m, _p in self.chamadas]

    def params(self, metodo: str) -> list[Any]:
        return [p for m, p in self.chamadas if m == metodo]


# ── Validação e montagem ──────────────────────────────────────────────────────


def test_v3_exige_usuario_e_senha_com_8_caracteres() -> None:
    with pytest.raises(ValidationError, match="usuário SNMPv3"):
        ConfigSnmp(ip="10.0.0.9", versao="3")
    with pytest.raises(ValidationError, match="pelo menos 8"):
        ConfigSnmp(ip="10.0.0.9", versao="3", usuario="u", auth_senha="curta")
    with pytest.raises(ValidationError):
        ConfigSnmp(ip="não é ip", versao="2")


def test_detalhes_apontam_para_macros_e_senha_vazia_mantem() -> None:
    v3 = ConfigSnmp(**{**V3, "priv_senha": ""})
    det = v3.detalhes()
    assert det["securityname"] == sn.MACRO_USUARIO and det["authpassphrase"] == sn.MACRO_AUTH
    assert det["securitylevel"] == "2" and "autenticar1" not in str(det)
    assert v3.macros() == [(sn.MACRO_USUARIO, "zbxro", "0"), (sn.MACRO_AUTH, "autenticar1", "1")]
    v2 = ConfigSnmp(ip="10.0.0.9", versao="2", community="publica")
    assert v2.detalhes() == {"version": "2", "bulk": "1", "community": sn.MACRO_COMMUNITY}
    assert v2.macros() == [(sn.MACRO_COMMUNITY, "publica", "1")]
    assert ConfigSnmp(ip="10.0.0.9", versao="2").macros() == []


def test_nivel_sem_privacidade_nao_grava_senha_de_privacidade() -> None:
    cfg = ConfigSnmp(**{**V3, "nivel": 1})
    assert [m for m, _v, _t in cfg.macros()] == [sn.MACRO_USUARIO, sn.MACRO_AUTH]


def test_nome_tecnico_sem_acento() -> None:
    assert sn.nome_tecnico("Loja Útil") == "Loja Util"
    assert sn.nome_tecnico("Triunfo 2") == "Triunfo 2"
    assert sn.nome_tecnico("★★") == "gravador"


# ── Aplicar no Zabbix ─────────────────────────────────────────────────────────


def test_gravador_sem_host_e_cadastrado_com_tag_e_macros_secretas() -> None:
    zbx = FakeZabbix()
    with patch.object(sn, "_zbx", zbx):
        res = sn.aplicar("Loja Útil", ConfigSnmp(**V3))
    assert res.criado and res.hostid == "999" and res.itens_verificados == 2 and res.senhas_faltando == []
    criado = zbx.params("host.create")[0]
    assert criado["host"] == "Loja Util" and criado["name"] == "Loja Útil"
    assert {"tag": "dvr", "value": "Loja Útil"} in criado["tags"]
    assert {"tag": "subcategory", "value": "dvr"} in criado["tags"]
    assert criado["templates"] == [{"templateid": "10563"}] and criado["groups"] == [{"groupid": "30"}]
    assert criado["interfaces"][0]["ip"] == "10.0.0.9" and criado["interfaces"][0]["type"] == "2"
    secretas = {m["macro"] for m in criado["macros"] if m["type"] == "1"}
    assert secretas == {sn.MACRO_AUTH, sn.MACRO_PRIV}
    assert zbx.params("task.create")[0] == [
        {"type": "6", "request": {"itemid": "0"}},
        {"type": "6", "request": {"itemid": "1"}},
    ]


def test_host_existente_troca_ip_interface_e_macros() -> None:
    host = {
        "hostid": "77",
        "host": "DVR-1",
        "interfaces": [
            {"interfaceid": "1", "type": "1", "main": "1", "ip": "172.29.11.17"},
            {"interfaceid": "2", "type": "2", "main": "1", "ip": "172.29.11.17"},
        ],
        "macros": [{"hostmacroid": "m1", "macro": sn.MACRO_USUARIO, "type": "0"}],
    }
    zbx = FakeZabbix([host], itens=0)
    with patch.object(sn, "_zbx", zbx):
        res = sn.aplicar("DVR-1", ConfigSnmp(**{**V3, "ip": "172.29.11.27", "priv_senha": ""}))
    assert not res.criado and res.itens_verificados == 0
    upd = zbx.params("hostinterface.update")
    assert upd[0]["interfaceid"] == "2" and upd[0]["ip"] == "172.29.11.27" and upd[0]["details"]["version"] == "3"
    assert upd[1] == {"interfaceid": "1", "ip": "172.29.11.27"}
    assert zbx.params("usermacro.update") == [{"hostmacroid": "m1", "value": "zbxro", "type": "0"}]
    assert zbx.params("usermacro.create")[0]["macro"] == sn.MACRO_AUTH
    # Sem senha de privacidade no form nem no host: avisa que cai na global
    assert res.senhas_faltando == ["senha de privacidade"]
    assert "task.create" not in zbx.metodos()


def test_host_sem_interface_snmp_ganha_uma() -> None:
    host = {
        "hostid": "5",
        "host": "nvr-x",
        "interfaces": [{"interfaceid": "1", "type": "1", "main": "1", "ip": "10.0.0.9"}],
    }
    zbx = FakeZabbix([host])
    with patch.object(sn, "_zbx", zbx):
        res = sn.aplicar("nvr-x", ConfigSnmp(ip="10.0.0.9", versao="2"))
    criada = zbx.params("hostinterface.create")[0]
    assert criada["hostid"] == "5" and criada["details"]["community"] == sn.MACRO_COMMUNITY
    assert "hostinterface.update" not in zbx.metodos()  # IP do agente já era o mesmo
    assert res.senhas_faltando == ["community"]


def test_host_criado_pelo_dashboard_e_achado_pela_tag() -> None:
    host = {"hostid": "8", "host": "Loja Util", "_tag_dvr": "Loja Útil", "interfaces": [], "macros": []}
    zbx = FakeZabbix([host])
    with patch.object(sn, "_zbx", zbx):
        res = sn.aplicar("Loja Útil", ConfigSnmp(**V3))
    assert res.hostid == "8" and "host.create" not in zbx.metodos()


def test_check_now_recusado_nao_derruba() -> None:
    zbx = FakeZabbix()

    def _zbx(metodo: str, params: Any) -> Any:
        if metodo == "task.create":
            raise RuntimeError("Zabbix API: item não suportado")
        return zbx(metodo, params)

    with patch.object(sn, "_zbx", _zbx):
        assert sn.aplicar("Novo", ConfigSnmp(**V3)).itens_verificados == 0


def test_sem_grupo_ou_template_falha_claro() -> None:
    zbx = FakeZabbix()

    def _zbx(metodo: str, params: Any) -> Any:
        return [] if metodo == "template.get" else zbx(metodo, params)

    with patch.object(sn, "_zbx", _zbx), pytest.raises(RuntimeError, match="Generic by SNMP"):
        sn.aplicar("Novo", ConfigSnmp(**V3))


# ── Status no card ────────────────────────────────────────────────────────────


def test_status_snmp() -> None:
    assert sn.status_snmp([{"type": "1"}], []) is None
    st = sn.status_snmp(
        [
            {
                "type": "2",
                "available": "2",
                "error": "Authentication failure",
                "port": "161",
                "details": {"version": "3", "securitylevel": "1", "authprotocol": "3"},
            }
        ],
        [{"macro": sn.MACRO_USUARIO, "value": "zbxro", "type": "0"}, {"macro": sn.MACRO_AUTH, "type": "1"}],
    )
    assert st is not None
    assert st["status"] == "falha" and st["usuario"] == "zbxro" and st["nivel"] == 1 and st["auth_protocolo"] == 3
    assert st["macros_host"] == [sn.MACRO_AUTH, sn.MACRO_USUARIO]
    assert sn.status_snmp([{"type": "2", "available": "0"}], [])["status"] == "aguardando"  # type: ignore[index]


def test_gravador_criado_pelo_dashboard_usa_a_tag_dvr() -> None:
    assert gravador_do_dispositivo("Loja Util", "dvr", {"dvr": "Loja Útil"}) == "Loja Útil"
    assert gravador_do_dispositivo("DVR-1", "dvr", {}) == "DVR-1"


def test_ip_principal_prefere_agente() -> None:
    assert _ip_principal([{"type": "2", "main": "1", "ip": "b"}, {"type": "1", "main": "1", "ip": "a"}]) == "a"
    assert _ip_principal([{"type": "2", "main": "1", "ip": "b"}]) == "b"
    assert _ip_principal([]) == "?"


# ── Rota e página ─────────────────────────────────────────────────────────────


def test_senhas_snmp_nunca_vao_para_a_fila() -> None:
    assert {"snmp_community", "snmp_auth_senha", "snmp_priv_senha"} <= CAMPOS_SENSIVEIS


_FORM = {
    "gravador": "Loja Útil",
    "snmp_ip": "10.0.0.9",
    "snmp_versao": "3",
    "snmp_usuario": "zbxro",
    "snmp_nivel": "2",
    "snmp_auth_protocolo": "1",
    "snmp_auth_senha": "autenticar1",
    "snmp_priv_protocolo": "1",
    "snmp_priv_senha": "",
}


def test_rota_aplica_e_avisa_o_que_falta(authed_client) -> None:
    res = sn.Resultado(hostid="1", criado=True, itens_verificados=3, senhas_faltando=["senha de privacidade"])
    with patch.object(sn, "aplicar", return_value=res) as aplicar:
        resp = authed_client.post("/gov/cftv/gravador/snmp", data=_FORM, follow_redirects=True)
    html = resp.get_data(as_text=True)
    gravador, cfg = aplicar.call_args.args
    assert gravador == "Loja Útil" and cfg.auth_senha == "autenticar1" and str(cfg.ip) == "10.0.0.9"
    assert "cadastrado no Zabbix" in html and "checagem de 3 itens" in html and "Falta informar" in html


def test_rota_recusa_formulario_invalido_sem_chamar_o_zabbix(authed_client) -> None:
    with patch.object(sn, "aplicar") as aplicar:
        resp = authed_client.post(
            "/gov/cftv/gravador/snmp", data={**_FORM, "snmp_auth_senha": "curta"}, follow_redirects=True
        )
    assert "não foi salvo" in resp.get_data(as_text=True) and "pelo menos 8" in resp.get_data(as_text=True)
    aplicar.assert_not_called()
    assert authed_client.post("/gov/cftv/gravador/snmp", data={"gravador": ""}).status_code == 400


def test_rota_mostra_recusa_do_zabbix(authed_client) -> None:
    with patch.object(sn, "aplicar", side_effect=RuntimeError("Zabbix API: host já existe")):
        resp = authed_client.post("/gov/cftv/gravador/snmp", data=_FORM, follow_redirects=True)
    assert "O Zabbix recusou" in resp.get_data(as_text=True)


@pytest.fixture
def gestor_client(factory_app) -> Iterator:
    from app.models.user import User

    with factory_app.app_context():
        user = User.query.filter_by(email="pytest-gestor-snmp@test.local").first()
        if user is None:
            user = User(name="Pytest Gestor SNMP", email="pytest-gestor-snmp@test.local", role="gestor")
            user.set_password("pytest-only-not-real")
            db.session.add(user)
            db.session.commit()
        uid = user.id
    with factory_app.test_client() as c:
        with c.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        yield c


def test_gestor_nao_altera_snmp(gestor_client) -> None:
    with patch.object(sn, "aplicar") as aplicar:
        resp = gestor_client.post("/gov/cftv/gravador/snmp", data=_FORM)
    assert resp.status_code == 403
    aplicar.assert_not_called()


def test_pagina_mostra_status_e_formulario(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    monkeypatch.setattr(cftv_monitoring, "get_cached_historico_quedas", lambda: {})
    base = {"andar": "?", "canal": "", "loja": "", "vendor": "", "model": "", "status": "up", "problems": 0}
    snmp = sn.status_snmp(
        [{"type": "2", "available": "2", "error": "Authentication failure", "details": {"version": "3"}}],
        [{"macro": sn.MACRO_AUTH, "type": "1"}],
    )
    dados = {
        "enabled": True,
        "devices": [
            {
                **base,
                "host": "DVR-1",
                "name": "DVR-1",
                "ip": "172.29.11.17",
                "subcat": "dvr",
                "gravador": "DVR-1",
                "is_gravador": True,
                "snmp": snmp,
            },
            {
                **base,
                "host": "cam-1",
                "name": "cam-1",
                "ip": "x",
                "subcat": "camera",
                "gravador": "DVR-1",
                "is_gravador": False,
            },
            {
                **base,
                "host": "cam-2",
                "name": "cam-2",
                "ip": "y",
                "subcat": "camera",
                "gravador": "Shopping 1",
                "is_gravador": False,
            },
        ],
    }
    with patch.object(cftv_monitoring, "get_cached_cftv_summary", return_value=dados):
        html = authed_client.get("/gov/cftv?ver=gravadores").get_data(as_text=True)
    assert "SNMP v3 · falha" in html and "Zabbix: Authentication failure" in html
    assert "Salvar SNMP" in html and "Cadastrar no Zabbix" in html
    assert "definida — em branco mantém" in html and ".snmp-form:has(" in html
