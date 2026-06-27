"""Cliente Microsoft Graph para inventário de dispositivos (pilar Dispositivos).

Usa o endpoint /devices (Entra ID device objects), não /deviceManagement/managedDevices
(Intune) — este tenant não usa Intune para gestão de dispositivos (managedDevices
retorna vazio), então o sinal de governança real vem do registro Entra ID
(Workplace Join / Hybrid Join / Azure AD Join), incluindo dispositivos parados.
"""

from __future__ import annotations

import structlog

from itgov.services.graph_client import _fetch_token
from itgov.services.mfa_graph_client import _paginate

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_DEVICE_SELECT = (
    "id,displayName,operatingSystem,operatingSystemVersion,trustType,"
    "isCompliant,isManaged,approximateLastSignInDateTime,registrationDateTime"
)
_DEVICES_URL = f"{GRAPH_BASE}/devices?$select={_DEVICE_SELECT}&$top=999"

_METER_NAME = "itgov.device_graph"


class DeviceGraphClient:
    """Cliente assíncrono para inventário de dispositivos via Microsoft Graph."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def get_devices(self, tenant_id: str) -> list[dict]:
        """Retorna todos os dispositivos registrados no tenant (Entra ID)."""
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            resultado = []
            async for device in _paginate(client, _DEVICES_URL, token, tenant_id, "devices"):
                resultado.append(device)
        log.info("device_graph.fetched", total=len(resultado), tenant_id=tenant_id)
        return resultado
