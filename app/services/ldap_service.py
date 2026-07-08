"""LDAP / Active Directory user summary (contagem de usuários habilitados/desabilitados).

Migrado de collectors/ldap_collector.py (Sprint 10F). Ver docs/MIGRATION.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_UAC_ENABLED_FILTER = "(&(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
_UAC_DISABLED_FILTER = "(&(objectClass=user)(userAccountControl:1.2.840.113556.1.4.803:=2))"


@dataclass
class LdapUserSummary:
    """Resultado da contagem de usuários no AD."""

    active: int
    disabled: int
    total: int
    enabled: bool
    error: str | None = None

    def as_dict(self) -> dict:
        d = {"active": self.active, "disabled": self.disabled, "total": self.total, "enabled": self.enabled}
        if self.error is not None:
            d["error"] = self.error
        return d


class LdapService:
    """Consulta o AD via LDAP para obter a contagem de usuários ativos/desativados."""

    def get_user_summary(self) -> LdapUserSummary:
        settings = get_settings().ldap

        if not settings.enabled:
            return LdapUserSummary(active=0, disabled=0, total=0, enabled=False)

        try:
            from ldap3 import ALL, NTLM, SUBTREE, Connection, Server
        except ImportError:
            log.error("ldap_service_import_error", detail="ldap3 não instalado")
            return LdapUserSummary(active=0, disabled=0, total=0, enabled=False, error="ldap3 não instalado")

        try:
            server = Server(settings.server, get_info=ALL)
            # AD rejeita bind SIMPLE (default do ldap3) para usuário no formato
            # DOMAIN\user — precisa de NTLM explícito ou UPN. LDAP_USER já vem
            # como DOMAIN\user (usado também pelo patch collector via WinRM).
            conn = Connection(
                server,
                settings.user,
                settings.password.get_secret_value(),
                authentication=NTLM,
                auto_bind=True,
                receive_timeout=10,
            )

            conn.search(settings.base_dn, _UAC_ENABLED_FILTER, SUBTREE, attributes=["cn"])
            active = len(conn.entries)

            conn.search(settings.base_dn, _UAC_DISABLED_FILTER, SUBTREE, attributes=["cn"])
            disabled = len(conn.entries)

            conn.unbind()

            return LdapUserSummary(active=active, disabled=disabled, total=active + disabled, enabled=True)
        except Exception as e:
            log.warning("ldap_service_falhou", erro=str(e))
            return LdapUserSummary(active=0, disabled=0, total=0, enabled=True, error=str(e))
