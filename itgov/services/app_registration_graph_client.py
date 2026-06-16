"""Cliente Microsoft Graph para inventário de App Registrations (pilar Aplicativos).

Foco em governança de credenciais: secrets/certificados expirando ou já
expirados são o sinal de risco mais acionável neste endpoint — uma app
registration com secret expirado para de funcionar silenciosamente.
"""

from __future__ import annotations

import structlog

from itgov.services.graph_client import _fetch_token
from itgov.services.mfa_graph_client import _paginate

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_APP_SELECT = "id,appId,displayName,createdDateTime,signInAudience,passwordCredentials,keyCredentials"
_APPS_URL = f"{GRAPH_BASE}/applications?$select={_APP_SELECT}&$top=999"


class AppRegistrationGraphClient:
    """Cliente assíncrono para inventário de App Registrations via Microsoft Graph."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def get_applications(self, tenant_id: str) -> list[dict]:
        """Retorna todas as App Registrations do tenant, com credenciais."""
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            resultado = []
            async for app in _paginate(client, _APPS_URL, token, tenant_id, "applications"):
                resultado.append(app)
        log.info("app_registration_graph.fetched", total=len(resultado), tenant_id=tenant_id)
        return resultado
