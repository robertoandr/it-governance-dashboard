"""Isolar máquina a pedido: só pelo Acronis, a partir de incidente que permite isolar."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.extensions import db
from app.models.aprovacao import PENDENTE, Solicitacao
from app.models.isolamento import ACRONIS, FALHOU, ISOLADA, LIBERADA, IsolamentoMaquina
from app.models.user import User
from app.services import isolamento_maquina as iso
from itgov.services import acronis_edr, fortigate_api
from itgov.services.acronis_edr import IncidenteEdr


def _inc(iid: str, maquina: str, acoes: dict[str, str] | None = None) -> IncidenteEdr:
    return IncidenteEdr(
        incident_id=iid,
        customer_id="cust",
        maquina=maquina,
        criado_em="2026-11-03T10:00:00Z",
        link=f"https://console/{iid}",
        acoes=acoes or {},
    )


_ISOLAVEL = _inc("i1", "NT0307261.grupogadens.com.br", {"WORKLOAD_ISOLATE": "w", "WORKLOAD_DEISOLATE": "w"})


@pytest.fixture
def ambiente(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Cliente DHCP NT0307261 nos FortiGates; Acronis com incidente da máquina que permite isolar."""
    estado: dict[str, Any] = {"acao": [], "status": {}, "ids": iter(f"act-{n}" for n in range(1, 99))}
    monkeypatch.setattr(
        fortigate_api,
        "get_cached_clientes",
        lambda: [{"ip": "172.29.3.86", "hostname": "NT0307261", "mac": "AA", "fortigate": "Sede"}],
    )
    monkeypatch.setattr(acronis_edr, "configurado", lambda: True)
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [_inc("x", "OUTRA.grupogadens.com.br"), _ISOLAVEL])
    monkeypatch.setattr(acronis_edr, "detalhe", lambda iid, cid: _ISOLAVEL if iid == "i1" else _inc(iid, "OUTRA"))

    def executar(inc: IncidenteEdr, acao: str, comentario: str) -> str:
        estado["acao"].append((inc.incident_id, acao, comentario))
        return next(estado["ids"])

    monkeypatch.setattr(acronis_edr, "executar_acao", executar)
    monkeypatch.setattr(acronis_edr, "status_acao", lambda iid, cid, act: estado["status"].get(act, "STARTED"))
    return estado


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


def test_resolver_cai_no_dns_e_alvo_invalido_e_recusado(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(iso.socket, "gethostbyname", lambda n: "172.29.3.90")
    assert iso.resolver("OUTRA") == ("OUTRA", "172.29.3.90")
    assert not iso.alvo_valido("x; rm -rf /") and not iso.alvo_valido("")
    assert iso.isolar("x; rm -rf /", "", "u") == (None, iso.isolar("", "", "u")[1])
    assert "Informe o nome" in iso.isolar("", "", "u")[1]


# ── isolar ──────────────────────────────────────────────────────────────────


def test_com_incidente_que_permite_isola_no_acronis(banco, ambiente) -> None:
    r, msg = iso.isolar("NT0307261", "suspeita", "chefe@test")
    assert msg == ""
    assert (r.mecanismo, r.status, r.incident_id, r.activity_id, r.acao_status, r.ip) == (
        ACRONIS,
        ISOLADA,
        "i1",
        "act-1",
        "QUEUED",
        "172.29.3.86",
    )
    assert r.maquina == "NT0307261.grupogadens.com.br" and r.fortigate == ""
    assert ambiente["acao"][0][1] == "WORKLOAD_ISOLATE" and "aprovado por chefe@test" in ambiente["acao"][0][2]


def test_por_ip_acha_a_maquina_pelo_dhcp(banco, ambiente) -> None:
    r, _ = iso.isolar("172.29.3.86", "", "u")
    assert r.mecanismo == ACRONIS and r.incident_id == "i1"


def test_sem_incidente_recusa_sem_gravar(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [])
    r, msg = iso.isolar("NT0307261", "", "u")
    assert r is None and "não tem incidente aberto no Acronis" in msg
    assert ambiente["acao"] == [] and IsolamentoMaquina.query.count() == 0


def test_incidente_sem_acao_de_isolar_recusa(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acronis_edr, "listar_abertos", lambda: [_inc("i1", "NT0307261")])
    monkeypatch.setattr(acronis_edr, "detalhe", lambda iid, cid: _inc("i1", "NT0307261", {"PROCESS_STOP": ""}))
    assert "não tem incidente aberto" in iso.isolar("NT0307261", "", "u")[1]


def test_acronis_fora_do_ar_recusa(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    def falha() -> list:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(acronis_edr, "listar_abertos", falha)
    assert iso.isolar("NT0307261", "", "u") == (None, "O Acronis está fora do ar; tente de novo em alguns minutos.")


def test_acronis_nao_configurado_recusa(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acronis_edr, "configurado", lambda: False)
    assert "não está configurado" in iso.isolar("NT0307261", "", "u")[1]


def test_ip_sem_nome_pede_o_nome(banco, ambiente) -> None:
    assert "Informe o nome" in iso.isolar("172.29.3.99", "", "u")[1]


def test_acronis_recusa_grava_falha(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    def recusa(inc: IncidenteEdr, acao: str, comentario: str) -> str:
        raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "https://a"), response=httpx.Response(500))

    monkeypatch.setattr(acronis_edr, "executar_acao", recusa)
    r, _ = iso.isolar("NT0307261", "", "u")
    assert r.status == FALHOU and "não aceitou isolar" in r.resultado
    # falha não conta como isolada: dá para pedir de novo
    assert iso.ativo("NT0307261", "172.29.3.86") is None


def test_nao_isola_duas_vezes(banco, ambiente) -> None:
    iso.isolar("NT0307261", "", "u")
    assert iso.isolar("172.29.3.86", "", "u") == (None, "NT0307261 já está isolada.")


# ── liberar e acompanhar ────────────────────────────────────────────────────


def test_acronis_acompanha_isolar_e_desisolar(banco, ambiente) -> None:
    ambiente["status"] |= {"act-1": "SUCCEEDED", "act-2": "STARTED"}
    r, _ = iso.isolar("NT0307261", "", "u")
    assert iso.acompanhar() == 1 and r.acao_status == "SUCCEEDED"
    assert iso.liberar(r, "chefe@test") == ""
    assert r.liberar_status == "QUEUED" and iso.liberando(r) and r.liberado_por == "chefe@test"
    assert ambiente["acao"][1][1] == "WORKLOAD_DEISOLATE"
    assert iso.liberar(r, "chefe@test") != ""  # já liberando
    iso.acompanhar()
    assert r.status == ISOLADA and r.liberar_status == "STARTED"
    ambiente["status"]["act-2"] = "SUCCEEDED"
    iso.acompanhar()
    assert r.status == LIBERADA
    assert "não está isolada" in iso.liberar(r, "chefe@test")


def test_liberar_com_falha_no_acronis(banco, ambiente, monkeypatch: pytest.MonkeyPatch) -> None:
    r, _ = iso.isolar("NT0307261", "", "u")

    def falha(iid: str, cid: str) -> IncidenteEdr:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(acronis_edr, "detalhe", falha)
    iso.liberar(r, "chefe@test")
    assert r.status == ISOLADA and r.liberar_status == "FAILED" and "console do Acronis" in r.resultado


def test_acronis_que_falhou_ao_isolar_sai_da_lista(banco, ambiente) -> None:
    ambiente["status"]["act-1"] = "FAILED"
    r, _ = iso.isolar("NT0307261", "", "u")
    iso.acompanhar()
    assert r.status == FALHOU
    assert iso.painel()["ativas"] == [] and iso.painel()["historico"][0]["id"] == r.id


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
    assert ambiente["acao"] == []  # nada sem aprovação
    with app.app_context():
        sol = Solicitacao.query.filter_by(status=PENDENTE).one()
        sol_id = sol.id
        assert "NT0307261" in sol.resumo and "recolhida" in sol.resumo
    chefe.post(f"/gov/aprovacoes/{sol_id}/aprovar")
    assert [a[1] for a in ambiente["acao"]] == ["WORKLOAD_ISOLATE"]
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
    resp = chefe.post("/gov/cyber/isolamentos", json={"alvo": "x; rm -rf /"})
    assert resp.status_code == 400 and "Informe o nome" in resp.get_json()["error"]
