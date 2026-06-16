"""Cliente Microsoft Graph para Secure Score (pilar Compliance).

Busca apenas o registro mais recente ($top=1) — o endpoint retorna histórico
diário, mas para o pilar de governança só o estado atual importa.
"""

from __future__ import annotations

import structlog

from itgov.services.graph_client import _fetch_token, _get

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SECURE_SCORE_URL = f"{GRAPH_BASE}/security/secureScores?$top=1"


class SecureScoreGraphClient:
    """Cliente assíncrono para o Secure Score mais recente via Microsoft Graph."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def get_latest_secure_score(self) -> dict | None:
        """Retorna o registro de secureScore mais recente, ou None se não houver dados."""
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            data = await _get(client, _SECURE_SCORE_URL, token)

        valores = data.get("value", [])
        if not valores:
            log.warning("secure_score_graph.sem_dados")
            return None

        log.info("secure_score_graph.fetched", current_score=valores[0].get("currentScore"))
        return valores[0]
