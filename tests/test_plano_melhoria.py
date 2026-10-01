"""Plano de melhoria dos pilares: o que fazer para subir o score."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask, url_for

from app.services import plano_melhoria as pm
from app.services.plano_melhoria import ItemConcreto, montar_plano


def _comp(cid: str, value: float, weight: float, source: str = "zabbix") -> dict:
    return {"id": cid, "label": cid.replace("_", " "), "value": value, "weight": weight, "source": source}


PILAR = {
    "id": "risk_management",
    "components": [
        _comp("incidents_critical", 10.0, 1.5),
        _comp("mfa_adoption", 62.0, 2.0, "entra_id"),
        _comp("backup_coverage", 100.0, 2.0, "acronis"),  # já na meta
        _comp("critical_vulns", 58.0, 3.0, "coming_soon"),  # fora do cálculo
    ],
}


def test_ganho_segue_a_media_ponderada_dos_componentes_reais() -> None:
    plano = montar_plano(PILAR, com_itens=False)
    peso_real = 1.5 + 2.0 + 2.0  # coming_soon fica fora, como no ScoreCalculator
    ganhos = {a.componente_id: a.ganho for a in plano}
    assert ganhos["incidents_critical"] == round(1.5 * (100 - 10) / peso_real, 1)
    assert ganhos["mfa_adoption"] == round(2.0 * (100 - 62) / peso_real, 1)


def test_ignora_componente_na_meta_e_sem_coleta() -> None:
    ids = [a.componente_id for a in montar_plano(PILAR, com_itens=False)]
    assert "backup_coverage" not in ids
    assert "critical_vulns" not in ids


def test_ordena_do_maior_ganho_para_o_menor() -> None:
    ganhos = [a.ganho for a in montar_plano(PILAR, com_itens=False)]
    assert ganhos == sorted(ganhos, reverse=True)
    assert montar_plano(PILAR, com_itens=False)[0].componente_id == "incidents_critical"


def test_componente_sem_regra_nao_entra() -> None:
    pilar = {"components": [_comp("componente_novo", 10.0, 1.0)]}
    assert montar_plano(pilar) == []


def test_pilar_sem_componentes_reais() -> None:
    assert montar_plano({"components": [_comp("mttr", 50.0, 1.0, "coming_soon")]}) == []


def test_fonte_fora_do_ar_nao_derruba_o_plano() -> None:
    regra = pm.REGRAS["incidents_critical"]
    quebrada = pm.Regra(regra.meta, regra.o_que_fazer, regra.onde_label, regra.onde_endpoint, itens=lambda: 1 / 0)
    with patch.dict(pm.REGRAS, {"incidents_critical": quebrada}):
        acao = montar_plano(PILAR)[0]
    assert acao.componente_id == "incidents_critical" and acao.itens == []


def test_itens_do_zabbix_mais_graves_primeiro() -> None:
    problemas = {
        "problems": [
            {
                "host": "cam-1",
                "severity": 2,
                "severity_label": "Aviso",
                "name": "ICMP Ping: High ICMP ping loss",
                "since_iso": "2026-09-01",
            },
            {
                "host": "sw-core",
                "severity": 4,
                "severity_label": "Alto",
                "name": "ICMP Ping: Unavailable by ICMP ping",
                "since_iso": "2026-09-20",
                "zabbix_url": "http://z/1",
            },
            {"host": "fw", "severity": 5, "severity_label": "Desastre", "name": "Link down", "since_iso": "2026-09-25"},
        ]
    }
    with patch("itgov.api.v1.zabbix_triggers.get_cached_triggers", return_value=problemas):
        graves = pm._problemas_zabbix(sev_min=4)()
        indisponiveis = pm._problemas_zabbix(so_indisponivel=True)()
    assert [i.titulo for i in graves] == ["fw", "sw-core"]
    assert [i.titulo for i in indisponiveis] == ["sw-core"]
    assert indisponiveis[0].url == "http://z/1"


def test_controles_do_secure_score_por_pontos_que_faltam() -> None:
    resumo = {
        "controles": [
            {
                "title": "A",
                "status": "pendente",
                "max_score": 10,
                "score": 9,
                "categoria": "Identity",
                "action_url": "u1",
            },
            {"title": "B", "status": "pendente", "max_score": 8, "score": 0, "categoria": "Apps", "action_url": "u2"},
            {"title": "C", "status": "implementado", "max_score": 20, "score": 20},
        ]
    }
    with patch("itgov.api.v1.governance_compliance.get_cached_compliance_summary", return_value=resumo):
        itens = pm._controles_secure_score()
    assert [i.titulo for i in itens] == ["B", "A"]
    assert itens[0].detalhe.startswith("+8.0 pts")


def test_chamados_e_projetos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "acme")
    sla = {
        "oldest": [
            {"id": 7, "subject": "Impressora", "age_str": "3d 2h", "priority": "high", "breached": True},
            {"id": 8, "subject": "Ok", "breached": False},
        ]
    }
    pmo = {
        "ativos": [
            {"name": "Gsurf", "overdue": True, "due_date": "31/07/2026", "assignees": ["Allan"], "url": "c1"},
            {"name": "Em dia", "overdue": False},
        ]
    }
    with patch("itgov.api.v1.zendesk.get_cached_sla_detail", return_value=sla):
        chamados = pm._chamados_fora_do_sla()
    with patch("itgov.api.v1.pmo_clickup.get_cached_pmo", return_value=pmo):
        projetos = pm._projetos_atrasados()
    assert [c.url for c in chamados] == ["https://acme.zendesk.com/agent/tickets/7"]
    assert [p.titulo for p in projetos] == ["Gsurf"]


def test_endpoints_das_regras_existem(factory_app: Flask) -> None:
    with factory_app.test_request_context():
        for cid, regra in pm.REGRAS.items():
            assert regra.onde_endpoint or regra.onde_url, cid
            if regra.onde_endpoint:
                assert url_for(regra.onde_endpoint)


def test_detalhe_do_pilar_mostra_plano(authed_client) -> None:
    acao = pm.AcaoMelhoria(
        componente_id="incidents_critical",
        componente="Incidentes críticos no mês",
        valor=10,
        meta=100,
        ganho=20.8,
        o_que_fazer="Resolver os problemas Alta/Desastre.",
        onde_label="Triggers",
        onde_endpoint="dashboards.zabbix_triggers",
        itens=[ItemConcreto(titulo="sw-core", detalhe="Alto · desde 20/09", url="http://z/1")],
    )
    externo = pm.AcaoMelhoria(
        componente_id="conditional_access",
        componente="Acesso Condicional",
        valor=0,
        meta=100,
        ganho=5.0,
        o_que_fazer="Criar políticas.",
        onde_label="Entra ID",
        onde_url=pm.ENTRA_CA_URL,
    )
    with patch("app.services.plano_melhoria.montar_plano", return_value=[acao, externo]):
        html = authed_client.get("/gov/pillars/risk_management").get_data(as_text=True)
    assert 'id="plano"' in html
    assert "+20.8 pts no pilar" in html
    assert "até +25.8 pts" in html
    assert 'href="http://z/1"' in html and "sw-core" in html
    assert 'href="/gov/triggers"' in html and "Onde agir: Triggers" in html
    assert pm.ENTRA_CA_URL in html


def test_detalhe_sem_acoes(authed_client) -> None:
    with patch("app.services.plano_melhoria.montar_plano", return_value=[]):
        html = authed_client.get("/gov/pillars/performance_measure").get_data(as_text=True)
    assert "Nada a fazer agora" in html


def test_lista_de_pilares_mostra_proxima_acao(authed_client) -> None:
    acao = pm.AcaoMelhoria(
        componente_id="mfa_adoption",
        componente="Adoção de MFA",
        valor=62,
        meta=100,
        ganho=13.8,
        o_que_fazer="Exigir MFA de todos.",
        onde_label="M365",
        onde_endpoint="dashboards.m365_overview",
    )
    with patch("app.services.plano_melhoria.montar_plano", return_value=[acao]):
        html = authed_client.get("/gov/pillars").get_data(as_text=True)
    assert "Próxima ação · +13.8 pts" in html
    assert "Adoção de MFA: Exigir MFA de todos." in html
