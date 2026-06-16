"""Cálculo de governança do pilar Aplicativos a partir de dados do Graph."""

from __future__ import annotations

from datetime import UTC, datetime

from itgov.models.governance_apps import AppRegistrationSummary, CredencialExpirando

_EXPIRANDO_DIAS = 30


def _credenciais(app: dict) -> list[tuple[str, str]]:
    """Retorna [(tipo, end_date_time), ...] de um app, password + certificate."""
    pares: list[tuple[str, str]] = []
    for cred in app.get("passwordCredentials") or []:
        end = cred.get("endDateTime")
        if end:
            pares.append(("password", end))
    for cred in app.get("keyCredentials") or []:
        end = cred.get("endDateTime")
        if end:
            pares.append(("certificate", end))
    return pares


def calcular_resumo_apps(apps: list[dict]) -> AppRegistrationSummary:
    """Calcula o resumo de governança a partir da lista de App Registrations do Graph.

    Args:
        apps: Lista de dicts retornados por AppRegistrationGraphClient.get_applications().

    Returns:
        AppRegistrationSummary agregado, com lista de credenciais expirando/expiradas
        ordenada pela mais urgente primeiro.
    """
    agora = datetime.now(UTC)
    expirando: list[CredencialExpirando] = []
    expirados = 0
    expirando_30d = 0

    for app in apps:
        nome = app.get("displayName") or "(sem nome)"
        app_id = app.get("appId") or app.get("id") or ""

        for tipo, end_str in _credenciais(app):
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            dias_restantes = (end_dt - agora).days
            if dias_restantes < 0:
                expirados += 1
                expirando.append(
                    CredencialExpirando(
                        app_display_name=nome,
                        app_id=app_id,
                        tipo=tipo,
                        end_date_time=end_dt,
                        dias_restantes=dias_restantes,
                    )
                )
            elif dias_restantes <= _EXPIRANDO_DIAS:
                expirando_30d += 1
                expirando.append(
                    CredencialExpirando(
                        app_display_name=nome,
                        app_id=app_id,
                        tipo=tipo,
                        end_date_time=end_dt,
                        dias_restantes=dias_restantes,
                    )
                )

    expirando.sort(key=lambda c: c.dias_restantes)

    return AppRegistrationSummary(
        total_apps=len(apps),
        secrets_expirando_30d=expirando_30d,
        secrets_expirados=expirados,
        expirando=expirando,
    )
