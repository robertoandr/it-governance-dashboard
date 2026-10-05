"""Compliance em PT-BR (sem HTML cru) e apps mantidos pela Microsoft fora do risco."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from itgov.services.app_registration_service import calcular_resumo_apps
from itgov.services.compliance_service import calcular_resumo_compliance, montar_tabela_controles
from itgov.services.secure_score_pt import CONTROLES_PT, resumo_pt, titulo_pt


def _iso(dias: int) -> str:
    return (datetime.now(UTC) + timedelta(days=dias)).isoformat().replace("+00:00", "Z")


def _app(nome: str, *dias: int) -> dict:
    return {"displayName": nome, "appId": nome, "keyCredentials": [{"endDateTime": _iso(d)} for d in dias]}


def test_app_do_copilot_studio_nao_conta_como_vencido() -> None:
    resumo = calcular_resumo_apps(
        [
            _app("Customer Service Copilot Bot (Microsoft Copilot Studio)", -400, -370),
            _app("ConnectSyncProvisioning_SRV-FS_9b1ca12ca9b4", -32),
            _app("App interno", 10),
            _app("App em dia", 200),
        ]
    )
    assert resumo.secrets_expirados == 1
    assert resumo.secrets_expirando_30d == 1
    assert resumo.gerenciados_microsoft == 2
    assert [c.app_display_name for c in resumo.expirando if not c.gerenciado_microsoft] == [
        "ConnectSyncProvisioning_SRV-FS_9b1ca12ca9b4",
        "App interno",
    ]


def test_titulo_pt_e_fallback_para_o_original() -> None:
    assert titulo_pt("mdo_safelinksforemail") == "Safe Links no e-mail"
    assert titulo_pt("controle_novo", "New control") == "New control"
    assert titulo_pt("controle_novo") == "controle_novo"


def test_resumo_sem_traducao_vira_texto_puro_cortado() -> None:
    texto = resumo_pt("controle_novo", "Linha 1<br/><br/>Linha&nbsp;2 &amp; <i>mais</i>" + " palavra" * 80)
    assert "<" not in texto and "&nbsp;" not in texto
    assert texto.startswith("Linha 1 Linha 2 & mais")
    assert len(texto) <= 220 and texto.endswith("…")


def test_recomendacoes_e_tabela_saem_em_portugues() -> None:
    score = {
        "currentScore": 10.0,
        "maxScore": 20.0,
        "controlScores": [
            {"controlName": "meeting_restrictanonymousjoin_v1", "controlCategory": "Apps", "scoreInPercentage": 0.0,
             "description": "By restricting anonymous users<br/>..."},
        ],
    }  # fmt: skip
    resumo = calcular_resumo_compliance(score, [])
    rec = resumo.recomendacoes[0]
    assert rec.titulo == "Restringir anônimos nas reuniões"
    assert "<br/>" not in rec.descricao
    tabela = montar_tabela_controles(
        [{"id": "meeting_restrictanonymousjoin_v1", "title": "Restrict anonymous join", "maxScore": 1}],
        score["controlScores"],
    )
    assert tabela[0].title == "Restringir anônimos nas reuniões"


def test_todos_os_controles_traduzidos_tem_titulo_e_resumo() -> None:
    for nome, (titulo, resumo) in CONTROLES_PT.items():
        assert titulo and resumo, nome


def test_pagina_apps_separa_os_da_microsoft(authed_client) -> None:
    from unittest.mock import patch

    resumo = calcular_resumo_apps(
        [
            _app("Customer Service Copilot Bot (Microsoft Copilot Studio)", -400),
            _app("ConnectSyncProvisioning_SRV-FS_9b1ca12ca9b4", -32),
        ]
    ).model_dump(mode="json")
    with (
        patch("app.views.dashboards.graph_configured", return_value=True),
        patch("itgov.api.v1.governance_apps.get_cached_app_summary", return_value=resumo),
        patch("itgov.services.m365_uso.obter_uso", return_value=None),
    ):
        html = authed_client.get("/gov/governance/apps").get_data(as_text=True)
    assert "ConnectSyncProvisioning_SRV-FS_9b1ca12ca9b4" in html
    assert "vencido há 3" in html
    assert "Apps mantidos pela Microsoft (1 credencial(is))" in html
