"""Shared fixtures for auth tests."""

from __future__ import annotations

import os
import time

import pytest

# Required env vars before any app import
os.environ.setdefault("AZURE_TENANT_ID", "test-tenant-id")
os.environ.setdefault("AZURE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AZURE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("AZURE_REDIRECT_URI", "http://localhost:5000/auth/callback")
os.environ["AZURE_SSO_ENABLED"] = "1"


SAMPLE_CLAIMS = {
    "oid": "oid-abc-123",
    "email": "alice@contoso.com",
    "name": "Alice Smith",
    "tid": "test-tenant-id",
    "preferred_username": "alice@contoso.com",
    "aud": "test-client-id",
    "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
    "nonce": "test-nonce-value",
    "exp": int(time.time()) + 3600,
}


@pytest.fixture()
def azure_settings():
    from config import get_azure_settings

    return get_azure_settings()


@pytest.fixture()
def auth_app(tmp_path):
    """Minimal Flask app with auth_bp registered and filesystem session in tmp_path."""
    from flask import Flask
    from flask_session import Session

    from itgov.auth.routes import auth_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key-pytest"
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = str(tmp_path)
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    Session(app)
    app.register_blueprint(auth_bp)
    return app


@pytest.fixture()
def auth_client(auth_app):
    with auth_app.test_client() as c:
        yield c
