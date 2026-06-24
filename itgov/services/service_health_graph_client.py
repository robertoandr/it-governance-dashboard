"""Cliente Microsoft Graph para Service Health (pilar Auditoria/Saúde do tenant).

Endpoint: /admin/serviceAnnouncement/healthOverviews
Permissão: ServiceHealth.Read.All (Application)
"""

from __future__ import annotations

import httpx
import structlog

from itgov.services.graph_client import _fetch_token, _get

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_HEALTH_URL = f"{GRAPH_BASE}/admin/serviceAnnouncement/healthOverviews"


class ServiceHealthGraphClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def get_health_overviews(self) -> list[dict]:
        """Retorna lista de status por serviço M365."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            data = await _get(client, _HEALTH_URL, token)

        services = data.get("value", [])
        log.info("service_health_graph.fetched", count=len(services))
        return services
