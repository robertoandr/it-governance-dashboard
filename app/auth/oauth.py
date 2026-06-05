"""OAuth2 client for Microsoft Entra ID using MSAL."""

from __future__ import annotations

import hashlib
import secrets
from base64 import urlsafe_b64encode
from typing import Any

import msal
import structlog

from app.auth.models import AuthenticatedUser, TokenResponse
from config import AzureSettings

log = structlog.get_logger(__name__)


def _build_msal_app(settings: AzureSettings) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.client_id,
        client_credential=settings.client_secret.get_secret_value(),
        authority=settings.authority,
    )


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE."""
    verifier = secrets.token_urlsafe(96)[:128]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def build_auth_url(
    settings: AzureSettings,
    state: str,
    code_challenge: str,
    nonce: str,
) -> str:
    """Build the Microsoft authorization URL with PKCE and state."""
    app = _build_msal_app(settings)
    result = app.get_authorization_request_url(
        scopes=settings.scopes,
        state=state,
        redirect_uri=settings.redirect_uri,
        nonce=nonce,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return result


def exchange_code_for_token(
    settings: AzureSettings,
    code: str,
    code_verifier: str,
) -> TokenResponse:
    """Exchange authorization code for tokens.

    Raises ValueError on failure (MSAL error response).
    """
    app = _build_msal_app(settings)
    result: dict[str, Any] = app.acquire_token_by_authorization_code(
        code=code,
        scopes=settings.scopes,
        redirect_uri=settings.redirect_uri,
        code_verifier=code_verifier,
    )

    if "error" in result:
        log.error(
            "auth.token_exchange.failed",
            error=result.get("error"),
            description=result.get("error_description"),
        )
        raise ValueError(f"Token exchange failed: {result.get('error_description', result['error'])}")

    return TokenResponse(
        access_token=result["access_token"],
        id_token=result.get("id_token", ""),
        token_type=result.get("token_type", "Bearer"),
        expires_in=result.get("expires_in", 3600),
        scope=result.get("scope", ""),
        id_token_claims=result.get("id_token_claims", {}),
    )


def validate_id_token_claims(
    claims: dict,
    settings: AzureSettings,
    nonce: str,
) -> None:
    """Validate critical id_token claims.

    MSAL already validates signature, aud, iss, and exp.
    This layer enforces nonce (anti-replay) and tenant binding.

    Raises ValueError with a descriptive message on any violation.
    """
    expected_iss = f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0"

    if claims.get("aud") != settings.client_id:
        raise ValueError(f"id_token aud mismatch: got {claims.get('aud')!r}")

    if claims.get("iss") != expected_iss:
        raise ValueError(f"id_token iss mismatch: got {claims.get('iss')!r}")

    if claims.get("nonce") != nonce:
        raise ValueError("id_token nonce mismatch (possible replay attack)")

    if claims.get("tid") != settings.tenant_id:
        raise ValueError(f"id_token tid mismatch: got {claims.get('tid')!r}")

    if not claims.get("oid"):
        raise ValueError("id_token missing required claim: oid")


def build_authenticated_user(claims: dict) -> AuthenticatedUser:
    return AuthenticatedUser.from_id_token_claims(claims)
