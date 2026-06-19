"""Cliente Microsoft Graph para Sensitivity Labels (pilar Dados/DLP).

Endpoint /security/dataSecurityAndGovernance/sensitivityLabels — não há API
de DLP Policies no Microsoft Graph (gerenciadas via Purview Compliance
Portal/PowerShell); sensitivity labels é o único sinal de governança de
dados exposto pelo Graph.
"""

from __future__ import annotations

import structlog

from itgov.services.graph_client import _fetch_token, _get

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_LABELS_URL = f"{GRAPH_BASE}/security/dataSecurityAndGovernance/sensitivityLabels"


class SensitivityLabelGraphClient:
    """Cliente assíncrono para sensitivity labels via Microsoft Graph."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def get_labels(self) -> list[dict]:
        """Retorna todos os sensitivity labels publicados no tenant."""
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            data = await _get(client, _LABELS_URL, token)

        labels = data.get("value", [])
        log.info("sensitivity_label_graph.fetched", total=len(labels))
        return labels
