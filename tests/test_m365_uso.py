"""Uso dos apps e serviços M365 a partir dos relatórios CSV do Graph (a1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from itgov.services import m365_uso as mu
from itgov.services.m365_uso import ler_csv, resumir_apps, resumir_historico, resumir_servicos

_SERVICOS = (
    "﻿Report Refresh Date,Exchange Active,Exchange Inactive,OneDrive Active,OneDrive Inactive,"
    "SharePoint Active,SharePoint Inactive,Skype For Business Active,Skype For Business Inactive,"
    "Yammer Active,Yammer Inactive,Teams Active,Teams Inactive,Office 365 Active,Office 365 Inactive,Report Period\n"
    "2026-09-29,280,31,266,42,263,44,,,0,364,247,61,285,81,30\n"
)

_APPS = (
    "﻿Report Refresh Date,User Principal Name,Windows,Mac,Mobile,Web,Outlook,Word,Excel,PowerPoint,OneNote,Teams\n"
    "2026-09-28,A1,Yes,No,No,Yes,Yes,Yes,Yes,No,No,Yes\n"
    "2026-09-28,B2,No,No,No,Yes,Yes,No,Yes,No,No,No\n"
    "2026-09-28,C3,Yes,No,Yes,No,Yes,Yes,No,No,No,Yes\n"
    "2026-09-28,D4,No,No,No,No,No,No,No,No,No,No\n"
)

_HIST_CAB = "﻿Report Refresh Date,Office 365,Exchange,OneDrive,SharePoint,Skype For Business,Yammer,Teams,Report Date,Report Period\n"


def _hist(*linhas: str) -> list[dict[str, str]]:
    return ler_csv(_HIST_CAB + "".join(f"2026-10-01,{linha},180\n" for linha in linhas))


_SETEMBRO = _hist(*[f"200,190,100,120,,,110,2026-09-0{d}" for d in (2, 3, 4)])


def test_servicos_percentual_sobre_ativos_mais_inativos() -> None:
    itens, contas, ativas, atualizado = resumir_servicos(ler_csv(_SERVICOS))
    por_nome = {i.nome: i for i in itens}
    assert por_nome["E-mail (Exchange)"].usuarios == 280
    assert por_nome["E-mail (Exchange)"].total == 311
    assert por_nome["E-mail (Exchange)"].pct == 90.0
    assert por_nome["Viva Engage (Yammer)"].pct == 0.0  # licenciado e sem uso aparece
    assert (contas, ativas, atualizado) == (366, 285, "2026-09-29")


def test_servico_sem_contas_fica_de_fora() -> None:
    # Skype for Business vem vazio (descontinuado) e não está na lista
    itens, *_ = resumir_servicos(ler_csv(_SERVICOS))
    assert all("Skype" not in i.nome for i in itens)
    assert resumir_servicos([]) == ([], 0, 0, "")


def test_apps_e_plataformas_contados_do_detalhe_por_conta() -> None:
    apps, plataformas = resumir_apps(ler_csv(_APPS))
    por_app = {i.nome: i for i in apps}
    assert por_app["Outlook"].usuarios == 3
    assert por_app["Outlook"].total == 4
    assert por_app["Outlook"].pct == 75.0
    assert por_app["PowerPoint"].usuarios == 0
    assert apps[0].nome == "Outlook"  # mais usado primeiro
    por_plat = {i.nome: i.usuarios for i in plataformas}
    assert por_plat == {"Windows": 2, "Navegador": 2, "Celular": 1, "Mac": 0}


def test_apps_sem_linhas_nao_divide_por_zero() -> None:
    apps, _ = resumir_apps([])
    assert all(i.pct == 0.0 for i in apps)


def test_historico_media_por_dia_util_sem_fim_de_semana() -> None:
    meses = resumir_historico(
        _hist(
            "230,215,121,150,,,129,2026-08-31",
            "200,190,100,120,,,110,2026-09-04",  # sexta
            "40,20,,,,,10,2026-09-05",  # sábado — fora da média
            "220,210,120,140,,,130,2026-09-07",  # segunda
            "210,200,110,130,,,120,2026-09-08",
        )
    )
    assert [m.rotulo for m in meses] == ["ago/2026", "set/2026"]
    setembro = meses[1]
    assert setembro.dias == 3
    assert setembro.medias["Office 365"] == 210.0
    assert setembro.medias["Teams"] == 120.0
    assert "Yammer" not in setembro.medias  # coluna vazia não vira zero


def test_historico_marca_primeiro_e_ultimo_mes_como_parciais() -> None:
    meses = resumir_historico(
        _hist(
            "1,1,1,1,,,1,2026-07-31",
            "1,1,1,1,,,1,2026-08-03",
            *[f"1,1,1,1,,,1,2026-09-0{d}" for d in (1, 2, 3)],
            "x,x,x,x,,,x,data-ruim",
        )
    )
    assert [m.parcial for m in meses] == [True, False, True]


def test_historico_descarta_mes_atual_com_poucos_dias() -> None:
    linhas = [f"1,1,1,1,,,1,2026-09-{d:02d}" for d in (1, 2, 3, 4, 7, 8)] + ["1,,1,1,,,1,2026-10-01"]
    meses = resumir_historico(_hist(*linhas))
    assert [m.rotulo for m in meses] == ["set/2026"]


def test_buscar_monta_o_resumo_dos_tres_relatorios() -> None:
    csvs = {
        mu._URL_SERVICOS: ler_csv(_SERVICOS),
        mu._URL_APPS: ler_csv(_APPS),
        mu._URL_HISTORICO: _SETEMBRO,
    }

    async def baixar(_client: httpx.AsyncClient, _token: str, url: str) -> list[dict[str, str]]:
        return csvs[url]

    with (
        patch.object(mu, "_fetch_token", AsyncMock(return_value="tok")),
        patch.object(mu, "_baixar", side_effect=baixar),
    ):
        uso = mu._carregar()
    assert uso is not None
    assert uso.contas == 366 and uso.pct_ativas == 77.9
    assert uso.contas_apps == 4
    assert uso.colunas_historico == ["Office 365", "Exchange", "OneDrive", "SharePoint", "Teams"]


def test_falha_do_graph_vira_none() -> None:
    with patch.object(mu, "_fetch_token", AsyncMock(side_effect=httpx.ConnectError("fora"))):
        assert mu._carregar() is None


def test_pagina_m365_mostra_o_uso_dos_apps(authed_client) -> None:
    from tests.test_m365_governanca import _painel

    with (
        patch("itgov.services.m365_uso.CacheSWR.get", lambda _self, carregar: carregar()),
        patch.object(mu, "_fetch_token", AsyncMock(return_value="tok")),
        patch.object(
            mu,
            "_baixar",
            side_effect=lambda _c, _t, url: {
                mu._URL_SERVICOS: ler_csv(_SERVICOS),
                mu._URL_APPS: ler_csv(_APPS),
                mu._URL_HISTORICO: _SETEMBRO,
            }[url],
        ),
        patch("app.views.dashboards.graph_configured", return_value=True),
        patch("app.services.m365_governanca.obter_painel", return_value=_painel()),
        patch(
            "itgov.api.v1.m365_licenses.get_licenses_summary",
            return_value={"has_data": False, "summary": {"uso_pct": 0}, "licenses": []},
        ),
        patch("itgov.api.v1.zabbix_triggers.get_cached_triggers", return_value={}),
        patch("itgov.api.v1.governance_security_alerts.get_cached_security_alerts_summary", return_value={}),
        patch("app.services.influxdb_provider.InfluxDBMetricsProvider._query", return_value=[]),
    ):
        resp = authed_client.get("/gov/m365")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Uso dos apps — últimos 30 dias" in html
    assert "285 de 366 contas" in html
    assert "set/2026" in html


def test_pagina_m365_sem_graph_nao_mostra_o_bloco(authed_client) -> None:
    from tests.test_m365_governanca import _painel

    with (
        patch("app.views.dashboards.graph_configured", return_value=False),
        patch("app.services.m365_governanca.obter_painel", return_value=_painel()),
        patch(
            "itgov.api.v1.m365_licenses.get_licenses_summary",
            return_value={"has_data": False, "summary": {"uso_pct": 0}, "licenses": []},
        ),
        patch("itgov.api.v1.zabbix_triggers.get_cached_triggers", return_value={}),
        patch("itgov.api.v1.governance_security_alerts.get_cached_security_alerts_summary", return_value={}),
        patch("app.services.influxdb_provider.InfluxDBMetricsProvider._query", return_value=[]),
    ):
        resp = authed_client.get("/gov/m365")
    assert resp.status_code == 200
    assert "Uso dos apps" not in resp.get_data(as_text=True)


def test_pagina_aplicativos_mostra_o_uso_dos_apps(authed_client) -> None:
    resumo = {"total_apps": 0, "secrets_expirando_30d": 0, "secrets_expirados": 0, "expirando": []}
    with (
        patch("itgov.services.m365_uso.CacheSWR.get", lambda _self, carregar: carregar()),
        patch.object(mu, "_fetch_token", AsyncMock(return_value="tok")),
        patch.object(
            mu,
            "_baixar",
            side_effect=lambda _c, _t, url: {
                mu._URL_SERVICOS: ler_csv(_SERVICOS),
                mu._URL_APPS: ler_csv(_APPS),
                mu._URL_HISTORICO: _SETEMBRO,
            }[url],
        ),
        patch("app.views.dashboards.graph_configured", return_value=True),
        patch("itgov.api.v1.governance_apps.get_cached_app_summary", return_value=resumo),
    ):
        resp = authed_client.get("/gov/governance/apps")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Uso dos apps — últimos 30 dias" in html
    assert "285 de 366 contas" in html


def test_pagina_aplicativos_avisa_quando_o_graph_nao_responde(authed_client) -> None:
    resumo = {"total_apps": 0, "secrets_expirando_30d": 0, "secrets_expirados": 0, "expirando": []}
    with (
        patch("itgov.services.m365_uso.obter_uso", return_value=None),
        patch("app.views.dashboards.graph_configured", return_value=True),
        patch("itgov.api.v1.governance_apps.get_cached_app_summary", return_value=resumo),
    ):
        html = authed_client.get("/gov/governance/apps").get_data(as_text=True)
    assert "não responderam agora" in html


def test_pagina_aplicativos_503_quando_o_resumo_falha(authed_client) -> None:
    with (
        patch("app.views.dashboards.graph_configured", return_value=True),
        patch("itgov.api.v1.governance_apps.get_cached_app_summary", side_effect=RuntimeError("graph fora")),
    ):
        assert authed_client.get("/gov/governance/apps").status_code == 503
