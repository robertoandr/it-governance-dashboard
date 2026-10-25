"""Agente de incidentes: triagem (regras + IA), execução só com aprovação, lote e aba da página Cyber."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import requests

from app.extensions import db
from app.models.aprovacao import PENDENTE, Solicitacao
from app.models.incidente_agente import (
    ACAO,
    ENCERRAR,
    EXECUTADA,
    FALHOU,
    INVESTIGAR,
    OBSOLETA,
    PROPOSTA,
    REJEITADA,
    PropostaIncidente,
)
from app.models.user import User
from app.services import incidentes_agente as ia
from itgov.services import acronis_edr
from itgov.services.acronis_edr import Deteccao, IncidenteEdr, IncidenteNaoEncontradoError

_AGORA = datetime(2026, 10, 25, 22, 0, tzinfo=UTC)


def _inc(
    iid: str, dias: int = 1, categorias: tuple[str, ...] = ("URL_BLOCKED",), maquina: str = "", **extra: Any
) -> IncidenteEdr:
    criado = (_AGORA - timedelta(days=dias)).isoformat().replace("+00:00", "Z")
    return IncidenteEdr(
        incident_id=iid,
        customer_id="cust",
        maquina=maquina or f"pc-{iid}",
        criado_em=criado,
        atualizado_em=criado,
        severidade="HIGH",
        veredito="MALICIOUS",
        estado="NOT_STARTED",
        mitigacao="NOT_MITIGATED",
        categorias=list(categorias),
        link=f"https://console/{iid}",
        **extra,
    )


def _det(bloqueado: bool | None, alvo: str = "mau.example") -> Deteccao:
    return Deteccao(
        motor="URLF",
        descricao="link malicioso",
        ameaca="URL.Malicious",
        veredito="MALICIOUS",
        bloqueado=bloqueado,
        alvo=alvo,
    )


@pytest.fixture
def banco(factory_app) -> Iterator[Any]:
    ia._tentativa.update(triagem=None, acompanhamento=None)
    with factory_app.app_context():
        yield factory_app
        PropostaIncidente.query.delete()
        Solicitacao.query.delete()
        db.session.commit()


@pytest.fixture
def sem_ia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDE_IA_URL", raising=False)
    monkeypatch.delenv("REDE_IA_KEY", raising=False)


@pytest.fixture
def com_ia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDE_IA_URL", "http://ia.test/v1")
    monkeypatch.setenv("REDE_IA_KEY", "k")


# ── regras ──────────────────────────────────────────────────────────────────


def test_regra_removido_encerra() -> None:
    assert ia.proposta_por_regra(None, True, 0, _AGORA)[0] == ENCERRAR


def test_regra_url_bloqueada_encerra_e_nao_bloqueada_recente_investiga() -> None:
    bloqueada = _inc("a", deteccoes=[_det(True), _det(True, "b.example")])
    decisao, _, evid = ia.proposta_por_regra(bloqueada, False, 0, _AGORA)
    assert decisao == ENCERRAR and evid == ["URL: b.example", "URL: mau.example"]
    nao_bloqueada = _inc("b", deteccoes=[_det(False)])
    assert ia.proposta_por_regra(nao_bloqueada, False, 0, _AGORA)[0] == INVESTIGAR


def test_regra_url_antiga_so_encerra_sem_outros_incidentes_na_maquina() -> None:
    antiga = _inc("a", dias=45, deteccoes=[_det(False)])
    decisao, justificativa, _ = ia.proposta_por_regra(antiga, False, 0, _AGORA)
    assert decisao == ENCERRAR and "45 dias" in justificativa
    assert ia.proposta_por_regra(antiga, False, 2, _AGORA)[0] == INVESTIGAR


def test_regra_pua_e_processo_investigam() -> None:
    pua = _inc("a", dias=60, categorias=("POTENTIALLY_UNWANTED_APPLICATION",))
    assert ia.proposta_por_regra(pua, False, 0, _AGORA)[0] == INVESTIGAR


# ── validação da IA ─────────────────────────────────────────────────────────


def test_validar_propostas_rebaixa_acao_indisponivel_e_ignora_lixo() -> None:
    lote = {
        "a": _inc("a", acoes={"PROCESS_STOP": "w", "WORKLOAD_SHUTDOWN": "w"}),
        "b": _inc("b", acoes={"PROCESS_STOP": "w"}),
        "c": _inc("c"),
    }
    dados = {
        "propostas": [
            {
                "incident_id": "a",
                "decisao": "acao",
                "acao": "process_stop",
                "justificativa": "j",
                "evidencias": ["e"] * 20,
            },
            {"incident_id": "b", "decisao": "acao", "acao": "WORKLOAD_SHUTDOWN", "justificativa": "desligar"},
            {"incident_id": "c", "decisao": "encerrar", "acao": "PROCESS_STOP"},
            {"incident_id": "zzz", "decisao": "encerrar"},
            {"incident_id": "c", "decisao": "apagar_tudo"},
            "texto",
        ]
    }
    v = ia.validar_propostas(dados, lote)
    assert v["a"][:2] == (ACAO, "PROCESS_STOP") and len(v["a"][3]) == 8
    assert v["b"][0] == INVESTIGAR and v["b"][1] == "" and "não está disponível" in v["b"][2]
    assert v["c"][:2] == (ENCERRAR, "")
    assert set(v) == {"a", "b", "c"}
    assert ia.validar_propostas(["x"], lote) == {}


# ── triagem ─────────────────────────────────────────────────────────────────


def _api_falsa(monkeypatch: pytest.MonkeyPatch, abertos: list[IncidenteEdr], detalhes: dict[str, Any]) -> None:
    monkeypatch.setattr(ia.acronis_edr, "listar_abertos", lambda: abertos)

    def detalhe(iid: str, customer: str) -> IncidenteEdr:
        valor = detalhes[iid]
        if isinstance(valor, Exception):
            raise valor
        return valor

    monkeypatch.setattr(ia.acronis_edr, "detalhe", detalhe)


def test_triagem_sem_ia_grava_regras(banco, sem_ia, monkeypatch: pytest.MonkeyPatch) -> None:
    abertos = [_inc("novo", dias=1), _inc("velho", dias=50), _inc("sumiu", dias=3), _inc("fora", dias=2)]
    _api_falsa(
        monkeypatch,
        abertos,
        {
            "novo": _inc("novo", dias=1, deteccoes=[_det(True)]),
            "velho": _inc("velho", dias=50, deteccoes=[_det(False)]),
            "sumiu": IncidenteNaoEncontradoError("sumiu"),
            "fora": httpx.ConnectTimeout("lento"),
        },
    )
    r = ia.triar(_AGORA)
    assert r == {"abertos": 4, "triados": 3, "ia": False, "erro_ia": "", "obsoletas": 0}
    decisoes = {p.incident_id: (p.decisao, p.fonte) for p in PropostaIncidente.query.all()}
    assert decisoes == {"novo": (ENCERRAR, "regra"), "velho": (ENCERRAR, "regra"), "sumiu": (ENCERRAR, "regra")}


def test_triagem_com_ia_e_contexto_da_maquina(banco, com_ia, monkeypatch: pytest.MonkeyPatch) -> None:
    abertos = [_inc("rdp", maquina="NT01", categorias=("PROCESS_DETECTED",)), _inc("url", maquina="NT01")]
    _api_falsa(
        monkeypatch,
        abertos,
        {
            "rdp": _inc("rdp", maquina="NT01", categorias=("PROCESS_DETECTED",), acoes={"WORKLOAD_ISOLATE": "w"}),
            "url": _inc("url", maquina="NT01", deteccoes=[_det(False)]),
        },
    )
    lotes: list[list[dict[str, Any]]] = []

    def resposta(lote: list[dict[str, Any]]) -> dict[str, Any]:
        lotes.append(lote)
        return {
            "propostas": [
                {"incident_id": "rdp", "decisao": "acao", "acao": "WORKLOAD_ISOLATE", "justificativa": "RDP suspeito"}
            ]
        }

    monkeypatch.setattr(ia, "perguntar_ia", resposta)
    r = ia.triar(_AGORA)
    assert r["ia"] is True
    rdp = PropostaIncidente.query.filter_by(incident_id="rdp").one()
    assert (rdp.decisao, rdp.acao, rdp.fonte, rdp.acoes_oferecidas) == (
        ACAO,
        "WORKLOAD_ISOLATE",
        "ia",
        "WORKLOAD_ISOLATE",
    )
    # A IA não respondeu sobre "url": vale a regra
    assert PropostaIncidente.query.filter_by(incident_id="url").one().fonte == "regra"
    enviado = {i["incident_id"]: i for i in lotes[0]}
    assert enviado["rdp"]["outros_incidentes_abertos_na_maquina"] == 1
    assert enviado["rdp"]["acoes_oferecidas"] == ["WORKLOAD_ISOLATE"]


def test_triagem_ia_falhando_cai_nas_regras(banco, com_ia, monkeypatch: pytest.MonkeyPatch) -> None:
    _api_falsa(monkeypatch, [_inc("a")], {"a": _inc("a", deteccoes=[_det(True)])})
    monkeypatch.setattr(ia, "perguntar_ia", MagicMock(side_effect=requests.ReadTimeout("lenta")))
    r = ia.triar(_AGORA)
    assert r["ia"] is False and r["erro_ia"].startswith("ReadTimeout")
    assert PropostaIncidente.query.one().fonte == "regra"


def test_triagem_marca_obsoleta_e_reanalisa_so_o_que_mudou(banco, sem_ia, monkeypatch: pytest.MonkeyPatch) -> None:
    _api_falsa(monkeypatch, [_inc("a"), _inc("b")], {"a": _inc("a"), "b": _inc("b")})
    ia.triar(_AGORA)
    PropostaIncidente.query.filter_by(incident_id="b").one().status = REJEITADA
    db.session.commit()
    chamadas: list[str] = []
    mudou = _inc("a")
    mudou.atualizado_em = "2026-10-25T21:00:00Z"
    monkeypatch.setattr(ia.acronis_edr, "listar_abertos", lambda: [mudou, _inc("b")])
    monkeypatch.setattr(ia.acronis_edr, "detalhe", lambda iid, c: chamadas.append(iid) or _inc(iid))
    ia.triar(_AGORA)
    assert chamadas == ["a"]  # "b" já decidida não volta
    monkeypatch.setattr(ia.acronis_edr, "listar_abertos", lambda: [])
    assert ia.triar(_AGORA)["obsoletas"] == 1
    assert PropostaIncidente.query.filter_by(incident_id="a").one().status == OBSOLETA


def test_triagem_respeita_lote_por_rodada(banco, sem_ia, monkeypatch: pytest.MonkeyPatch) -> None:
    abertos = [_inc(f"i{n}") for n in range(ia.LOTE_POR_RODADA + 5)]
    _api_falsa(monkeypatch, abertos, {i.incident_id: i for i in abertos})
    assert ia.triar(_AGORA)["triados"] == ia.LOTE_POR_RODADA


# ── execução ────────────────────────────────────────────────────────────────


def _proposta(decisao: str = ENCERRAR, acao: str = "", iid: str = "a") -> PropostaIncidente:
    p = PropostaIncidente(
        incident_id=iid, customer_id="cust", maquina=f"pc-{iid}", decisao=decisao, acao=acao, justificativa="motivo"
    )
    db.session.add(p)
    db.session.commit()
    return p


def test_executar_encerrar_grava_estado_com_comentario(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[tuple] = []
    monkeypatch.setattr(ia.acronis_edr, "atualizar_estado", lambda *a: chamadas.append(a))
    p = _proposta()
    ia.executar(p, "roberto@x", _AGORA)
    assert p.status == EXECUTADA and p.decidido_por == "roberto@x"
    iid, cust, estado, comentario = chamadas[0]
    assert (iid, cust, estado) == ("a", "cust", "CLOSED")
    assert "aprovado por roberto@x" in comentario and "motivo" in comentario


def test_executar_acao_relê_detalhe_dispara_e_triagem(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    estados: list[str] = []
    monkeypatch.setattr(ia.acronis_edr, "detalhe", lambda iid, c: _inc(iid, acoes={"PROCESS_STOP": "w"}))
    monkeypatch.setattr(ia.acronis_edr, "executar_acao", lambda inc, acao, com: "act-9")
    monkeypatch.setattr(ia.acronis_edr, "atualizar_estado", lambda iid, c, estado, com: estados.append(estado))
    p = _proposta(ACAO, "PROCESS_STOP")
    ia.executar(p, "roberto@x", _AGORA)
    assert (p.status, p.activity_id, p.acao_status) == (EXECUTADA, "act-9", "QUEUED")
    assert estados == ["TRIAGED"]


def test_executar_falha_fica_registrada(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ia.acronis_edr, "detalhe", lambda iid, c: _inc(iid))  # ação não oferecida mais
    p = _proposta(ACAO, "PROCESS_STOP")
    ia.executar(p, "roberto@x", _AGORA)
    assert p.status == FALHOU and "AcaoNaoPermitidaError" in p.resultado


def test_executar_incidente_que_sumiu_conta_como_feito(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ia.acronis_edr, "atualizar_estado", MagicMock(side_effect=IncidenteNaoEncontradoError("a")))
    p = _proposta()
    ia.executar(p, "roberto@x", _AGORA)
    assert p.status == EXECUTADA and "já não existia" in p.resultado


def test_investigar_e_decidida_nao_executam(banco) -> None:
    with pytest.raises(ia.PropostaNaoExecutavelError):
        ia.executar(_proposta(INVESTIGAR, iid="i"), "x")
    feita = _proposta(iid="f")
    feita.status = EXECUTADA
    db.session.commit()
    with pytest.raises(ia.PropostaNaoExecutavelError):
        ia.executar(feita, "x")
    with pytest.raises(ia.PropostaNaoExecutavelError):
        ia.rejeitar(feita, "x")


def test_executar_lote_agrupa_por_estado_e_ignora_acao(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    lotes: list[tuple] = []

    def lote(pares: list[tuple[str, str]], estado: str, comentario: str) -> dict[str, str]:
        lotes.append((sorted(i for i, _ in pares), estado))
        return {i: ("não existe" if i == "e2" else "") for i, _ in pares}

    monkeypatch.setattr(ia.acronis_edr, "atualizar_estado_lote", lote)
    props = [
        _proposta(iid="e1"),
        _proposta(iid="e2"),
        _proposta("falso_positivo", iid="f1"),
        _proposta(ACAO, "PROCESS_STOP", iid="x1"),
    ]
    r = ia.executar_lote(props, "roberto@x", _AGORA)
    assert r == {"executadas": 2, "falhas": 1, "ignoradas": 1}
    assert lotes == [(["e1", "e2"], "CLOSED"), (["f1"], "FALSE_POSITIVE")]
    assert {p.incident_id: p.status for p in props} == {"e1": EXECUTADA, "e2": FALHOU, "f1": EXECUTADA, "x1": PROPOSTA}


def test_acompanhar_acoes_atualiza_so_as_em_andamento(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    andando = _proposta(ACAO, "PROCESS_STOP", iid="a")
    andando.status, andando.activity_id, andando.acao_status = EXECUTADA, "act-1", "QUEUED"
    pronta = _proposta(ACAO, "PROCESS_STOP", iid="b")
    pronta.status, pronta.activity_id, pronta.acao_status = EXECUTADA, "act-2", "SUCCEEDED"
    db.session.commit()
    monkeypatch.setattr(ia.acronis_edr, "status_acao", lambda iid, c, act: "STARTED")
    assert ia.acompanhar_acoes() == 1
    assert andando.acao_status == "STARTED" and pronta.acao_status == "SUCCEEDED"


def test_rodada_triagem_a_cada_30_e_acompanhamento_a_cada_5(banco, monkeypatch: pytest.MonkeyPatch) -> None:
    triagem, acomp = MagicMock(return_value={}), MagicMock(return_value=0)
    monkeypatch.setattr(ia, "triar", triagem)
    monkeypatch.setattr(ia, "acompanhar_acoes", acomp)
    for minutos in (0, 3, 6, 31):
        ia.rodada(_AGORA + timedelta(minutes=minutos))
    assert triagem.call_count == 2 and acomp.call_count == 3


def test_agendador_nao_liga_em_teste(factory_app) -> None:
    assert ia.iniciar_agendador(factory_app) is False


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
def super_client(factory_app) -> Iterator[Any]:
    yield _cliente(factory_app, "pytest-super-cyber@test.local", "admin", True)
    with factory_app.app_context():
        User.query.filter_by(email="pytest-super-cyber@test.local").one().super_admin = False
        db.session.commit()


@pytest.fixture
def gestor_client(factory_app) -> Any:
    return _cliente(factory_app, "pytest-gestor-cyber@test.local", "gestor", False)


@pytest.fixture
def sem_contexto(factory_app) -> Iterator[Any]:
    """Para testes de rota: sem contexto de app aberto (senão o "g" guarda o usuário da 1ª requisição)."""
    yield factory_app
    with factory_app.app_context():
        PropostaIncidente.query.delete()
        Solicitacao.query.delete()
        db.session.commit()


def _nova(factory_app, decisao: str = ENCERRAR, iid: str = "a") -> int:
    with factory_app.app_context():
        return _proposta(decisao, iid=iid).id


def _ler(factory_app, pid: int) -> dict[str, Any]:
    with factory_app.app_context():
        return db.session.get(PropostaIncidente, pid).para_dict()


def test_gestor_so_pede_e_super_admin_aprova_na_fila(sem_contexto, super_client, gestor_client, monkeypatch) -> None:
    app = sem_contexto
    chamadas: list[tuple] = []
    monkeypatch.setattr(acronis_edr, "atualizar_estado", lambda *a: chamadas.append(a))
    pid = _nova(app)
    resp = gestor_client.post(f"/gov/cyber/incidentes/propostas/{pid}/aprovar", json={})
    assert resp.status_code == 202 and resp.get_json()["pendente"] is True
    assert chamadas == []  # nada no Acronis sem aprovação
    with app.app_context():
        sol = Solicitacao.query.filter_by(status=PENDENTE).one()
        sol_id, resumo = sol.id, sol.resumo
    assert "pc-a" in resumo
    super_client.post(f"/gov/aprovacoes/{sol_id}/aprovar")
    p = _ler(app, pid)
    assert p["status"] == EXECUTADA
    assert p["decidido_por"] == "pytest-super-cyber@test.local (pedido de pytest-gestor-cyber@test.local)"
    assert "aprovado por pytest-super-cyber@test.local" in chamadas[0][3]


def test_super_admin_executa_direto(sem_contexto, super_client, monkeypatch) -> None:
    monkeypatch.setattr(acronis_edr, "atualizar_estado", lambda *a: None)
    pid = _nova(sem_contexto)
    resp = super_client.post(f"/gov/cyber/incidentes/propostas/{pid}/aprovar", json={})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert super_client.post(f"/gov/cyber/incidentes/propostas/{pid}/aprovar", json={}).status_code == 409
    assert super_client.post("/gov/cyber/incidentes/propostas/999999/aprovar", json={}).status_code == 404


def test_lote_e_rejeitar_pelas_rotas(sem_contexto, super_client, gestor_client, monkeypatch) -> None:
    app = sem_contexto
    monkeypatch.setattr(acronis_edr, "atualizar_estado_lote", lambda pares, e, c: {i: "" for i, _ in pares})
    ids = [_nova(app, iid="l1"), _nova(app, iid="l2")]
    resp = super_client.post("/gov/cyber/incidentes/propostas/aprovar-lote", json={"ids": ids})
    assert resp.get_json() == {"ok": True, "executadas": 2, "falhas": 0, "ignoradas": 0}
    assert super_client.post("/gov/cyber/incidentes/propostas/aprovar-lote", json={"ids": []}).status_code == 400
    outra = _nova(app, INVESTIGAR, iid="r1")
    resp = gestor_client.post(f"/gov/cyber/incidentes/propostas/{outra}/rejeitar")
    assert resp.status_code == 200 and resp.get_json()["proposta"]["status"] == REJEITADA


def test_api_painel_do_agente(banco, authed_client, sem_ia) -> None:
    _proposta(iid="p1")
    _proposta(INVESTIGAR, iid="p2")
    feita = _proposta(iid="p3")
    feita.status, feita.decidido_em = EXECUTADA, _AGORA
    db.session.commit()
    dados = authed_client.get("/api/v1/cyber/incidentes/agente").get_json()
    assert dados["por_decisao"] == {"encerrar": 1, "falso_positivo": 0, "acao": 0, "investigar": 1}
    assert {p["incident_id"] for p in dados["esperando"]} == {"p1", "p2"}
    assert [p["incident_id"] for p in dados["decididas"]] == ["p3"]


def test_pagina_cyber_tem_aba_agente_de_incidentes(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    html = authed_client.get("/gov/cyber").get_data(as_text=True)
    assert "Agente de incidentes" in html and "/api/v1/cyber/incidentes/agente" in html
