"""Integration tests for /auth/* routes."""

from __future__ import annotations

from unittest.mock import patch

from itgov.auth.models import AuthenticatedUser, TokenResponse

from .conftest import SAMPLE_CLAIMS

# ─── helpers ──────────────────────────────────────────────────────────────────


def _inject_user(client, app) -> AuthenticatedUser:
    """Save a valid user into the session via a real request context."""
    user = AuthenticatedUser.from_id_token_claims(SAMPLE_CLAIMS)
    with app.test_request_context("/"), client.session_transaction() as sess:
        sess["auth_user"] = user.to_session_dict()
    return user


# ─── /auth/login ──────────────────────────────────────────────────────────────


def test_login_redirects_to_microsoft(auth_client):
    fake_url = "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/authorize?foo=bar"
    with patch("itgov.auth.routes.build_auth_url", return_value=fake_url):
        resp = auth_client.get("/auth/login")
    assert resp.status_code == 302
    assert "login.microsoftonline.com" in resp.headers["Location"]


def test_login_stores_state_and_pkce_in_session(auth_client):
    fake_url = "https://login.microsoftonline.com/x"
    with patch("itgov.auth.routes.build_auth_url", return_value=fake_url):
        auth_client.get("/auth/login")
    with auth_client.session_transaction() as sess:
        assert "oauth_state" in sess
        assert "pkce_verifier" in sess
        assert "oauth_nonce" in sess


# ─── /auth/callback ───────────────────────────────────────────────────────────


def _seed_session(client, state="valid-state", verifier="v3r1f13r", nonce="n0nc3"):
    with client.session_transaction() as sess:
        sess["oauth_state"] = state
        sess["pkce_verifier"] = verifier
        sess["oauth_nonce"] = nonce


def _make_token_response(nonce: str = "n0nc3") -> TokenResponse:
    claims = {**SAMPLE_CLAIMS, "nonce": nonce}
    return TokenResponse(
        access_token="acc-token",
        id_token="id-token",
        id_token_claims=claims,
    )


def test_callback_with_valid_code_creates_session(auth_app, auth_client):
    _seed_session(auth_client)
    token = _make_token_response()

    with (
        patch("itgov.auth.routes.exchange_code_for_token", return_value=token),
        patch("itgov.auth.routes.validate_id_token_claims"),
        patch(
            "itgov.auth.routes.build_authenticated_user",
            return_value=AuthenticatedUser.from_id_token_claims(SAMPLE_CLAIMS),
        ),
    ):
        resp = auth_client.get("/auth/callback?code=auth_code&state=valid-state")

    assert resp.status_code in (302, 200)
    with auth_client.session_transaction() as sess:
        assert "auth_user" in sess


def test_callback_rejects_invalid_state(auth_client):
    _seed_session(auth_client, state="expected-state")
    resp = auth_client.get("/auth/callback?code=x&state=WRONG")
    assert resp.status_code in (302, 400)


def test_callback_rejects_missing_session(auth_client):
    resp = auth_client.get("/auth/callback?code=x&state=state")
    assert resp.status_code in (302, 400)


def test_callback_rejects_token_exchange_failure(auth_client):
    _seed_session(auth_client)
    with patch("itgov.auth.routes.exchange_code_for_token", side_effect=ValueError("exchange failed")):
        resp = auth_client.get("/auth/callback?code=x&state=valid-state")
    assert resp.status_code in (302, 400)


def test_callback_rejects_invalid_token_claims(auth_client):
    _seed_session(auth_client)
    token = _make_token_response()
    with (
        patch("itgov.auth.routes.exchange_code_for_token", return_value=token),
        patch("itgov.auth.routes.validate_id_token_claims", side_effect=ValueError("aud mismatch")),
    ):
        resp = auth_client.get("/auth/callback?code=x&state=valid-state")
    assert resp.status_code in (302, 400)


def test_callback_handles_microsoft_error(auth_client):
    resp = auth_client.get("/auth/callback?error=access_denied&error_description=user+cancelled")
    assert resp.status_code == 302
    assert "login-failed" in resp.headers["Location"]


# ─── /auth/me ─────────────────────────────────────────────────────────────────


def test_me_returns_401_without_session(auth_client):
    resp = auth_client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_user_info_when_authenticated(auth_app, auth_client):
    user = AuthenticatedUser.from_id_token_claims(SAMPLE_CLAIMS)
    with auth_client.session_transaction() as sess:
        sess["auth_user"] = user.to_session_dict()

    resp = auth_client.get("/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["email"] == "alice@contoso.com"
    assert data["oid"] == "oid-abc-123"
    assert data["tenant_id"] == "test-tenant-id"


# ─── /auth/logout ─────────────────────────────────────────────────────────────


def test_logout_clears_session(auth_app, auth_client):
    user = AuthenticatedUser.from_id_token_claims(SAMPLE_CLAIMS)
    with auth_client.session_transaction() as sess:
        sess["auth_user"] = user.to_session_dict()

    resp = auth_client.get("/auth/logout")
    assert resp.status_code == 302
    assert "microsoftonline.com" in resp.headers["Location"]

    with auth_client.session_transaction() as sess:
        assert "auth_user" not in sess


def test_logout_redirects_to_ms_logout(auth_client):
    resp = auth_client.get("/auth/logout")
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert "logout" in loc


# ─── /auth/login-failed ───────────────────────────────────────────────────────


def test_login_failed_returns_401(auth_client):
    resp = auth_client.get("/auth/login-failed?reason=state_mismatch")
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["reason"] == "state_mismatch"
