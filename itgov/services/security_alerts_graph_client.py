"""Cliente Graph para alertas de segurança do Microsoft Defender (pilar Endpoint).

KPI-END-01: conta alertas abertos há mais de 24h no Microsoft Defender via
Graph API /security/alerts_v2.

Requer permissão: SecurityAlert.Read.All
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import structlog

from itgov.services.graph_client import _fetch_token, _get

log = structlog.get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class SecurityAlertsGraphClient:
    """Cliente assíncrono para alertas de segurança via Microsoft Graph."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout

    async def get_alerts_summary(self) -> dict:
        """Retorna resumo de alertas: total, por severidade, abertos > 24h.

        Busca alertas com status 'new' (abertos).
        Classifica por severidade e conta os criados há mais de 24h.

        Returns:
            dict com chaves:
                total_open (int), high (int), medium (int), low (int),
                older_than_24h (int), alerts (list[dict]).
            Cada alert: id, title, severity, status, createdDateTime, category.
        """
        url = (
            f"{GRAPH_BASE}/security/alerts_v2"
            "?$filter=status eq 'new'"
            "&$top=999"
            "&$select=id,title,severity,status,createdDateTime,category"
        )
        alerts: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            token = await _fetch_token(client)
            next_url: str | None = url
            while next_url:
                data = await _get(client, next_url, token)
                page_items = data.get("value", [])
                alerts.extend(page_items)
                next_url = data.get("@odata.nextLink")

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        total_open = len(alerts)
        high = sum(1 for a in alerts if a.get("severity", "").lower() == "high")
        medium = sum(1 for a in alerts if a.get("severity", "").lower() == "medium")
        low = sum(1 for a in alerts if a.get("severity", "").lower() == "low")
        older_than_24h = 0
        for alert in alerts:
            created_str = alert.get("createdDateTime", "")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if created < cutoff:
                        older_than_24h += 1
                except ValueError:
                    pass

        alert_list = [
            {
                "id": a.get("id", ""),
                "title": a.get("title", ""),
                "severity": a.get("severity", ""),
                "status": a.get("status", ""),
                "createdDateTime": a.get("createdDateTime", ""),
                "category": a.get("category", ""),
            }
            for a in alerts[:50]  # Limita a 50 para evitar payloads gigantes
        ]

        log.info(
            "security_alerts.summary",
            total_open=total_open,
            high=high,
            medium=medium,
            low=low,
            older_than_24h=older_than_24h,
        )

        return {
            "total_open": total_open,
            "high": high,
            "medium": medium,
            "low": low,
            "older_than_24h": older_than_24h,
            "alerts": alert_list,
        }
