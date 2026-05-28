"""IT Governance Dashboard — Flask application factory."""

import structlog
from flask import Flask
from flask_restx import Api

log = structlog.get_logger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application.

    Registers Flask-RESTX Api with Swagger UI at /api/docs and
    all v1 namespaces.

    Returns:
        Flask: Configured application instance.
    """
    app = Flask(__name__)

    from app.config import get_settings

    settings = get_settings()
    app.config["SECRET_KEY"] = settings.secret_key.get_secret_value()
    app.config["DEBUG"] = settings.debug

    api = Api(
        app,
        version="1.0",
        title="IT Governance Dashboard API",
        description="Gestão de fornecedores, contratos, SLA e governança de TI",
        doc="/api/docs",
        prefix="/api/v1",
    )

    from app.api.v1.fornecedores import ns as fornecedores_ns

    api.add_namespace(fornecedores_ns)

    log.info("app.created", version=settings.app_version)
    return app
