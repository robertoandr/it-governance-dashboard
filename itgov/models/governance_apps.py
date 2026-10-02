"""Modelos Pydantic para o pilar Aplicativos — Governança de TI."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CredencialExpirando(BaseModel):
    """Secret ou certificado de uma App Registration próximo do vencimento."""

    app_display_name: str
    app_id: str
    tipo: str = Field(description="password | certificate")
    end_date_time: datetime
    dias_restantes: int = Field(description="Negativo = já expirado")
    gerenciado_microsoft: bool = Field(
        default=False, description="App criado e mantido por serviço da Microsoft (ex.: Copilot Studio)"
    )


class AppRegistrationSummary(BaseModel):
    """Resumo de governança de App Registrations.

    O sinal de risco mais acionável é credencial (secret/certificado)
    expirando — uma app para de autenticar silenciosamente quando o
    secret vence, sem erro óbvio até o primeiro uso após a expiração.
    """

    total_apps: int = Field(ge=0)
    secrets_expirando_30d: int = Field(ge=0)
    secrets_expirados: int = Field(ge=0)
    gerenciados_microsoft: int = Field(default=0, ge=0, description="Credenciais de apps mantidos pela Microsoft")
    expirando: list[CredencialExpirando] = Field(default_factory=list)
