"""Isolar máquina a pedido: Acronis quando há incidente que permite, senão quarentena no FortiGate."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import requests

from app.extensions import db
from app.models.aprovacao import PENDENTE, Solicitacao
from app.models.isolamento import ACRONIS, FALHOU, FORTIGATE, ISOLADA, LIBERADA, IsolamentoMaquina
from app.models.user import User
from app.services import isolamento_maquina as iso
from itgov.services import acronis_edr, fortigate_api
from itgov.services.acronis_edr import IncidenteEdr
from itgov.services.fortigate_api import QuarentenaError

_FW = {"nome": "Sede", "url": "https://fw.test", "token": "ler", "sha256": "", "token_escrita": "esc"}


def _inc(iid: str, maquina: str, acoes: dict[str, str] | None = None) -> IncidenteEdr:
    return IncidenteEdr(
        incident_id=iid,
        customer_id="cust",
        maquina=maquina,
        criado_em="2026-11-03T10:00:00Z",
        link=f"https://console/{iid}",
        acoes=acoes or {},
    )


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """FortiGate Sede com a rede 172.29.3.0/24 e um cliente DHCP; Acronis sem incidentes."""
    chamadas: dict[str, list] = {"quarentena": [], "liberar": [], "acao": []}
    monkeypatch.setattr(
        fortigate_api,
        "get_cached_clientes",
        lambda: [{"ip": "172.29.3.86", "hostname": "NT0307261", "mac": "AA", "fortigate": "Sede"}],
    )
    monkeypatch.setattr(
        fortigate_api,
        "get_cached_fortigates",
        lambda: [{"unidade": "Sede", "redes": [{"cidr": "172.29.3.0/24", "gateway": "172.29.3.254"}]}],
    )
    monkeypatch.setattr(fortigate_api, "configurados", lambda: ([_FW], []))
    monkeypatch.setattr(fortigate_api, "quarentenar", lambda fw, ip: chamadas["quarentena"].append((fw["nome"], ip)))
    monkeypatch.setattr(fortigate_api, "liberar_quarentena", lambda fw, ip: chamadas["liberar"].append(ip))
    monkeypatch.setattr(acronis_edr, "configurado", lambda: True)
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [])
    monkeypatch.delenv("ISOLAR_IPS_PROTEGIDOS", raising=False)
    return chamadas


@pytest.fixture
def banco(factory_app) -> Iterator[Any]:
    with factory_app.app_context():
        yield factory_app
        IsolamentoMaquina.query.delete()
        Solicitacao.query.delete()
        db.session.commit()


# ── resolver ────────────────────────────────────────────────────────────────


def test_resolver_por_nome_curto_ou_fqdn_e_por_ip(ambiente) -> None:
    assert iso.resolver("NT0307261") == ("NT0307261", "172.29.3.86")
    assert iso.resolver("nt0307261.grupogadens.com.br") == ("nt0307261.grupogadens.com.br", "172.29.3.86")
    assert iso.resolver("172.29.3.86") == ("NT0307261", "172.29.3.86")


def test_resolver_cai_no_dns_e_recusa_lixo(ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso.socket, "gethostbyname", lambda n: "172.29.3.90")
    assert iso.resolver("OUTRA") == ("OUTRA", "172.29.3.90")
    with pytest.raises(iso.IsolamentoError):
        iso.resolver("x; rm -rf /")
    with pytest.raises(iso.IsolamentoError):
        iso.resolver("")


# ── isolar ──────────────────────────────────────────────────────────────────


def test_sem_incidente_vai_para_quarentena_no_fortigate(banco, ambiente) -> None:
    r = iso.isolar("NT0307261", "máquina recolhida", "chefe@test")
    assert (r.mecanismo, r.status, r.fortigate, r.ip) == (FORTIGATE, ISOLADA, "Sede", "172.29.3.86")
    assert ambiente["quarentena"] == [("Sede", "172.29.3.86")]
    assert "172.29.3.0/24" in r.resultado


def test_com_incidente_que_permite_isola_no_acronis(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _inc("i1", "NT0307261.grupogadens.com.br")
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [_inc("x", "OUTRA.grupogadens.com.br"), base])
    detalhe = _inc("i1", "NT0307261.grupogadens.com.br", {"WORKLOAD_ISOLATE": "w1"})
    monkeypatch.setattr(acronis_edr, "detalhe", lambda iid, cid: detalhe)

    def executar(inc: IncidenteEdr, acao: str, comentario: str) -> str:
        ambiente["acao"].append((inc.incident_id, acao, comentario))
        return "act-1"

    monkeypatch.setattr(acronis_edr, "executar_acao", executar)
    r = iso.isolar("NT0307261", "suspeita", "chefe@test")
    assert (r.mecanismo, r.status, r.incident_id, r.activity_id, r.acao_status) == (
        ACRONIS,
        ISOLADA,
        "i1",
        "act-1",
        "QUEUED",
    )
    assert ambiente["quarentena"] == []
    assert ambiente["acao"][0][1] == "WORKLOAD_ISOLATE" and "aprovado por chefe@test" in ambiente["acao"][0][2]


def test_incidente_sem_acao_de_isolar_usa_fortigate(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [_inc("i1", "NT0307261")])
    monkeypatch.setattr(acronis_edr, "detalhe", lambda iid, cid: _inc("i1", "NT0307261", {"PROCESS_STOP": ""}))
    assert iso.isolar("NT0307261", "", "u").mecanismo == FORTIGATE


def test_acronis_fora_do_ar_usa_fortigate_e_avisa(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    def falha() -> list:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(acronis_edr, "listar_abertos", falha)
    r = iso.isolar("NT0307261", "", "u")
    assert r.mecanismo == FORTIGATE and r.resultado.startswith("Acronis fora do ar")


def test_fortigate_recusa_token_grava_falha(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    def recusa(fw: dict, ip: str) -> None:
        raise QuarentenaError("O FortiGate Sede recusou o token (HTTP 403)")

    monkeypatch.setattr(fortigate_api, "quarentenar", recusa)
    r = iso.isolar("172.29.3.86", "", "u")
    assert r.status == FALHOU and "403" in r.resultado
    # falha não conta como isolada: dá para pedir de novo
    assert iso.ativo("NT0307261", "172.29.3.86") is None


def test_protecoes(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(iso.IsolamentoError, match="gateway"):
        iso.isolar("172.29.3.254", "", "u")
    with pytest.raises(iso.IsolamentoError, match="rede interna"):
        iso.isolar("8.8.8.8", "", "u")
    monkeypatch.setenv("ISOLAR_IPS_PROTEGIDOS", "172.29.3.10, 172.29.3.11")
    with pytest.raises(iso.IsolamentoError, match="protegidos"):
        iso.isolar("172.29.3.11", "", "u")
    with pytest.raises(iso.IsolamentoError, match="Nenhum FortiGate"):
        iso.isolar("10.9.9.9", "", "u")
    assert ambiente["quarentena"] == []


def test_sem_ip_e_sem_incidente_pede_o_ip(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    def nao_resolve(nome: str) -> str:
        raise iso.socket.gaierror("x")

    monkeypatch.setattr(iso.socket, "gethostbyname", nao_resolve)
    with pytest.raises(iso.IsolamentoError, match="Informe o IP"):
        iso.isolar("FANTASMA", "", "u")


def test_nao_isola_duas_vezes(banco, ambiente) -> None:
    iso.isolar("NT0307261", "", "u")
    with pytest.raises(iso.IsolamentoError, match="já está isolada"):
        iso.isolar("172.29.3.86", "", "u")


# ── liberar e acompanhar ────────────────────────────────────────────────────


def test_liberar_fortigate(banco, ambiente) -> None:
    r = iso.isolar("NT0307261", "", "u")
    iso.liberar(r, "chefe@test")
    assert r.status == LIBERADA and r.liberado_por == "chefe@test"
    assert ambiente["liberar"] == ["172.29.3.86"]
    with pytest.raises(iso.IsolamentoError):
        iso.liberar(r, "chefe@test")


def test_liberar_fortigate_com_falha_continua_isolada(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    r = iso.isolar("NT0307261", "", "u")

    def falha(fw: dict, ip: str) -> None:
        raise QuarentenaError("rede")

    monkeypatch.setattr(fortigate_api, "liberar_quarentena", falha)
    iso.liberar(r, "chefe@test")
    assert r.status == ISOLADA and r.liberado_por == "" and "Monitor" in r.resultado


def test_acronis_acompanha_isolar_e_desisolar(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    inc = _inc("i1", "NT0307261", {"WORKLOAD_ISOLATE": "w", "WORKLOAD_DEISOLATE": "w"})
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [inc])
    monkeypatch.setattr(acronis_edr, "detalhe", lambda iid, cid: inc)
    ids = iter(["act-iso", "act-des"])
    monkeypatch.setattr(acronis_edr, "executar_acao", lambda i, a, c: next(ids))
    status = {"act-iso": "SUCCEEDED", "act-des": "STARTED"}
    monkeypatch.setattr(acronis_edr, "status_acao", lambda iid, cid, act: status[act])
    r = iso.isolar("NT0307261", "", "u")
    assert iso.acompanhar() == 1 and r.acao_status == "SUCCEEDED"
    iso.liberar(r, "chefe@test")
    assert r.liberar_status == "QUEUED" and iso.liberando(r)
    with pytest.raises(iso.IsolamentoError):
        iso.liberar(r, "chefe@test")  # já liberando
    iso.acompanhar()
    assert r.status == ISOLADA and r.liberar_status == "STARTED"
    status["act-des"] = "SUCCEEDED"
    iso.acompanhar()
    assert r.status == LIBERADA


def test_acronis_que_falhou_ao_isolar_sai_da_lista(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    inc = _inc("i1", "NT0307261", {"WORKLOAD_ISOLATE": "w"})
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [inc])
    monkeypatch.setattr(acronis_edr, "detalhe", lambda iid, cid: inc)
    monkeypatch.setattr(acronis_edr, "executar_acao", lambda i, a, c: "act")
    monkeypatch.setattr(acronis_edr, "status_acao", lambda iid, cid, act: "FAILED")
    r = iso.isolar("NT0307261", "", "u")
    iso.acompanhar()
    assert r.status == FALHOU
    assert iso.painel()["ativas"] == [] and iso.painel()["historico"][0]["id"] == r.id


# ── FortiGate: chamadas de escrita ──────────────────────────────────────────


class _Resp:
    def __init__(self, status: int, corpo: Any) -> None:
        self.status_code, self._corpo = status, corpo

    def json(self) -> Any:
        return self._corpo


class _Sessao:
    def __init__(self, resp: _Resp | Exception, chamadas: list) -> None:
        self.resp, self.chamadas = resp, chamadas

    def __enter__(self) -> _Sessao:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def post(self, url: str, **kw: Any) -> _Resp:
        self.chamadas.append((url, kw["headers"]["Authorization"], kw["json"]))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def test_quarentena_usa_token_de_escrita_e_corpo_certo(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list = []
    monkeypatch.setattr(fortigate_api, "_sessao", lambda fw: _Sessao(_Resp(200, {"status": "success"}), chamadas))
    fortigate_api.quarentenar(_FW, "172.29.3.86")
    fortigate_api.liberar_quarentena(_FW, "172.29.3.86")
    assert chamadas[0] == (
        "https://fw.test/api/v2/monitor/user/banned/add_users",
        "Bearer esc",
        {"ip_addresses": ["172.29.3.86"], "expiry": 0},
    )
    assert chamadas[1][0].endswith("/banned/clear_users") and chamadas[1][2] == {"ip_addresses": ["172.29.3.86"]}


@pytest.mark.parametrize(
    ("resp", "trecho"),
    [
        (_Resp(403, {}), "TOKEN_ESCRITA"),
        (_Resp(500, {}), "HTTP 500"),
        (_Resp(200, {"status": "error"}), "não confirmou"),
        (requests.ConnectionError("x"), "Falha de rede"),
    ],
)
def test_quarentena_erros_viram_mensagem(monkeypatch: pytest.MonkeyPatch, resp: Any, trecho: str) -> None:
    monkeypatch.setattr(fortigate_api, "_sessao", lambda fw: _Sessao(resp, []))
    with pytest.raises(QuarentenaError, match=trecho):
        fortigate_api.quarentenar(_FW, "172.29.3.86")


def test_quarentena_sem_token_de_escrita_usa_o_de_leitura(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list = []
    monkeypatch.setattr(fortigate_api, "_sessao", lambda fw: _Sessao(_Resp(200, {"status": "success"}), chamadas))
    fortigate_api.quarentenar(_FW | {"token_escrita": ""}, "172.29.3.86")
    assert chamadas[0][1] == "Bearer ler"


def test_fortigate_da_rede_e_configurados_le_token_de_escrita(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fortigate_api,
        "get_cached_fortigates",
        lambda: [{"unidade": "Sede", "redes": [{"cidr": "172.29.3.0/24", "gateway": "172.29.3.254"}]}],
    )
    assert fortigate_api.fortigate_da_rede("172.29.3.86") == ("Sede", "172.29.3.0/24")
    assert fortigate_api.fortigate_da_rede("10.0.0.1") is None
    assert fortigate_api.gateways() == {"172.29.3.254"}
    for k in [k for k in __import__("os").environ if k.startswith("FORTIGATE_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("FORTIGATE_TESTE_URL", "https://x")
    monkeypatch.setenv("FORTIGATE_TESTE_TOKEN", "a")
    monkeypatch.setenv("FORTIGATE_TESTE_TOKEN_ESCRITA", "b")
    prontos, _ = fortigate_api.configurados()
    assert prontos[0]["token_escrita"] == "b"


# ── rotas: aprovação ────────────────────────────────────────────────────────


def _cliente(factory_app, email: str, role: str, super_admin: bool) -> Any:
    with factory_app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(name=email.split("@")[0], email=email, role=role)
            user.set_password("pytest-only-not-real")
            db.session.add(user)
        user.role, user.super_admin, user.is_active = role, super_admin, True
        db.session.commit()
        uid = user.id
    c = factory_app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    return c


@pytest.fixture
def rotas(factory_app) -> Iterator[Any]:
    yield factory_app
    with factory_app.app_context():
        IsolamentoMaquina.query.delete()
        Solicitacao.query.delete()
        User.query.filter_by(email="pytest-super-iso@test.local").one().super_admin = False
        db.session.commit()


def test_gestor_pede_super_admin_aprova_e_libera(rotas, ambiente) -> None:
    app = rotas
    gestor = _cliente(app, "pytest-gestor-iso@test.local", "gestor", False)
    chefe = _cliente(app, "pytest-super-iso@test.local", "admin", True)
    resp = gestor.post("/gov/cyber/isolamentos", json={"alvo": "NT0307261", "motivo": "recolhida"})
    assert resp.status_code == 202 and resp.get_json()["pendente"] is True
    assert ambiente["quarentena"] == []  # nada sem aprovação
    with app.app_context():
        sol = Solicitacao.query.filter_by(status=PENDENTE).one()
        sol_id = sol.id
        assert "NT0307261" in sol.resumo and "recolhida" in sol.resumo
    chefe.post(f"/gov/aprovacoes/{sol_id}/aprovar")
    assert ambiente["quarentena"] == [("Sede", "172.29.3.86")]
    with app.app_context():
        r = IsolamentoMaquina.query.one()
        rid, pedido = r.id, r.pedido_por
    assert pedido == "pytest-super-iso@test.local (pedido de pytest-gestor-iso@test.local)"
    painel = chefe.get("/api/v1/cyber/incidentes/agente").get_json()
    assert [a["id"] for a in painel["isolamentos"]["ativas"]] == [rid]
    resp = chefe.post(f"/gov/cyber/isolamentos/{rid}/liberar", json={})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert chefe.post(f"/gov/cyber/isolamentos/{rid}/liberar", json={}).status_code == 409
    assert chefe.post("/gov/cyber/isolamentos/999999/liberar", json={}).status_code == 404


def test_pedido_invalido_volta_400(rotas, ambiente) -> None:
    chefe = _cliente(rotas, "pytest-super-iso@test.local", "admin", True)
    resp = chefe.post("/gov/cyber/isolamentos", json={"alvo": "172.29.3.254"})
    assert resp.status_code == 400 and "gateway" in resp.get_json()["error"]
