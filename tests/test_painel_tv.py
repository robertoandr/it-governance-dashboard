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
    nomes += ["_dispositivos", "_links", "_dispo_7d", "_fontes"]
    with factory_app.app_context(), patch.multiple(ptv, **dict.fromkeys(nomes, quebra)):
        painel = ptv.montar_painel()

    assert painel["score"] is None
    assert painel["unidades"] == [] and painel["alertas"] == []
    assert {"governança", "zabbix", "unidades", "zendesk", "fortigate"} <= set(painel["erros"])
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


def test_licencas_alertam_quando_perto_de_esgotar() -> None:
    """Uso de licenças alto é alerta (≥ 85% atenção, ≥ 95% crítico), não "verde"."""
    raiz = Path(__file__).resolve().parent.parent / "app"
    js = (raiz / "static/js/painel_tv.js").read_text(encoding="utf-8")
    assert 'v >= 95 ? "crit" : v >= 85 ? "warn" : "ok"' in js
    for versao in (4, 6):
        tela = (raiz / f"templates/tv/v{versao}.html").read_text(encoding="utf-8")
        assert "T.faixaUso(lic.uso_pct)" in tela
        assert "T.faixa(lic.uso_pct)" not in tela
