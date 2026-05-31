"""Security middleware: Talisman (headers/CSP) + Limiter (rate limiting).

Registrado em app.py via init_security(app).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

if TYPE_CHECKING:
    from flask import Flask

log = structlog.get_logger(__name__)

# Grafana embed via Nginx (iframe no dashboard principal)
_GRAFANA_ORIGIN = os.getenv("GRAFANA_ROOT_URL", "http://localhost:8090")

CONTENT_SECURITY_POLICY: dict[str, str | list[str]] = {
    "default-src": "'self'",
    "script-src": ["'self'", "'unsafe-inline'"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:", "blob:"],
    "font-src": "'self'",
    "connect-src": "'self'",
    # Permite iframe do Grafana via Nginx
    "frame-src": ["'self'", _GRAFANA_ORIGIN],
    "frame-ancestors": ["'self'"],
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
}

# Rate limits por endpoint
_DEFAULT_LIMITS = ["200 per day", "60 per hour", "20 per minute"]
_API_LIMITS = ["500 per day", "100 per hour", "30 per minute"]
_AUTH_LIMITS = ["20 per hour", "5 per minute"]


def _get_limiter(app: Flask) -> Limiter:
    """Cria e configura o Limiter com backend in-memory."""
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=_DEFAULT_LIMITS,
        storage_uri="memory://",
        strategy="fixed-window",
    )
    return limiter


def init_security(app: Flask) -> Limiter:
    """Inicializa Talisman + Limiter no app Flask.

    Args:
        app: Instância Flask configurada.

    Returns:
        Instância do Limiter (para decorar rotas com limites específicos).
    """
    _is_https = os.getenv("FLASK_ENV") not in ("development", "testing")

    Talisman(
        app,
        force_https=_is_https,
        strict_transport_security=_is_https,
        strict_transport_security_max_age=31536000,
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
        frame_options="SAMEORIGIN",
    )

    limiter = _get_limiter(app)

    _configure_session(app)

    log.info(
        "security_middleware_initialized",
        https_enforced=_is_https,
        rate_limit_default=_DEFAULT_LIMITS[2],
    )
    return limiter


def _configure_session(app: Flask) -> None:
    """Configura cookies de sessão com flags de segurança."""
    app.config.setdefault("SESSION_COOKIE_SECURE", os.getenv("FLASK_ENV") not in ("development", "testing"))
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", 3600)
    app.config.setdefault(
        "SESSION_COOKIE_NAME",
        "__Host-session" if os.getenv("FLASK_ENV") not in ("development", "testing") else "session",
    )


def api_limit(limiter: Limiter):
    """Decorator de rate limit para endpoints de API."""
    return limiter.limit(_API_LIMITS[2])


def auth_limit(limiter: Limiter):
    """Decorator de rate limit para endpoints de autenticação."""
    return limiter.limit(_AUTH_LIMITS[1])
