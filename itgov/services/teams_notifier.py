"""Notificador Teams via Adaptive Card (Workflows trigger).

Formato aceito pelo trigger "When a Teams webhook request is received":
  { "type": "message",
    "attachments": [{ "contentType": "application/vnd.microsoft.card.adaptive",
                      "content": { <AdaptiveCard v1.4> } }] }

Severidades:
  "critico"  → color Attention (vermelho)
  "warning"  → color Warning   (laranja)
  "recovery" → color Good      (verde)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_COR_SEVERIDADE: dict[str, str] = {
    "critico": "Attention",
    "warning": "Warning",
    "recovery": "Good",
}

_ICONE_SEVERIDADE: dict[str, str] = {
    "critico": "🔴",
    "warning": "⚠️",
    "recovery": "✅",
}


def _montar_adaptive_card(
    tipo: str,
    severidade: str,
    titulo: str,
    unidade: str,
    ramo: str,
    host: str,
    qtd_afetados: int,
    mensagem: str,
    timestamp: str,
) -> dict[str, Any]:
    cor = _COR_SEVERIDADE.get(severidade, "Default")
    icone = _ICONE_SEVERIDADE.get(severidade, "")

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": f"{icone} {titulo}",
                "weight": "Bolder",
                "size": "Large",
                "color": cor,
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Unidade", "value": unidade},
                    {"title": "Ramo", "value": ramo},
                    {"title": "Host", "value": host},
                    {"title": "Afetados", "value": f"{qtd_afetados} câmera(s)"},
                    {"title": "Tipo", "value": tipo},
                ],
            },
            {
                "type": "TextBlock",
                "text": mensagem,
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": f"🕒 {timestamp}",
                "isSubtle": True,
                "size": "Small",
            },
        ],
    }

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def notify(payload: dict[str, Any]) -> bool:
    """Envia Adaptive Card ao webhook do Teams Workflows.

    Args:
        payload: Dicionário com chaves tipo, severidade, titulo, unidade,
                 ramo, host, qtd_afetados, mensagem, timestamp.

    Returns:
        True em sucesso (HTTP 200/202), False em qualquer falha.
    """
    webhook_url = os.getenv("TEAMS_WEBHOOK_URL", "")
    if not webhook_url:
        log.warning("teams_webhook_url_ausente", motivo="TEAMS_WEBHOOK_URL não definida")
        return False

    envelope = _montar_adaptive_card(
        tipo=payload.get("tipo", ""),
        severidade=payload.get("severidade", "warning"),
        titulo=payload.get("titulo", ""),
        unidade=payload.get("unidade", ""),
        ramo=payload.get("ramo", ""),
        host=payload.get("host", ""),
        qtd_afetados=payload.get("qtd_afetados", 0),
        mensagem=payload.get("mensagem", ""),
        timestamp=payload.get("timestamp", ""),
    )

    try:
        resp = httpx.post(webhook_url, json=envelope, timeout=10.0)
        if resp.status_code not in (200, 202):
            log.error(
                "teams_webhook_falhou",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        log.info("teams_notif_ok", tipo=payload.get("tipo"), host=payload.get("host"))
        return True
    except httpx.TimeoutException as exc:
        log.error("teams_webhook_erro", erro=str(exc))
        return False
    except Exception as exc:
        log.error("teams_webhook_erro", erro=str(exc))
        return False


# Mantido para compatibilidade de import nos testes legados — use notify() em código novo
class TeamsNotifier:
    """Wrapper legado sobre notify(). Prefer usar notify() diretamente."""

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        os.environ.setdefault("TEAMS_WEBHOOK_URL", webhook_url)

    def notify_dvr_down(self, ramo: str, dvr_name: str, n_cameras: int, msg: str) -> None:
        from datetime import UTC, datetime

        notify(
            {
                "tipo": "DVR_DOWN",
                "severidade": "critico",
                "titulo": f"{dvr_name} DOWN — CAUSA-RAIZ",
                "unidade": ramo,
                "ramo": ramo,
                "host": dvr_name,
                "qtd_afetados": n_cameras,
                "mensagem": msg,
                "timestamp": datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC"),
            }
        )

    def notify_individual_down(self, ramo: str, host_name: str, tipo: str, msg: str) -> None:
        from datetime import UTC, datetime

        notify(
            {
                "tipo": tipo,
                "severidade": "warning",
                "titulo": f"{host_name} DOWN",
                "unidade": ramo,
                "ramo": ramo,
                "host": host_name,
                "qtd_afetados": 0,
                "mensagem": msg,
                "timestamp": datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC"),
            }
        )

    def notify_recovery(self, ramo: str, host_name: str, msg: str) -> None:
        from datetime import UTC, datetime

        notify(
            {
                "tipo": "RECOVERY",
                "severidade": "recovery",
                "titulo": f"{host_name} RECUPERADO",
                "unidade": ramo,
                "ramo": ramo,
                "host": host_name,
                "qtd_afetados": 0,
                "mensagem": msg,
                "timestamp": datetime.now(UTC).strftime("%d/%m/%Y %H:%M UTC"),
            }
        )
