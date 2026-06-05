"""Unit tests for oauth.py — PKCE, state, token validation."""

from __future__ import annotations

import hashlib
from base64 import urlsafe_b64encode
from unittest.mock import MagicMock, patch

import pytest

from itgov.auth.oauth import (
    build_auth_url,
    build_authenticated_user,
    exchange_code_for_token,
    generate_pkce_pair,
    generate_state,
    validate_id_token_claims,
)

from .conftest import SAMPLE_CLAIMS


def test_generate_state_is_unique():
    states = {generate_state() for _ in range(50)}
    assert len(states) == 50


def test_generate_state_min_length():
    for _ in range(20):
        assert len(generate_state()) >= 32


def test_pkce_verifier_length():
    verifier, _ = generate_pkce_pair()
    assert 43 <= len(verifier) <= 128


def test_pkce_challenge_is_sha256_of_verifier():
    verifier, challenge = generate_pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected


def test_pkce_pairs_are_unique():
    pairs = {generate_pkce_pair() for _ in range(20)}
    assert len(pairs) == 20


def test_validate_id_token_claims_valid(azure_settings):
    validate_id_token_claims(SAMPLE_CLAIMS.copy(), azure_settings, nonce="test-nonce-value")


def test_validate_id_token_claims_wrong_aud(azure_settings):
    claims = {**SAMPLE_CLAIMS, "aud": "wrong-client"}
    with pytest.raises(ValueError, match="aud mismatch"):
        validate_id_token_claims(claims, azure_settings, nonce="test-nonce-value")


def test_validate_id_token_claims_wrong_iss(azure_settings):
    claims = {**SAMPLE_CLAIMS, "iss": "https://evil.example.com"}
    with pytest.raises(ValueError, match="iss mismatch"):
        validate_id_token_claims(claims, azure_settings, nonce="test-nonce-value")


def test_validate_id_token_claims_nonce_mismatch(azure_settings):
    claims = {**SAMPLE_CLAIMS, "nonce": "wrong-nonce"}
    with pytest.raises(ValueError, match="nonce mismatch"):
        validate_id_token_claims(claims, azure_settings, nonce="test-nonce-value")


def test_validate_id_token_claims_wrong_tid(azure_settings):
    claims = {**SAMPLE_CLAIMS, "tid": "other-tenant"}
    with pytest.raises(ValueError, match="tid mismatch"):
        validate_id_token_claims(claims, azure_settings, nonce="test-nonce-value")


def test_validate_id_token_claims_missing_oid(azure_settings):
    claims = {k: v for k, v in SAMPLE_CLAIMS.items() if k != "oid"}
    with pytest.raises(ValueError, match="oid"):
        validate_id_token_claims(claims, azure_settings, nonce="test-nonce-value")


# ─── build_auth_url ───────────────────────────────────────────────────────────


def test_build_auth_url_calls_msal(azure_settings):
    fake_url = "https://login.microsoftonline.com/test/authorize?foo=bar"
    mock_app = MagicMock()
    mock_app.get_authorization_request_url.return_value = fake_url

    with patch("itgov.auth.oauth._build_msal_app", return_value=mock_app):
        result = build_auth_url(azure_settings, state="s", code_challenge="c", nonce="n")

    assert result == fake_url
    mock_app.get_authorization_request_url.assert_called_once()


def test_build_auth_url_passes_scopes(azure_settings):
    mock_app = MagicMock()
    mock_app.get_authorization_request_url.return_value = "https://x"

    with patch("itgov.auth.oauth._build_msal_app", return_value=mock_app):
        build_auth_url(azure_settings, state="s", code_challenge="c", nonce="n")

    call_kwargs = mock_app.get_authorization_request_url.call_args
    assert "scopes" in call_kwargs.kwargs or len(call_kwargs.args) > 0


# ─── exchange_code_for_token ──────────────────────────────────────────────────


def test_exchange_code_for_token_success(azure_settings):
    mock_app = MagicMock()
    mock_app.acquire_token_by_authorization_code.return_value = {
        "access_token": "acc",
        "id_token": "idt",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "openid",
        "id_token_claims": SAMPLE_CLAIMS,
    }

    with patch("itgov.auth.oauth._build_msal_app", return_value=mock_app):
        token = exchange_code_for_token(azure_settings, code="c", code_verifier="v")

    assert token.access_token == "acc"
    assert token.id_token_claims == SAMPLE_CLAIMS


def test_exchange_code_for_token_raises_on_error(azure_settings):
    mock_app = MagicMock()
    mock_app.acquire_token_by_authorization_code.return_value = {
        "error": "invalid_grant",
        "error_description": "Code expired",
    }

    with (
        patch("itgov.auth.oauth._build_msal_app", return_value=mock_app),
        pytest.raises(ValueError, match="Code expired"),
    ):
        exchange_code_for_token(azure_settings, code="bad", code_verifier="v")


# ─── build_authenticated_user ─────────────────────────────────────────────────


def test_build_authenticated_user_from_claims():
    user = build_authenticated_user(SAMPLE_CLAIMS)
    assert user.oid == "oid-abc-123"
    assert user.email == "alice@contoso.com"
    assert user.tenant_id == "test-tenant-id"
