"""Detecção de integrações externas configuradas.

Fonte única usada pelas views (404 quando desligada) e pelo menu lateral
(item "em breve"), para que os dois nunca divirjam.
"""

from __future__ import annotations

import os


def zendesk_configured() -> bool:
    """Indica se as credenciais do Zendesk estão presentes no ambiente.

    Returns:
        ``True`` quando ``ZENDESK_SUBDOMAIN`` está definido.
    """
    return bool(os.getenv("ZENDESK_SUBDOMAIN"))


def graph_configured() -> bool:
    """Indica se o app registration do Microsoft Graph está presente no ambiente.

    Returns:
        ``True`` quando ``AZURE_CLIENT_ID`` ou ``MSAL_CLIENT_ID`` está definido.
    """
    return bool(os.getenv("AZURE_CLIENT_ID") or os.getenv("MSAL_CLIENT_ID"))
