"""
Coletor LDAP/AD.
Habilitado apenas quando LDAP_ENABLED=true no .env e a conta de serviço existir.
DC do domínio grupogadens.com.br: 172.29.1.246
"""
import logging
import config

log = logging.getLogger(__name__)


class LDAPCollector:
    def get_user_summary(self) -> dict:
        if not config.LDAP_ENABLED:
            return {
                "active": 0,
                "disabled": 0,
                "total": 0,
                "enabled": False,
                "note": "LDAP_ENABLED=false. Habilite após criar a conta de serviço no DC.",
            }
        try:
            from ldap3 import Server, Connection, ALL, SUBTREE
        except ImportError:
            return {"active": 0, "disabled": 0, "total": 0, "enabled": False,
                    "error": "ldap3 não instalado"}

        try:
            srv = Server(config.LDAP_SERVER, get_info=ALL)
            conn = Connection(srv, config.LDAP_USER, config.LDAP_PASSWORD,
                              auto_bind=True, receive_timeout=10)
            # Usuários habilitados
            conn.search(
                config.LDAP_BASE_DN,
                "(&(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
                SUBTREE, attributes=["cn"],
            )
            active = len(conn.entries)
            # Usuários desabilitados
            conn.search(
                config.LDAP_BASE_DN,
                "(&(objectClass=user)(userAccountControl:1.2.840.113556.1.4.803:=2))",
                SUBTREE, attributes=["cn"],
            )
            disabled = len(conn.entries)
            conn.unbind()
            return {
                "active": active,
                "disabled": disabled,
                "total": active + disabled,
                "enabled": True,
            }
        except Exception as e:
            log.warning("LDAP falhou: %s", e)
            return {"active": 0, "disabled": 0, "total": 0, "enabled": True,
                    "error": str(e)}
