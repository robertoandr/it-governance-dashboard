"""Cliente Microsoft Graph para Secure Score (pilar Compliance).

Busca apenas o registro mais recente ($top=1) — o endpoint retorna histórico
diário, mas para o pilar de governança só o estado atual importa.

Tambem expoe get_security_controls() para buscar perfis de controles de segurança
(secureScoreControlProfiles) — usado para detectar Safe Links, Safe Attachments
e Audit Log (KPI-EMAIL-01, KPI-AUD-01).
"""

from __future__ import annotations

import structlog

from itgov.services.graph_client import _fetch_token, _get

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SECURE_SCORE_URL = (
    f"{GRAPH_BASE}/security/secureScores"
    "?$top=1&$select=id,currentScore,maxScore,averageComparativeScores,controlScores,activeUserCount"
)
_CONTROL_PROFILES_URL = (
    f"{GRAPH_BASE}/security/secureScoreControlProfiles"
    "?$top=999&$select=id,controlName,title,controlCategory,implementationStatus,score,maxScore,"
    "remediation,actionUrl"
)


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

    async def get_security_controls(self) -> list[dict]:
        """Retorna lista de perfis de controles de segurança do Secure Score.

        Pagina automaticamente via @odata.nextLink.
        Cada item inclui: id, controlName, title, controlCategory,
        implementationStatus, score, maxScore, remediation, actionUrl.

        Requer SecurityEvents.Read.All ou SecurityActions.Read.All.
        """
        import httpx

        results: list[dict] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            url: str | None = _CONTROL_PROFILES_URL
            page = 0
            while url:
                page += 1
                data = await _get(client, url, token)
                results.extend(data.get("value", []))
                url = data.get("@odata.nextLink")

        log.info("secure_score_graph.controls_fetched", count=len(results), pages=page)
        return results
