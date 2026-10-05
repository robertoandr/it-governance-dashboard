"""Catálogo de relatórios (Conferência V2.0 rl1)."""

from __future__ import annotations

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
