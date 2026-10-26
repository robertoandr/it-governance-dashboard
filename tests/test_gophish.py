"""Testes do serviço de leitura do GoPhish (só leitura, sem rede real)."""

from __future__ import annotations

import httpx
import pytest
import respx

from itgov.services import gophish

_URL = "https://gophish-teste:3333"


@pytest.fixture
def credenciais(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOPHISH_API_URL", _URL)
    monkeypatch.setenv("GOPHISH_API_KEY", "chave-secreta")
    monkeypatch.delenv("GOPHISH_VERIFY_TLS", raising=False)


def _summary(campanhas: list[dict]) -> dict:
    return {"total": len(campanhas), "campaigns": campanhas}


def _camp(cid: int, nome: str, criada: str, **stats: int) -> dict:
    base = {"total": 0, "sent": 0, "opened": 0, "clicked": 0, "submitted_data": 0, "email_reported": 0, "error": 0}
    return {
        "id": cid,
        "name": nome,
        "status": "Completed",
        "created_date": criada,
        "launch_date": criada,
        "stats": base | stats,
    }


def test_configurado_depende_das_duas_variaveis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOPHISH_API_URL", raising=False)
    monkeypatch.delenv("GOPHISH_API_KEY", raising=False)
    assert gophish.configurado() is False
    monkeypatch.setenv("GOPHISH_API_URL", _URL)
    assert gophish.configurado() is False
    monkeypatch.setenv("GOPHISH_API_KEY", "x")
    assert gophish.configurado() is True


@respx.mock
def test_listar_campanhas_mapeia_stats_e_ordena_por_data(credenciais: None) -> None:
    respx.get(f"{_URL}/api/campaigns/summary").mock(
        return_value=httpx.Response(
            200,
            json=_summary(
                [
                    _camp(1, "Antiga", "2026-01-01T10:00:00Z", total=10, clicked=2, email_reported=1),
                    _camp(2, "Nova", "2026-06-01T10:00:00Z", total=50, clicked=5, email_reported=20),
                ]
            ),
        )
    )
    campanhas = gophish.listar_campanhas()
    assert [c.nome for c in campanhas] == ["Nova", "Antiga"]  # mais nova primeiro
    nova = campanhas[0]
    assert (nova.alvos, nova.clicaram, nova.reportaram) == (50, 5, 20)
    assert nova.taxa_clique == 0.1 and nova.taxa_reporte == 0.4


def test_taxas_com_zero_alvos_nao_dividem_por_zero() -> None:
    c = gophish._campanha({"id": 1, "name": "vazia", "stats": {}})
    assert c.taxa_clique == 0.0 and c.taxa_reporte == 0.0


def test_painel_sem_configuracao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOPHISH_API_URL", raising=False)
    monkeypatch.delenv("GOPHISH_API_KEY", raising=False)
    painel = gophish.painel()
    assert painel == {"configurado": False, "erro": "", "campanhas": [], "resumo": {}}


@respx.mock
def test_painel_agrega_resumo_geral(credenciais: None) -> None:
    respx.get(f"{_URL}/api/campaigns/summary").mock(
        return_value=httpx.Response(
            200,
            json=_summary(
                [
                    _camp(1, "A", "2026-01-01T10:00:00Z", total=100, clicked=10, email_reported=5, submitted_data=3),
                    _camp(2, "B", "2026-02-01T10:00:00Z", total=100, clicked=30, email_reported=15, submitted_data=7),
                ]
            ),
        )
    )
    resumo = gophish.painel()["resumo"]
    assert resumo["campanhas"] == 2
    assert resumo["alvos"] == 200 and resumo["clicaram"] == 40 and resumo["reportaram"] == 20
    assert resumo["caíram"] == 10
    assert resumo["taxa_clique"] == 0.2 and resumo["taxa_reporte"] == 0.1


@respx.mock
def test_painel_engole_erro_http(credenciais: None) -> None:
    respx.get(f"{_URL}/api/campaigns/summary").mock(return_value=httpx.Response(502))
    painel = gophish.painel()
    assert painel["configurado"] is True and painel["campanhas"] == []
    assert "GoPhish" in painel["erro"]


def test_endpoint_phishing_responde_sem_configuracao(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOPHISH_API_URL", raising=False)
    monkeypatch.delenv("GOPHISH_API_KEY", raising=False)
    dados = authed_client.get("/api/v1/cyber/phishing").get_json()
    assert dados == {"configurado": False, "erro": "", "campanhas": [], "resumo": {}}


def test_endpoint_phishing_exige_login(factory_client) -> None:
    assert factory_client.get("/api/v1/cyber/phishing").status_code == 401


def test_pagina_cyber_tem_aba_phishing(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    html = authed_client.get("/gov/cyber").get_data(as_text=True)
    assert "Phishing" in html and "/api/v1/cyber/phishing" in html
