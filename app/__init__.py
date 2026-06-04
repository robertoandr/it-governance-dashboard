"""Flask application factory for the IT Governance Dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, jsonify

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

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Core Flask config
    app.config.update(
        SECRET_KEY=settings.app.secret_key.get_secret_value(),
        TESTING=settings.app.testing,
        DEBUG=settings.app.debug,
        APP_VERSION=settings.app.version,
        APP_ENVIRONMENT=settings.app.environment,
    )

    # Database
    from app.services.db import init_db

    init_db(app, settings)

    # Flask-RESTX API
    from flask_restx import Api

    api = Api(
        app,
        version=settings.app.version,
        title="IT Governance Dashboard API",
        description="5-pillar COBIT-aligned governance score API",
        prefix="/api",
        doc="/api/docs",
    )

    from app.api.dashboards import ns as overview_ns
    from app.api.pillars import ns as pillars_ns

    api.add_namespace(overview_ns, path="/overview")
    api.add_namespace(pillars_ns, path="/pillars")

    # HTML blueprints
    from app.views.dashboards import bp as dashboards_bp

    app.register_blueprint(dashboards_bp)

    # Health endpoint
    @app.route("/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "healthy",
                "version": settings.app.version,
                "environment": settings.app.environment,
            }
        )

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

    # Security headers
    @app.after_request
    def add_security_headers(response: Any) -> Any:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # Context processors
    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "app_version": settings.app.version,
            "app_name": settings.app.name,
            "environment": settings.app.environment,
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
