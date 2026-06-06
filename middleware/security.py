"""Security middleware: Talisman (headers/CSP) + Limiter (rate limiting) + ProxyFix.

Registrado em app.py via init_security(app).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

# Grafana embed via Nginx (iframe no dashboard principal)
_GRAFANA_ORIGIN = os.getenv("GRAFANA_ROOT_URL", "http://localhost:8090")

CONTENT_SECURITY_POLICY: dict[str, str | list[str]] = {
    "default-src": "'self'",
    "script-src": ["'self'", "'strict-dynamic'"],
    "style-src": ["'self'"],
    "img-src": ["'self'", "data:", "blob:"],
    "font-src": "'self'",
    "connect-src": "'self'",
    "frame-src": ["'self'", _GRAFANA_ORIGIN],
    "frame-ancestors": ["'self'"],
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
}

_DEFAULT_LIMITS = ["200 per day", "60 per hour", "20 per minute"]
_API_LIMITS = ["500 per day", "100 per hour", "30 per minute"]
_AUTH_LIMITS = ["20 per hour", "5 per minute"]


def _is_behind_proxy() -> bool:
    """True quando o app roda atrás de reverse proxy (nginx/traefik)."""
    return os.getenv("BEHIND_PROXY", "false").lower() in ("true", "1", "yes")


def _is_production() -> bool:
    """True em produção (não dev/testing)."""
    return os.getenv("FLASK_ENV") not in ("development", "testing")


def _get_limiter(app: Flask) -> Limiter:
    """Cria e configura o Limiter.

    Backend configurável via FLASK_LIMITER_STORAGE_URI env var.
    Default: in-memory (suficiente para instância única).
    Para múltiplas instâncias: redis://redis:6379/0
    """
    storage_uri = os.getenv("FLASK_LIMITER_STORAGE_URI", "memory://")
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=_DEFAULT_LIMITS,
        storage_uri=storage_uri,
        strategy="fixed-window",
    )
    return limiter


def init_security(app: Flask) -> Limiter:
    """Inicializa ProxyFix + Talisman + Limiter no app Flask.

    Args:
        app: Instância Flask configurada.

    Returns:
        Instância do Limiter (para decorar rotas com limites específicos).
    """
    behind_proxy: bool = _is_behind_proxy()
    is_prod: bool = _is_production()

    # 1) ProxyFix DEVE vir antes — confiar nos X-Forwarded-* do nginx
    if behind_proxy:
        app.wsgi_app = ProxyFix(  # type: ignore[assignment]
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_prefix=1,
        )
        log.info("proxyfix_applied", trusted_hops=1)

    # 2) Talisman — Em prod atrás de proxy, nginx cuida do HTTPS.
    #    Forçar HTTPS aqui causaria 302 -> https://127.0.0.1 (loop / 502).
    force_https: bool = is_prod and not behind_proxy

    Talisman(
        app,
        force_https=force_https,
        force_https_permanent=False,
        strict_transport_security=is_prod,
        strict_transport_security_max_age=31_536_000,
        strict_transport_security_include_subdomains=True,
        content_security_policy=CONTENT_SECURITY_POLICY,
        content_security_policy_nonce_in=["script-src"],
        referrer_policy="strict-origin-when-cross-origin",
        feature_policy={
            "geolocation": "'none'",
            "camera": "'none'",
            "microphone": "'none'",
        },
        x_content_type_options=True,
        x_xss_protection=True,
        # ⚠️ NÃO setar frame_options — sobrescreve frame-ancestors do CSP.
        frame_options=None,
    )

    limiter = _get_limiter(app)
    _configure_session(app)

    log.info(
        "security_middleware_initialized",
        https_enforced=force_https,
        behind_proxy=behind_proxy,
        production=is_prod,
        rate_limit_default=_DEFAULT_LIMITS[2],
    )
    return limiter


def _configure_session(app: Flask) -> None:
    """Configura cookies de sessão com flags de segurança.

    SAMESITE=Lax para não quebrar OAuth callbacks do Microsoft Entra.
    Cookie name sem prefixo __Host- para funcionar em subpath /governanca.
    """
    from datetime import timedelta

    is_prod = _is_production()
    app.config["SESSION_COOKIE_SECURE"] = is_prod
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)
    app.config["SESSION_COOKIE_NAME"] = "session"


def api_limit(limiter: Limiter):
    """Decorator de rate limit para endpoints de API."""
    return limiter.limit(_API_LIMITS[2])


def auth_limit(limiter: Limiter):
    """Decorator de rate limit para endpoints de autenticação."""
    return limiter.limit(_AUTH_LIMITS[1])
