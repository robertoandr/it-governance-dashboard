"""Testes do serviço de leitura do GoPhish (só leitura, sem rede real)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from itgov.services import gophish, phishing_material

_URL = "https://gophish-teste:3333"
_M365 = phishing_material.ISCAS[phishing_material.ISCA_SENHA_M365]
_RH = phishing_material.ISCAS[phishing_material.ISCA_RH_PREMIO]


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
    html = authed_client.get("/gov/cyber/phishing").get_data(as_text=True)
    assert "Phishing" in html and "/api/v1/cyber/phishing" in html


# ── Escrita (montar e disparar campanhas) ───────────────────────────────────


@respx.mock
def test_listar_smtp_devolve_nomes(credenciais: None) -> None:
    respx.get(f"{_URL}/api/smtp/").mock(
        return_value=httpx.Response(200, json=[{"name": "M365"}, {"name": "Teste"}, {"id": 3}])
    )
    assert gophish.listar_smtp() == ["M365", "Teste"]


def test_criar_smtp_exige_campos(credenciais: None) -> None:
    with pytest.raises(gophish.PhishingError):
        gophish.criar_smtp("", "h", "u", "s", "r")


def test_criar_smtp_rejeita_email_sem_arroba(credenciais: None) -> None:
    with pytest.raises(gophish.PhishingError, match="e-mail"):
        gophish.criar_smtp("M365", "smtp.office365.com:587", "u", "s", "apenas-texto")


@respx.mock
def test_criar_smtp_posta_perfil_com_remetente_sanitizado(credenciais: None) -> None:
    rota = respx.post(f"{_URL}/api/smtp/").mock(return_value=httpx.Response(201, json={"id": 1}))
    # Formatos RFC 5322 e prefixos "De:" devem ser sanitizados para o e-mail puro que o GoPhish aceita
    gophish.criar_smtp("M365", "smtp.office365.com:587", "u@x", "senha", "De: Avisos TI <ti@x.com>")
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["interface_type"] == "SMTP" and corpo["from_address"] == "ti@x.com"


@respx.mock
def test_garantir_material_cria_quando_falta(credenciais: None) -> None:
    respx.get(f"{_URL}/api/templates/").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_URL}/api/pages/").mock(return_value=httpx.Response(200, json=[]))
    pt = respx.post(f"{_URL}/api/templates/").mock(return_value=httpx.Response(201, json={"id": 1}))
    pp = respx.post(f"{_URL}/api/pages/").mock(return_value=httpx.Response(201, json={"id": 1}))
    gophish.garantir_material(phishing_material.ISCA_SENHA_M365, "https://noc/phishing/educativo")
    assert pt.called and pp.called
    pagina = json.loads(pp.calls.last.request.content)
    assert pagina["name"] == _M365.landing_nome
    assert pagina["capture_credentials"] is True and pagina["capture_passwords"] is False


@respx.mock
def test_garantir_material_rh_nao_captura_credenciais(credenciais: None) -> None:
    respx.get(f"{_URL}/api/templates/").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{_URL}/api/pages/").mock(return_value=httpx.Response(200, json=[]))
    pt = respx.post(f"{_URL}/api/templates/").mock(return_value=httpx.Response(201, json={"id": 1}))
    pp = respx.post(f"{_URL}/api/pages/").mock(return_value=httpx.Response(201, json={"id": 1}))
    gophish.garantir_material(phishing_material.ISCA_RH_PREMIO, "https://noc/phishing/educativo/rh-premio-surpresa")
    template = json.loads(pt.calls.last.request.content)
    pagina = json.loads(pp.calls.last.request.content)
    assert template["name"] == _RH.template_nome and template["subject"] == _RH.assunto
    # Isca do RH não guarda NADA do que a pessoa digita (nem senha, nem nome/e-mail).
    assert pagina["name"] == _RH.landing_nome
    assert pagina["capture_credentials"] is False and pagina["capture_passwords"] is False


def test_garantir_material_isca_desconhecida(credenciais: None) -> None:
    with pytest.raises(gophish.PhishingError):
        gophish.garantir_material("nao-existe", "https://noc/educativo")


@respx.mock
def test_garantir_material_nao_duplica(credenciais: None) -> None:
    respx.get(f"{_URL}/api/templates/").mock(return_value=httpx.Response(200, json=[{"name": _M365.template_nome}]))
    respx.get(f"{_URL}/api/pages/").mock(return_value=httpx.Response(200, json=[{"name": _M365.landing_nome}]))
    pt = respx.post(f"{_URL}/api/templates/")
    pp = respx.post(f"{_URL}/api/pages/")
    gophish.garantir_material(phishing_material.ISCA_SENHA_M365, "https://noc/educativo")
    assert not pt.called and not pp.called


def test_criar_grupo_exige_email_valido(credenciais: None) -> None:
    with pytest.raises(gophish.PhishingError):
        gophish.criar_grupo("grupo", ["sem-arroba", "   "])


@respx.mock
def test_criar_grupo_monta_alvos(credenciais: None) -> None:
    rota = respx.post(f"{_URL}/api/groups/").mock(return_value=httpx.Response(201, json={"id": 1}))
    gophish.criar_grupo("Piloto", ["a@x.com", "ruim", "b@x.com"])
    corpo = json.loads(rota.calls.last.request.content)
    assert [t["email"] for t in corpo["targets"]] == ["a@x.com", "b@x.com"]


def test_disparar_campanha_exige_campos(credenciais: None) -> None:
    with pytest.raises(gophish.PhishingError):
        gophish.disparar_campanha("c", "g", "", "http://x", phishing_material.ISCA_SENHA_M365)


def test_disparar_campanha_rejeita_isca_desconhecida(credenciais: None) -> None:
    with pytest.raises(gophish.PhishingError):
        gophish.disparar_campanha("c", "g", "M365", "http://x", "nao-existe")


@respx.mock
def test_disparar_campanha_usa_material_da_isca(credenciais: None) -> None:
    rota = respx.post(f"{_URL}/api/campaigns/").mock(return_value=httpx.Response(201, json={"id": 42}))
    cid = gophish.disparar_campanha(
        "Piloto", "Piloto — alvos", "M365", "http://172.29.2.11:8080/", phishing_material.ISCA_SENHA_M365
    )
    assert cid == 42
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["template"]["name"] == _M365.template_nome and corpo["page"]["name"] == _M365.landing_nome
    assert corpo["url"] == "http://172.29.2.11:8080"


@respx.mock
def test_disparar_campanha_rh_usa_material_do_rh(credenciais: None) -> None:
    rota = respx.post(f"{_URL}/api/campaigns/").mock(return_value=httpx.Response(201, json={"id": 7}))
    gophish.disparar_campanha(
        "Piloto RH", "Piloto RH — alvos", "RH", "http://172.29.2.11:8080", phishing_material.ISCA_RH_PREMIO
    )
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["template"]["name"] == _RH.template_nome and corpo["page"]["name"] == _RH.landing_nome


def test_opcoes_lista_as_duas_iscas() -> None:
    ids = {o["id"] for o in phishing_material.opcoes()}
    assert phishing_material.ISCA_SENHA_M365 in ids and phishing_material.ISCA_RH_PREMIO in ids


def test_obter_cai_na_padrao_quando_vazio_e_none_quando_invalida() -> None:
    assert phishing_material.obter("").id == phishing_material.ISCA_PADRAO
    assert phishing_material.obter("xpto") is None
