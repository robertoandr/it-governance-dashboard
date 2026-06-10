"""Formatador de MessageCard para Microsoft Teams e dispatcher via webhook.

Formatos de card:
  🔴 DVR DOWN      → themeColor vermelho, seção com câmeras afetadas
  ⚠️  Individual    → themeColor laranja, alerta de câmera/ativo
  ✅  Recuperação   → themeColor verde

Referência:
  https://learn.microsoft.com/en-us/outlook/actionable-messages/message-card-reference
"""

from __future__ import annotations

import structlog

from itgov.utils.http_client import SyncAPIClient

log = structlog.get_logger(__name__)

_COR_CRITICO = "FF0000"  # vermelho
_COR_WARNING = "FF8C00"  # laranja
_COR_OK = "36A64F"  # verde


def _card_dvr_down(ramo: str, dvr_name: str, n_cameras: int, msg: str) -> dict:
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _COR_CRITICO,
        "summary": f"CAUSA-RAIZ: {dvr_name} DOWN",
        "sections": [
            {
                "activityTitle": f"🔴 [{ramo}] {dvr_name} DOWN — CAUSA-RAIZ",
                "activitySubtitle": f"{n_cameras} câmera(s) afetada(s)",
                "activityText": msg,
                "facts": [
                    {"name": "Ramo", "value": ramo},
                    {"name": "DVR", "value": dvr_name},
                    {"name": "Câmeras afetadas", "value": str(n_cameras)},
                    {"name": "Tipo", "value": "DVR_DOWN"},
                ],
            }
        ],
    }


def _card_individual(ramo: str, host_name: str, tipo: str, msg: str) -> dict:
    icone = "⚠️"
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _COR_WARNING,
        "summary": f"{host_name} DOWN",
        "sections": [
            {
                "activityTitle": f"{icone} [{ramo}] {host_name} DOWN",
                "activityText": msg,
                "facts": [
                    {"name": "Ramo", "value": ramo},
                    {"name": "Host", "value": host_name},
                    {"name": "Tipo", "value": tipo},
                ],
            }
        ],
    }


def _card_recovery(ramo: str, host_name: str, msg: str) -> dict:
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _COR_OK,
        "summary": f"{host_name} RECUPERADO",
        "sections": [
            {
                "activityTitle": f"✅ [{ramo}] {host_name} RECUPERADO",
                "activityText": msg,
                "facts": [
                    {"name": "Ramo", "value": ramo},
                    {"name": "Host", "value": host_name},
                ],
            }
        ],
    }


class TeamsNotifier:
    """Envia MessageCards para um webhook do Microsoft Teams.

    Args:
        webhook_url: URL do incoming webhook configurado no canal Teams.
        timeout: Timeout HTTP em segundos (padrão: 10).
    """

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        self._webhook_url = webhook_url
        self._client = SyncAPIClient(base_url="", timeout=timeout)

    def _post(self, card: dict) -> None:
        """Envia o card ao webhook. Falhas são logadas sem propagar."""
        try:
            resp = self._client._client.post(self._webhook_url, json=card, timeout=10)
            resp.raise_for_status()
            log.info("teams_notif_ok", summary=card.get("summary"))
        except Exception as exc:
            log.error("teams_notif_failed", error=str(exc), summary=card.get("summary"))

    def notify_dvr_down(self, ramo: str, dvr_name: str, n_cameras: int, msg: str) -> None:
        """Notifica DVR DOWN com contagem de câmeras afetadas."""
        self._post(_card_dvr_down(ramo, dvr_name, n_cameras, msg))

    def notify_individual_down(self, ramo: str, host_name: str, tipo: str, msg: str) -> None:
        """Notifica alerta individual (câmera, facial, antena)."""
        self._post(_card_individual(ramo, host_name, tipo, msg))

    def notify_recovery(self, ramo: str, host_name: str, msg: str) -> None:
        """Notifica recuperação (transição DOWN→UP)."""
        self._post(_card_recovery(ramo, host_name, msg))
