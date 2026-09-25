"""Integrações desligadas: menu "em breve", views 404 e API Zendesk 503.

Sem credenciais de Graph/Zendesk as páginas devolvem 404; o menu não pode
continuar oferecendo esses links como se funcionassem.
"""

from __future__ import annotations

import pytest

from app.integrations import graph_configured, zendesk_configured

_GRAPH_VARS = ("AZURE_CLIENT_ID", "MSAL_CLIENT_ID")


@pytest.fixture
def sem_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def com_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000000")


@pytest.fixture
def sem_zendesk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZENDESK_SUBDOMAIN", raising=False)


@pytest.fixture
def com_zendesk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test-corp")


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_graph_configured_false_sem_env(sem_graph: None) -> None:
    assert graph_configured() is False


@pytest.mark.parametrize("var", _GRAPH_VARS)
def test_graph_configured_aceita_azure_ou_msal(sem_graph: None, monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "abc")
    assert graph_configured() is True


def test_zendesk_configured(sem_zendesk: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert zendesk_configured() is False
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test-corp")
    assert zendesk_configured() is True


# ── Menu lateral ──────────────────────────────────────────────────────────────


def _link_do_menu(html: str, label: str) -> str:
    """Devolve o trecho <a ...>...</a> do item de menu com esse rótulo."""
    fim = html.index(f">{label}</span>")
    inicio = html.rindex("<a ", 0, fim)
    return html[inicio : html.index("</a>", fim)]


_ITENS_GRAPH = ("Dispositivos", "Aplicativos", "Compliance", "Dados / Labels", "Alertas Defender")
_ITENS_ZENDESK = ("Zendesk MTTR", "SLA / Chamados")


def test_menu_marca_em_breve_quando_desligadas(authed_client, sem_graph: None, sem_zendesk: None) -> None:
    html = authed_client.get("/").get_data(as_text=True)
    for label in _ITENS_GRAPH + _ITENS_ZENDESK:
        link = _link_do_menu(html, label)
        assert 'href="#"' in link, label
        assert "em breve" in link, label


def test_menu_linka_quando_configuradas(authed_client, com_graph: None, com_zendesk: None) -> None:
    html = authed_client.get("/").get_data(as_text=True)
    for label in _ITENS_GRAPH + _ITENS_ZENDESK:
        link = _link_do_menu(html, label)
        assert 'href="#"' not in link, label
        assert "em breve" not in link, label


# ── Views continuam 404 sem credenciais ───────────────────────────────────────


@pytest.mark.parametrize("path", ["/gov/governance/devices", "/gov/governance/apps", "/gov/governance/security-alerts"])
def test_views_graph_404_sem_credenciais(authed_client, sem_graph: None, path: str) -> None:
    assert authed_client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/gov/sla", "/gov/zendesk"])
def test_views_zendesk_404_sem_credenciais(authed_client, sem_zendesk: None, path: str) -> None:
    assert authed_client.get(path).status_code == 404


# ── API Zendesk: 503 em vez de 500 ────────────────────────────────────────────


def test_api_zendesk_503_sem_subdominio(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.ZENDESK_SUBDOMAIN", "")
    resp = authed_client.get("/api/v1/zendesk/groups")
    assert resp.status_code == 503
    assert "não configurada" in resp.get_json()["message"]
