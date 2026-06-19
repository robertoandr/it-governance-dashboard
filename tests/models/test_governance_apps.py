"""Testes para itgov/models/governance_apps.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from itgov.models.governance_apps import AppRegistrationSummary, CredencialExpirando


class TestCredencialExpirando:
    def test_dias_restantes_negativo_permitido(self) -> None:
        cred = CredencialExpirando(
            app_display_name="app-x",
            app_id="id-1",
            tipo="password",
            end_date_time="2026-01-01T00:00:00Z",
            dias_restantes=-5,
        )

        assert cred.dias_restantes == -5


class TestAppRegistrationSummary:
    def test_total_apps_negativo_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            AppRegistrationSummary(total_apps=-1, secrets_expirando_30d=0, secrets_expirados=0)

    def test_default_expirando_lista_vazia(self) -> None:
        resumo = AppRegistrationSummary(total_apps=5, secrets_expirando_30d=0, secrets_expirados=0)

        assert resumo.expirando == []
