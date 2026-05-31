"""Testes de headers de segurança HTTP.

Valida que Flask-Talisman e configurações de sessão estão corretos.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient


@pytest.fixture()
def secure_app() -> Flask:
    """App Flask mínimo com middleware de segurança em modo testing."""
    import os

    os.environ.setdefault("FLASK_ENV", "testing")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-not-real")

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key-not-real"

    from app.middleware.security import init_security

    init_security(app)

    @app.route("/ping")
    def ping():
        return "pong"

    @app.route("/api/test")
    def api_test():
        return {"status": "ok"}

    return app


@pytest.fixture()
def client(secure_app: Flask) -> FlaskClient:
    return secure_app.test_client()


class TestSecurityHeaders:
    """Valida presença e valores dos headers de segurança."""

    def test_x_content_type_options(self, client: FlaskClient) -> None:
        r = client.get("/ping")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_xss_protection(self, client: FlaskClient) -> None:
        r = client.get("/ping")
        assert "X-XSS-Protection" in r.headers

    def test_x_frame_options_sameorigin(self, client: FlaskClient) -> None:
        r = client.get("/ping")
        assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_content_security_policy_present(self, client: FlaskClient) -> None:
        r = client.get("/ping")
        csp = r.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp
        assert "object-src 'none'" in csp

    def test_csp_blocks_external_scripts(self, client: FlaskClient) -> None:
        r = client.get("/ping")
        csp = r.headers.get("Content-Security-Policy", "")
        assert "script-src" in csp
        # Não deve permitir fontes externas arbitrárias
        assert "http://*" not in csp
        assert "https://*" not in csp

    def test_referrer_policy(self, client: FlaskClient) -> None:
        r = client.get("/ping")
        assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_no_https_in_testing(self, client: FlaskClient) -> None:
        """Em modo testing, não deve redirecionar para HTTPS."""
        r = client.get("/ping")
        assert r.status_code == 200

    def test_strict_transport_security_absent_in_testing(self, client: FlaskClient) -> None:
        """HSTS não deve ser enviado em testing (sem HTTPS)."""
        r = client.get("/ping")
        assert "Strict-Transport-Security" not in r.headers


class TestSessionSecurity:
    """Valida configuração de cookies de sessão."""

    def test_session_cookie_httponly(self, secure_app: Flask) -> None:
        assert secure_app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_session_cookie_samesite(self, secure_app: Flask) -> None:
        assert secure_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_session_lifetime(self, secure_app: Flask) -> None:
        assert secure_app.config["PERMANENT_SESSION_LIFETIME"] == 3600
