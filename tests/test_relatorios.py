"""Catálogo de relatórios (Conferência V2.0 rl1)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services import relatorios as rl


def _secao_ok() -> rl.Secao:
    return rl.Secao(
        chave="sla",
        titulo="SLA de atendimento",
        fonte="Zendesk",
        kpis=[rl.Kpi(rotulo="SLA cumprido", valor="90,0%", tom="ok")],
        tabelas=[rl.Tabela(titulo="Por prioridade", colunas=["Prioridade", "Chamados"], linhas=[["Alta", "3"]])],
    )


def test_formatacao_em_portugues() -> None:
    assert rl._pct(45.23) == "45,2%" and rl._pct(None) == "—"
    assert rl._brl(17149.7) == "R$ 17.149,70"
    assert rl._int("1234") == "1.234"
    assert rl._tom_pct(99, 98, 90) == "ok" and rl._tom_pct(91, 98, 90) == "alerta" and rl._tom_pct(80, 98, 90) == "ruim"


def test_secao_que_falha_vem_com_o_motivo() -> None:
    def _quebra() -> rl.Secao:
        raise RuntimeError("Zendesk fora")

    with patch.dict(rl.SECOES, {"sla": ("SLA de atendimento", _quebra)}):
        s = rl.montar_secao("sla")
    assert s.erro and "Zendesk fora" in s.erro


def test_personalizado_segue_a_ordem_do_catalogo_e_ignora_desconhecidas() -> None:
    with patch.object(rl, "montar_secao", lambda chave: rl.Secao(chave=chave, titulo=chave, fonte="")):
        r = rl.montar_relatorio("personalizado", ["sla", "inexistente", "links"])
    assert [s.chave for s in r.secoes] == ["links", "sla"]


def test_csv_tem_indicadores_e_tabelas() -> None:
    with patch.object(rl, "montar_secao", lambda _c: _secao_ok()):
        texto = rl.gerar_csv(rl.montar_relatorio("service-desk"))
    assert "SLA cumprido;90,0%" in texto and "Prioridade;Chamados" in texto and "Alta;3" in texto


def test_pagina_catalogo(authed_client) -> None:
    html = authed_client.get("/gov/relatorios").get_data(as_text=True)
    assert "Relatório personalizado" in html and "Service desk e SLA" in html


def test_pagina_relatorio_e_csv(authed_client) -> None:
    with patch.object(rl, "montar_secao", lambda _c: _secao_ok()):
        html = authed_client.get("/gov/relatorios/service-desk").get_data(as_text=True)
        csv_resp = authed_client.get("/gov/relatorios/service-desk?formato=csv")
    assert "SLA de atendimento" in html and "data-imprimir" in html and "onclick" not in html
    assert csv_resp.mimetype == "text/csv" and "attachment" in csv_resp.headers["Content-Disposition"]


def test_relatorio_desconhecido_e_personalizado_vazio(authed_client) -> None:
    assert authed_client.get("/gov/relatorios/nao-existe").status_code == 404
    assert authed_client.get("/gov/relatorios/personalizado").status_code == 302


def _valores(s: rl.Secao) -> dict[str, str]:
    assert s.erro is None
    return {k.rotulo: k.valor for k in s.kpis}


def test_secao_disponibilidade_ordena_por_severidade() -> None:
    triggers = {
        "total": 2,
        "counts": {"high": 1, "warning": 1, "info": 0},
        "problems": [
            {"severity": 2, "severity_label": "Atenção", "host": "sw1", "name": "CPU"},
            {"severity": 4, "severity_label": "Alta", "host": "fw1", "name": "Link", "acknowledged": True},
        ],
    }
    with (
        patch("itgov.api.v1.zabbix_monitoring.get_cached_zabbix_summary", return_value={"uptime_pct": 99.5}),
        patch("itgov.api.v1.zabbix_triggers.get_cached_triggers", return_value=triggers),
    ):
        s = rl.secao_disponibilidade()
    assert _valores(s)["Disponibilidade"] == "99,5%" and s.kpis[0].tom == "ok"
    assert s.kpis[3].tom == "ruim" and s.kpis[3].detalhe == "high: 1, warning: 1"
    assert [linha[1] for linha in s.tabelas[0].linhas] == ["fw1", "sw1"] and s.tabelas[0].linhas[0][4] == "Sim"


def test_secao_links_conta_wans_e_caminhos_fora() -> None:
    fortigates = [
        {
            "unidade": "Sede",
            "api_up": True,
            "wans": [{"operadora": "Claro", "iface": "wan1", "link": True, "speed_mbps": 1000}, {"iface": "wan2"}],
            "sdwan": [
                {
                    "sla": "Ping",
                    "members": [
                        {"label": "wan1", "latency_ms": 12.34, "status": "up"},
                        {"label": "wan2", "status": "down"},
                    ],
                }
            ],
        },
        {"unidade": "Loja", "api_up": False},
    ]
    with patch("itgov.services.fortigate_api.get_cached_fortigates", return_value=fortigates):
        s = rl.secao_links()
    v = _valores(s)
    assert v == {"FortiGates lidos": "1 de 2", "WANs sem link": "1", "Caminhos SD-WAN fora": "1"}
    assert s.tabelas[0].linhas[0][3:5] == ["Ativo", "1.000"] and s.tabelas[0].linhas[1][3] == "Sem link"
    assert s.tabelas[1].linhas[0][3] == "12,3 ms"


def test_secao_cftv_offline_e_ranking_de_quedas() -> None:
    devices = [
        {"host": "cam1", "name": "Câmera 1", "status": "up"},
        {"host": "cam2", "name": "Câmera 2", "status": "down", "loja": "L10", "ip": "10.0.0.2"},
    ]
    quedas = {"cam1": {"quedas": 1, "segundos": 60}, "cam2": {"quedas": 5, "segundos": 10, "em_aberto": True}}
    with (
        patch("itgov.api.v1.cftv_monitoring.get_cached_cftv_summary", return_value={"devices": devices}),
        patch("itgov.api.v1.cftv_monitoring.get_cached_historico_quedas", return_value=quedas),
    ):
        s = rl.secao_cftv()
    assert _valores(s) == {"Dispositivos": "2", "Online": "50,0%", "Offline agora": "1"}
    assert s.tabelas[0].linhas == [["Câmera 2", "L10", "10.0.0.2", ""]]
    assert [linha[0] for linha in s.tabelas[1].linhas] == ["Câmera 2", "Câmera 1"]


def test_secao_sla_e_volume() -> None:
    sla = {
        "period": {"window_days": 30, "compliance_pct": 75, "avg_resolution_hours": 12.4},
        "by_priority": {"high": {"count": 4, "ok": 3, "breached": 1, "compliance_pct": 75}},
        "age_buckets": {"0-7 dias": 3},
        "oldest": [{"id": 9, "subject": "Impressora", "breached": True}],
        "total_open": 3,
        "backlog_breached": 1,
    }
    hist = {
        "total": 120,
        "media_mensal": 40,
        "meses": [{"rotulo": "set/26", "abertos": 40, "resolvidos": 38}],
        "por_responsavel": [{"nome": "Ana", "total": 10}],
        "por_solicitante": [{"nome": "Bia", "total": 5}],
    }
    with (
        patch("itgov.api.v1.zendesk.get_cached_sla_detail", return_value=sla),
        patch("itgov.api.v1.zendesk.get_cached_historico", return_value=hist),
    ):
        s, vol = rl.secao_sla(), rl.secao_volume()
    assert _valores(s)["SLA cumprido"] == "75,0%" and s.kpis[0].tom == "alerta"
    assert _valores(s)["Resolução média"] == "12 h"
    assert s.tabelas[0].linhas[0][:4] == ["Alta", "4", "3", "1"] and s.tabelas[2].linhas[0][5] == "Sim"
    assert _valores(vol) == {"Chamados no período": "120", "Média mensal": "40"}
    assert vol.tabelas[1].linhas[0][0] == "Ana" and vol.tabelas[2].linhas[0][0] == "Bia"


def test_secao_seguranca() -> None:
    sc = {
        "pct": 55,
        "current_score": 300,
        "max_score": 545,
        "variacao_30d": -1.25,
        "category_breakdown": {"Identidade": 60},
        "recomendacoes": [{"control_name": "MFA", "score_pct": 10}],
    }
    alertas = {"total_open": 0, "high": 0}
    ac = {
        "protected_pct": 95,
        "incidents_not_mitigated": 2,
        "incidentes": [{"resource_name": "PC1", "time": datetime(2026, 10, 1, 12, 0, tzinfo=rl.TZ)}],
        "offline_gt30": [{"name": "PC2", "offline_days": 40}],
        "sem_plano": [{"name": "PC3"}],
    }
    with (
        patch("itgov.api.v1.governance_compliance.get_cached_compliance_summary", return_value=sc),
        patch("itgov.api.v1.governance_security_alerts.get_cached_security_alerts_summary", return_value=alertas),
        patch("itgov.api.v1.acronis_backup.get_cached_acronis_summary", return_value=ac),
    ):
        score, ameacas = rl.secao_secure_score(), rl.secao_ameacas()
    assert _valores(score)["Variação em 30 dias"] == "-1,2 p.p." and score.kpis[1].tom == "alerta"
    assert score.kpis[0].tom == "alerta" and score.tabelas[1].linhas[0][0] == "MFA"
    assert ameacas.kpis[0].tom == "ok" and ameacas.kpis[1].tom == "alerta" and ameacas.kpis[2].tom == "ruim"
    assert ameacas.tabelas[1].linhas[0][4] == "01/10/2026 12:00" and ameacas.tabelas[3].linhas == [["PC3"]]


def test_secao_identidade_email() -> None:
    dns = {
        "spf": {"found": True, "record": "v=spf1 -all"},
        "dmarc": {"found": True, "policy": "reject"},
        "dkim": {"found": False, "selector": "s1"},
    }
    with (
        patch("itgov.api.v1.governance_mfa._obter_dados", return_value={"mfa_enabled_pct": 97}),
        patch("itgov.services.dns_check_service._get_domain", return_value="exemplo.com.br"),
        patch("itgov.services.dns_check_service.get_email_security_summary", return_value=dns),
    ):
        s = rl.secao_identidade_email()
    assert _valores(s) == {"Usuários com MFA": "97,0%", "Domínio": "exemplo.com.br"}
    assert s.tabelas[0].linhas == [
        ["SPF", "Configurado", "v=spf1 -all"],
        ["DKIM", "Ausente", "seletor s1"],
        ["DMARC", "Configurado", "política reject"],
    ]


def test_secao_licencas_custos_e_renovacoes() -> None:
    dados = {
        "summary": {"custo_mensal_total_brl": 1500.5, "desperdicio_total_brl": 0, "uso_pct": 90},
        "licenses": [
            {"friendly_name": "E3", "custo_mensal_brl": 1000, "renovacao_dias": 30, "renewal_date": "2026-11-04"},
            {"friendly_name": "Basic", "custo_mensal_brl": 500.5, "renovacao_dias": 200, "renewal_date": "x"},
            {"friendly_name": "Grátis", "is_free": True},
        ],
    }
    with patch("itgov.api.v1.m365_licenses.get_licenses_summary", return_value=dados):
        s = rl.secao_licencas()
    v = _valores(s)
    assert v["Custo mensal"] == "R$ 1.500,50" and s.kpis[1].tom == "ok" and v["Renovam em 90 dias"] == "1"
    assert [linha[0] for linha in s.tabelas[0].linhas] == ["E3", "Basic"]
    assert s.tabelas[0].linhas[0][7] == "04/11/2026" and s.tabelas[0].linhas[1][7] == "x"
    assert s.tabelas[1].linhas == [["E3", "04/11/2026", "30", "R$ 1.000,00"]]


def test_secao_uso_apps_e_falha_do_graph() -> None:
    item = SimpleNamespace(nome="Teams", usuarios=80, pct=80.0)
    uso = SimpleNamespace(
        pct_ativas=85.0,
        ativas=85,
        contas=100,
        servicos=[item],
        apps=[item],
        colunas_historico=["Teams"],
        meses=[SimpleNamespace(rotulo="set/26", medias={"Teams": 70})],
    )
    with patch("itgov.services.m365_uso.obter_uso", return_value=uso):
        s = rl.secao_uso_apps()
    assert s.kpis[0].valor == "85,0%" and s.kpis[0].detalhe == "85 de 100 contas"
    assert s.tabelas[2].linhas == [["set/26", "70"]]
    with patch("itgov.services.m365_uso.obter_uso", return_value=None):
        falha = rl.montar_secao("uso-apps")
    assert falha.erro and "não responderam" in falha.erro
    assert "não responderam" in rl.gerar_csv(
        rl.Relatorio(chave="x", titulo="x", descricao="", gerado_em=datetime.now(rl.TZ), secoes=[falha])
    )


def test_formatacao_de_valores_vazios() -> None:
    assert rl._brl(None) == rl._ms("x") == rl._data(None) == rl._data_hora(None) == "—"
    assert rl._ms(3.25) == "3,2 ms" and rl._tom_pct(None, 1, 0) == rl._tom_qtd(None) == "neutro"


def test_pagina_personalizado_com_secoes(authed_client) -> None:
    with patch.object(rl, "montar_secao", lambda _c: _secao_ok()):
        html = authed_client.get("/gov/relatorios/personalizado?secao=sla&secao=links").get_data(as_text=True)
    assert "Relatório personalizado" in html and "SLA de atendimento" in html


def test_pagina_executivo(authed_client) -> None:
    resp = authed_client.get("/gov/relatorios/executivo")
    assert resp.status_code == 200
