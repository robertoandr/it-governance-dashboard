"""Tests for @require_auth decorator and before_request middleware."""

from __future__ import annotations

import pytest
from flask import Flask, jsonify
from flask_session import Session

from itgov.auth.decorators import require_auth
from itgov.auth.models import AuthenticatedUser

from .conftest import SAMPLE_CLAIMS


@pytest.fixture()
def decorated_app(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "pytest-decorator-secret"
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = str(tmp_path)
    app.config["SESSION_COOKIE_SECURE"] = False
    Session(app)

    from itgov.auth.routes import auth_bp

    app.register_blueprint(auth_bp)

    @app.route("/protected")
    @require_auth
    def protected():
        return jsonify({"ok": True})

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/data")
    @require_auth
    def api_data():
        return jsonify({"data": "secret"})

    return app


@pytest.fixture()
def decorated_client(decorated_app):
    with decorated_app.test_client() as c:
        yield c


def test_protected_route_redirects_when_unauthenticated(decorated_client):
    resp = decorated_client.get("/protected")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_protected_route_accessible_when_authenticated(decorated_client):
    user = AuthenticatedUser.from_id_token_claims(SAMPLE_CLAIMS)
    with decorated_client.session_transaction() as sess:
        sess["auth_user"] = user.to_session_dict()

    resp = decorated_client.get("/protected")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_health_is_public_no_auth_required(decorated_client):
    resp = decorated_client.get("/health")
    assert resp.status_code == 200


def test_api_route_returns_401_json_when_unauthenticated(decorated_client):
    resp = decorated_client.get("/api/data", headers={"Accept": "application/json"})
    assert resp.status_code == 401
    data = resp.get_json()
    assert "error" in data
    assert "login_url" in data


def test_require_auth_saves_next_url(decorated_client):
    decorated_client.get("/protected?foo=bar")
    with decorated_client.session_transaction() as sess:
        assert "auth_next" in sess
