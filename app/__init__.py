"""Flask application factory for the Governança de TI Dashboard."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from flask import Flask, g, jsonify

from app.utils.logging import configure_logging

if TYPE_CHECKING:
    from app.config import AppSettings


def create_app(settings: AppSettings | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        settings: Optional AppSettings override (useful for testing).

    Returns:
        Configured Flask application instance.
    """
    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    configure_logging(
        level=settings.logging.level,
        fmt=settings.logging.format,
    )

    import structlog

    log = structlog.get_logger(__name__)

    from pathlib import Path

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Core Flask config
    _users_db = Path(__file__).resolve().parent.parent / "data" / "app.db"
    app.config.update(
        SECRET_KEY=settings.app.secret_key.get_secret_value(),
        TESTING=settings.app.testing,
        DEBUG=settings.app.debug,
        APP_VERSION=settings.app.version,
        APP_ENVIRONMENT=settings.app.environment,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{_users_db}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    # Flask-SQLAlchemy + Flask-Login + Flask-Bcrypt
    from app.extensions import bcrypt, db, login_manager

    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    # Ensure tables exist (idempotent).
    # Use "from ... import" — plain "import app.models.user" would rebind the
    # local variable "app" to the Python package, shadowing the Flask instance.
    with app.app_context():
        from app.models import link as _link_model  # noqa: F401
        from app.models import user as _user_model  # noqa: F401

        db.create_all()

    # Existing raw-SQLite governance DB (unchanged)
    from app.services.db import init_db

    init_db(app, settings)

    # Flask-RESTX API
    from flask_restx import Api

    api = Api(
        app,
        version=settings.app.version,
        title="Governança de TI Dashboard API",
        description="5-pillar COBIT-aligned governance score API",
        prefix="/api",
        doc="/api/docs",
    )

    from app.api.dashboards import ns as overview_ns
    from app.api.health import ns as health_ns
    from app.api.intune import ns as intune_ns
    from app.api.pillars import ns as pillars_ns
    from app.api.pmo import ns as pmo_ns

    api.add_namespace(overview_ns, path="/overview")
    api.add_namespace(pillars_ns, path="/pillars")
    api.add_namespace(health_ns, path="/health")
    api.add_namespace(pmo_ns, path="/pmo")
    api.add_namespace(intune_ns, path="/intune")

    # Legacy itgov namespaces (Sprint 10E) — preserved under /api/v1/ path
    try:
        from itgov.api.v1.ativos import ns as ativos_ns
        from itgov.api.v1.zabbix import ns as zabbix_ns
        from itgov.api.v1.zendesk import ns as zendesk_ns

        api.add_namespace(zabbix_ns, path="/v1/zabbix")
        api.add_namespace(zendesk_ns, path="/v1/zendesk")
        api.add_namespace(ativos_ns, path="/v1/ativos")
        app.extensions["itgov_api"] = api
        log.info("itgov_legacy_namespaces_registered")
    except Exception as _e:
        log.warning("itgov_legacy_api_unavailable", error=str(_e))

    # Governança MFA — bloco separado: não depende de ZABBIX/Zendesk
    try:
        from itgov.api.v1.governance_mfa import ns as governance_mfa_ns

        api.add_namespace(governance_mfa_ns, path="/v1/governance")
        log.info("itgov_governance_mfa_registered")
    except Exception as _e:
        log.warning("itgov_governance_mfa_unavailable", error=str(_e))

    # Governança Dispositivos + Aplicativos (pilares M365) — mesmo padrão do MFA
    try:
        from itgov.api.v1.governance_apps import ns as governance_apps_ns
        from itgov.api.v1.governance_devices import ns as governance_devices_ns

        api.add_namespace(governance_devices_ns, path="/v1/governance")
        api.add_namespace(governance_apps_ns, path="/v1/governance")
        log.info("itgov_governance_devices_apps_registered")
    except Exception as _e:
        log.warning("itgov_governance_devices_apps_unavailable", error=str(_e))

    # Governança Compliance (Secure Score) — mesmo padrão do MFA
    try:
        from itgov.api.v1.governance_compliance import ns as governance_compliance_ns

        api.add_namespace(governance_compliance_ns, path="/v1/governance")
        log.info("itgov_governance_compliance_registered")
    except Exception as _e:
        log.warning("itgov_governance_compliance_unavailable", error=str(_e))

    # Governança Dados (Sensitivity Labels) — mesmo padrão do MFA
    try:
        from itgov.api.v1.governance_data import ns as governance_data_ns

        api.add_namespace(governance_data_ns, path="/v1/governance")
        log.info("itgov_governance_data_registered")
    except Exception as _e:
        log.warning("itgov_governance_data_unavailable", error=str(_e))

    # Governança Service Health — ServiceHealth.Read.All
    try:
        from itgov.api.v1.governance_service_health import ns as governance_service_health_ns

        api.add_namespace(governance_service_health_ns, path="/v1/governance")
        log.info("itgov_governance_service_health_registered")
    except Exception as _e:
        log.warning("itgov_governance_service_health_unavailable", error=str(_e))

    # Governança Security Alerts — Defender KPI-END-01
    try:
        from itgov.api.v1.governance_security_alerts import ns as governance_security_alerts_ns

        api.add_namespace(governance_security_alerts_ns, path="/v1/governance")
        log.info("itgov_governance_security_alerts_registered")
    except Exception as _e:
        log.warning("itgov_governance_security_alerts_unavailable", error=str(_e))

    # HTML blueprints
    import os

    from app.auth import bp as auth_bp
    from app.views.dashboards import bp as dashboards_bp
    from app.views.users import bp as users_bp

    _gov_prefix = os.getenv("APP_ROOT_PATH", "/gov")
    app.register_blueprint(auth_bp, url_prefix=_gov_prefix)
    app.register_blueprint(dashboards_bp, url_prefix=_gov_prefix)
    app.register_blueprint(users_bp, url_prefix=_gov_prefix)

    # CLI commands
    from app.commands import register_commands

    register_commands(app)

    # Error handlers
    @app.errorhandler(404)
    def not_found(e: Exception) -> Any:
        from flask import request

        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return (
            app.send_static_file("../templates/errors/404.html") if False else (_render_error("errors/404.html", 404))
        )

    @app.errorhandler(500)
    def internal_error(e: Exception) -> Any:
        log.error("internal_server_error", error=str(e))
        from flask import request

        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return _render_error("errors/500.html", 500)

    # Per-request CSP nonce — generated once per request, stored in g
    @app.before_request
    def _generate_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(16)

    # Security headers (including CSP with nonce for script-src)
    @app.after_request
    def add_security_headers(response: Any) -> Any:
        nonce = getattr(g, "csp_nonce", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'nonce-{nonce}' 'strict-dynamic' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            f"style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data:; "
            f"connect-src 'self'; "
            f"frame-ancestors 'none'"
        )
        return response

    # Error handlers for auth
    @app.errorhandler(401)
    def unauthorized(e: Exception) -> Any:
        from flask import redirect, url_for

        return redirect(url_for("auth.login")), 302

    @app.errorhandler(403)
    def forbidden(e: Exception) -> Any:
        return _render_error("errors/403.html", 403)

    # Context processors
    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        from flask_login import current_user as cu

        return {
            "app_version": settings.app.version,
            "app_name": settings.app.name,
            "environment": settings.app.environment,
            "csp_nonce": lambda: getattr(g, "csp_nonce", ""),
            "current_user": cu,
        }

    log.info(
        "app_created",
        version=settings.app.version,
        environment=settings.app.environment,
    )
    return app


def _render_error(template: str, code: int) -> Any:
    from flask import render_template

    return render_template(template), code


# Backward-compat: `from app import app` and `from app import itgov_api`
# used by legacy tests that predate the factory pattern.
def _get_legacy_app() -> Flask:
    """Return a cached module-level Flask instance for legacy test imports."""
    import sys

    _mod = sys.modules[__name__]
    if not hasattr(_mod, "_legacy_app_instance"):
        _mod._legacy_app_instance = create_app()  # type: ignore[attr-defined]
    return _mod._legacy_app_instance  # type: ignore[return-value]


class _LegacyAppProxy:
    """Proxy that forwards attribute access to the cached Flask instance."""

    def __getattr__(self, name: str):  # type: ignore[override]
        return getattr(_get_legacy_app(), name)

    def __call__(self, *args, **kwargs):  # type: ignore[override]
        return _get_legacy_app()(*args, **kwargs)


app = _LegacyAppProxy()  # type: ignore[assignment]


def __getattr__(name: str):  # module-level __getattr__ (PEP 562)
    if name == "itgov_api":
        flask_app = _get_legacy_app()
        return flask_app.extensions.get("itgov_api")
    raise AttributeError(f"module 'app' has no attribute {name!r}")
