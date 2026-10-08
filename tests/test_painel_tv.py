"""Testes do painel de TV (/gov/v1 … /gov/v6 e /gov/painel/dados)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import painel_tv as ptv

_PAINEL = {
    "gerado_em": "2026-10-07T13:56:20-03:00",
    "hora": "13:56",
    "data": "qua, 07 out",
    "score": {"valor": 60.7, "faixa": "warn", "tendencia": "stable"},
    "pilares": [{"nome": "Gestão de Riscos", "score": 50.2, "faixa": "crit"}],
    "zabbix": {"disponibilidade": 94.7, "hosts_total": 283, "hosts_fora": 15, "problemas": {"total": 1, "alto": 1}},
    "unidades": [{"nome": "Sede <Centro>", "ativos": 120, "falhas": 8, "faixa": "crit"}],
    "alertas": [
        {
            "sev": 4,
            "sev_rotulo": "ALTO",
            "host": "<script>alert(1)</script>",
            "problema": "sem ping",
            "unidade": "Sede",
            "desde": "2026-10-07T11:58:57-03:00",
            "reconhecido": False,
        }
    ],
    "sla": None,
    "secure_score": None,
    "licencas": None,
    "dispositivos": None,
    "links": None,
    "dispo_7d": [],
    "fontes": [],
    "erros": ["zendesk"],
}


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, "none"), (100, "ok"), (85.1, "ok"), (85, "warn"), (60, "warn"), (59.9, "crit")],
)
def test_faixa_segue_85_e_60(valor: float | None, esperado: str) -> None:
    assert ptv.faixa(valor) == esperado


@pytest.mark.parametrize(
    ("falhas", "total", "esperado"), [(0, 0, "none"), (0, 10, "ok"), (3, 41, "warn"), (6, 115, "crit")]
)
def test_faixa_da_unidade_pela_quantidade_de_falhas(falhas: int, total: int, esperado: str) -> None:
    assert ptv.faixa_unidade(falhas, total) == esperado


def test_fonte_fora_do_ar_nao_derruba_o_painel(factory_app) -> None:
    def quebra() -> dict:
        raise RuntimeError("fora do ar")

    nomes = ["_governanca", "_zabbix", "_unidades_e_hosts", "_sla", "_secure_score", "_licencas"]
    nomes += ["_dispositivos", "_links", "_dispo_7d", "_fontes", "_seguranca", "_controlados", "_cftv"]
    with factory_app.app_context(), patch.multiple(ptv, **dict.fromkeys(nomes, quebra)):
        painel = ptv.montar_painel()

    assert painel["score"] is None
    assert painel["unidades"] == [] and painel["alertas"] == []
    assert {"governança", "zabbix", "unidades", "zendesk", "fortigate", "segurança", "acronis", "cftv"} <= set(
        painel["erros"]
    )
    assert painel["seguranca"] is None and painel["controlados"] is None and painel["cftv"] is None
    assert painel["hora"]  # o relógio aparece mesmo sem nenhuma fonte


def test_alertas_mais_recentes_primeiro_com_unidade_e_texto_curto() -> None:
    problemas = [
        {
            "severity": 4,
            "host": "Cam A",
            "name": "ICMP Ping: Unavailable by ICMP ping",
            "since_iso": "2026-09-24T17:45:00-03:00",
        },
        {
            "severity": 3,
            "host": "Cam B",
            "name": "Outro",
            "since_iso": "2026-10-07T11:00:00-03:00",
            "acknowledged": True,
        },
    ]

    alertas = ptv._alertas(problemas, {"Cam A": "Sede Centro"})

    assert [a["host"] for a in alertas] == ["Cam B", "Cam A"]
    assert alertas[1]["problema"] == "sem ping"
    assert alertas[1]["unidade"] == "Sede Centro"
    assert alertas[0]["unidade"] == "—" and alertas[0]["reconhecido"] is True


def test_telas_exigem_login(factory_client) -> None:
    resp = factory_client.get("/gov/v1")
    assert resp.status_code in (302, 401)


@pytest.mark.parametrize("versao", [1, 2, 3, 4, 5, 6])
def test_cada_modelo_renderiza_com_dados_embutidos_e_escapados(authed_client, versao: int) -> None:
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        resp = authed_client.get(f"/gov/v{versao}")

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'id="dados-painel"' in html
    assert "<script>alert(1)</script>" not in html  # tojson escapa < e >
    assert "js/painel_tv.js" in html


def test_modelo_inexistente_404(authed_client) -> None:
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        assert authed_client.get("/gov/v9").status_code == 404


def test_dados_em_json_sem_cache(authed_client) -> None:
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        resp = authed_client.get("/gov/painel/dados")

    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json()["score"]["valor"] == 60.7


_TOKEN_TV = "tv-noc-" + "a" * 40


def test_tv_sem_token_configurado_nao_existe(factory_client, monkeypatch) -> None:
    monkeypatch.delenv("PAINEL_TV_TOKENS", raising=False)
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        assert factory_client.get(f"/gov/tv/{_TOKEN_TV}").status_code == 404
        assert factory_client.get(f"/gov/tv/{_TOKEN_TV}/dados").status_code == 404


def test_tv_com_token_abre_sem_login_no_modelo_escolhido(factory_client, monkeypatch) -> None:
    monkeypatch.setenv("PAINEL_TV_TOKENS", f"outro-{'b' * 40}, {_TOKEN_TV}")
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        resp = factory_client.get(f"/gov/tv/{_TOKEN_TV}")

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<title>v6" in html or "Sala de Controle Bento" in html
    assert f'data-url="/gov/tv/{_TOKEN_TV}/dados"' in html
    assert "/gov/v1" not in html  # sem o menu de troca de modelo
    assert "<script>alert(1)</script>" not in html


def test_tv_dados_com_token_em_json_sem_cache(factory_client, monkeypatch) -> None:
    monkeypatch.setenv("PAINEL_TV_TOKENS", _TOKEN_TV)
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        resp = factory_client.get(f"/gov/tv/{_TOKEN_TV}/dados")

    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json()["score"]["valor"] == 60.7


@pytest.mark.parametrize("token", ["errado-" + "c" * 40, _TOKEN_TV[:-1]])
def test_tv_token_errado_404(factory_client, monkeypatch, token: str) -> None:
    monkeypatch.setenv("PAINEL_TV_TOKENS", _TOKEN_TV)
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        assert factory_client.get(f"/gov/tv/{token}").status_code == 404
        assert factory_client.get(f"/gov/tv/{token}/dados").status_code == 404


def test_tv_token_curto_e_ignorado(factory_client, monkeypatch) -> None:
    monkeypatch.setenv("PAINEL_TV_TOKENS", "1234")
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        assert factory_client.get("/gov/tv/1234").status_code == 404


def test_versoes_com_login_buscam_dados_na_rota_normal(authed_client) -> None:
    with patch("app.views.painel_tv.get_painel", return_value=_PAINEL):
        html = authed_client.get("/gov/v6").get_data(as_text=True)

    assert 'data-url="/gov/painel/dados"' in html


def test_v6_mostra_seguranca_controlados_e_cameras() -> None:
    v6 = (Path(__file__).resolve().parent.parent / "app/templates/tv/v6.html").read_text(encoding="utf-8")

    for campo in ("d.seguranca", "d.controlados", "d.cftv", "Dispositivos controlados", "Câmeras"):
        assert campo in v6


def test_licencas_alertam_quando_perto_de_esgotar() -> None:
    """Uso de licenças alto é alerta (≥ 85% atenção, ≥ 95% crítico), não "verde"."""
    raiz = Path(__file__).resolve().parent.parent / "app"
    js = (raiz / "static/js/painel_tv.js").read_text(encoding="utf-8")
    assert 'v >= 95 ? "crit" : v >= 85 ? "warn" : "ok"' in js
    for versao in (4, 6):
        tela = (raiz / f"templates/tv/v{versao}.html").read_text(encoding="utf-8")
        assert "T.faixaUso(lic.uso_pct)" in tela
        assert "T.faixa(lic.uso_pct)" not in tela


_ACRONIS = {
    "total_agents": 214,
    "online": 187,
    "offline": 27,
    "outdated": 15,
    "protected": 198,
    "protected_pct": 92.5,
    "sem_plano_count": 9,
    "offline_gt_30d_count": 7,
    "incidents_total": 12,
    "incidents_not_mitigated": 3,
    "intrusion_attempts": 37,
    "intrusion_edr": 4,
    "intrusion_url": 29,
    "intrusion_login": 4,
    "patches_critical": 18,
    "incidentes": [
        {"resource_name": "NB-01", "alert_type": "Ransomware", "severity": "high", "mitigation": "", "time": None},
        {"resource_name": "NB-02", "alert_type": "URL", "severity": "low", "mitigation": "blocked", "time": None},
    ],
}


def test_seguranca_traz_invasoes_incidentes_e_defender() -> None:
    defender = {"enabled": True, "total_open": 6, "high": 2, "older_than_24h": 4}
    with (
        patch("itgov.api.v1.acronis_backup.get_cached_acronis_summary", return_value=_ACRONIS),
        patch("itgov.api.v1.governance_security_alerts.get_cached_security_alerts_summary", return_value=defender),
    ):
        seg = ptv._seguranca()

    assert seg["invasoes"] == 37 and seg["invasoes_url"] == 29
    assert seg["nao_mitigados"] == 3 and seg["patches_criticos"] == 18
    assert seg["defender_abertos"] == 6 and seg["defender_24h"] == 4
    assert [i["mitigado"] for i in seg["ultimos_incidentes"]] == [False, True]


def test_seguranca_sem_defender_continua_com_acronis() -> None:
    with (
        patch("itgov.api.v1.acronis_backup.get_cached_acronis_summary", return_value=_ACRONIS),
        patch(
            "itgov.api.v1.governance_security_alerts.get_cached_security_alerts_summary",
            side_effect=RuntimeError("influx fora"),
        ),
    ):
        seg = ptv._seguranca()

    assert seg["invasoes"] == 37 and seg["defender_abertos"] is None


def test_controlados_sem_acronis_vira_erro_do_bloco() -> None:
    with patch("itgov.api.v1.acronis_backup.get_cached_acronis_summary", return_value={}), pytest.raises(ValueError):
        ptv._controlados()
    with patch("itgov.api.v1.acronis_backup.get_cached_acronis_summary", return_value=_ACRONIS):
        ctl = ptv._controlados()
    assert ctl == {
        "total": 214,
        "online": 187,
        "offline": 27,
        "protegidos": 198,
        "protegidos_pct": 92.5,
        "sem_plano": 9,
        "offline_30d": 7,
        "desatualizados": 15,
    }


def test_cftv_conta_cameras_e_lista_fora_com_gravador_primeiro() -> None:
    devs = [
        {"name": "Cam 1", "is_gravador": False, "status": "up"},
        {"name": "Cam 2", "is_gravador": False, "status": "up"},
        {"name": "Cam 3", "is_gravador": False, "status": "down", "offline_desde": 1791400000},
        {"name": "Cam 4", "is_gravador": False, "status": "maint"},
        {"name": "Cam 5", "is_gravador": False, "status": "nodata"},
        {"name": "NVR 1", "is_gravador": True, "status": "down", "offline_desde": 1791300000},
        {"name": "NVR 2", "is_gravador": True, "status": "up"},
    ]
    with patch("itgov.api.v1.cftv_monitoring.get_cached_cftv_summary", return_value={"devices": devs}):
        cf = ptv._cftv({"Cam 3": "Shopping"})

    assert (cf["cameras"], cf["funcionando"], cf["fora"], cf["sem_dados"], cf["manutencao"]) == (5, 2, 1, 1, 1)
    assert cf["funcionando_pct"] == 50.0  # manutenção não conta como ativa
    assert (cf["gravadores"], cf["gravadores_fora"]) == (2, 1)
    assert [c["nome"] for c in cf["lista_fora"]] == ["NVR 1", "Cam 3"]
    assert cf["lista_fora"][1]["unidade"] == "Shopping" and cf["lista_fora"][1]["desde"].startswith("2026-10-")


def test_cftv_sem_dispositivos_vira_erro_do_bloco() -> None:
    with (
        patch("itgov.api.v1.cftv_monitoring.get_cached_cftv_summary", return_value={"devices": []}),
        pytest.raises(ValueError),
    ):
        ptv._cftv({})


def test_v5_tem_seis_paginas_e_coluna_ao_vivo() -> None:
    """v5 roda 6 páginas (cada uma com KPIs de controle + bloco de alerta) e o "Ao vivo" fixo."""
    tela = (Path(__file__).resolve().parent.parent / "app/templates/tv/v5.html").read_text(encoding="utf-8")
    assert "const NPG = 6;" in tela
    for nome in (
        "Visão geral",
        "Alertas críticos",
        "Segurança",
        "Dispositivos controlados",
        "Câmeras",
        "Rede e atendimento",
    ):
        assert f'pagina("{nome}"' in tela
    assert "Ao vivo" in tela and "T.faixaUso(lic.uso_pct)" in tela
