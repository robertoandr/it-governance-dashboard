"""Permissão de gestão de phishing e rotas do dashboard."""

from __future__ import annotations

import pytest

from app.models.user import User


def test_pode_gerenciar_phishing_admin_sempre_e_flag_para_os_demais() -> None:
    admin = User(name="A", email="a@x", role="admin")
    gestor = User(name="G", email="g@x", role="gestor")
    gestor_liberado = User(name="G2", email="g2@x", role="gestor", gerencia_phishing=True)
    assert admin.pode_gerenciar_phishing is True
    assert gestor.pode_gerenciar_phishing is False
    assert gestor_liberado.pode_gerenciar_phishing is True


def test_pagina_educativa_e_publica(factory_client) -> None:
    resp = factory_client.get("/gov/phishing/educativo")
    assert resp.status_code == 200
    assert "simulação" in resp.get_data(as_text=True)


def test_aba_phishing_tem_gestao_para_admin(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from itgov.services import gophish
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    monkeypatch.setattr(gophish, "configurado", lambda: False)
    _mock_fontes(monkeypatch)
    html = authed_client.get("/gov/cyber").get_data(as_text=True)
    assert "Nova campanha" in html and "/gov/cyber/phishing/disparar" in html


def test_smtp_e_disparo_exigem_login(factory_client) -> None:
    # Rotas do dashboard: sem sessão, o login_required redireciona para o login.
    assert factory_client.post("/gov/cyber/phishing/smtp", json={}).status_code == 302
    assert factory_client.post("/gov/cyber/phishing/disparar", json={}).status_code == 302
