"""Shared fixtures for auth tests."""

from __future__ import annotations

import time

import pytest

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

_AZURE_ENV = {
    "AZURE_TENANT_ID": "test-tenant-id",
    "AZURE_CLIENT_ID": "test-client-id",
    "AZURE_CLIENT_SECRET": "test-client-secret",
    "AZURE_REDIRECT_URI": "http://localhost:5000/auth/callback",
}


@pytest.fixture(autouse=True)
def _azure_env(monkeypatch):
    """Set AZURE_* env vars for each auth test, cleaned up automatically."""
    for k, v in _AZURE_ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def azure_settings(_azure_env):
    from config import AzureSettings

    return AzureSettings()  # type: ignore[call-arg]


@pytest.fixture()
def auth_app(tmp_path, _azure_env):
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
