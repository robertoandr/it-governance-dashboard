"""Auth blueprint — /auth/login, /auth/callback, /auth/logout, /auth/me."""

from __future__ import annotations

import structlog
from flask import Blueprint, jsonify, redirect, request, session, url_for

from config import get_azure_settings
from itgov.auth.oauth import (
    build_auth_url,
    build_authenticated_user,
    exchange_code_for_token,
    generate_pkce_pair,
    generate_state,
    validate_id_token_claims,
)
from itgov.auth.session import clear_user, get_current_user, pop_next_url, save_user

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
log = structlog.get_logger(__name__)


@auth_bp.route("/login")
def login():  # type: ignore[return]
    """Initiate Authorization Code + PKCE flow."""
    settings = get_azure_settings()

    state = generate_state()
    verifier, challenge = generate_pkce_pair()
    nonce = generate_state()

    session["oauth_state"] = state
    session["pkce_verifier"] = verifier
    session["oauth_nonce"] = nonce

    auth_url = build_auth_url(settings, state=state, code_challenge=challenge, nonce=nonce)

    log.info("auth.login.initiated", redirect_uri=settings.redirect_uri)
    return redirect(auth_url)


@auth_bp.route("/callback")
def callback():  # type: ignore[return]
    """Handle Microsoft redirect with authorization code."""
    settings = get_azure_settings()

    error = request.args.get("error")
    if error:
        desc = request.args.get("error_description", "")
        log.warning("auth.callback.error", error=error, description=desc)
        return redirect(url_for("auth.login_failed", reason=error))

    code = request.args.get("code", "")
    returned_state = request.args.get("state", "")
    expected_state = session.pop("oauth_state", None)
    verifier = session.pop("pkce_verifier", None)
    nonce = session.pop("oauth_nonce", None)

    if not expected_state or returned_state != expected_state:
        log.warning("auth.callback.state_mismatch", returned=returned_state)
        return redirect(url_for("auth.login_failed", reason="state_mismatch")), 400

    if not verifier or not nonce:
        log.warning("auth.callback.missing_session_params")
        return redirect(url_for("auth.login_failed", reason="session_expired")), 400

    try:
        token = exchange_code_for_token(settings, code=code, code_verifier=verifier)
    except ValueError as exc:
        log.warning("auth.callback.token_exchange_failed", error=str(exc))
        return redirect(url_for("auth.login_failed", reason="token_exchange_failed")), 400

    claims = token.id_token_claims
    try:
        validate_id_token_claims(claims, settings, nonce=nonce)
    except ValueError as exc:
        log.warning("auth.callback.token_validation_failed", error=str(exc))
        return redirect(url_for("auth.login_failed", reason="token_invalid")), 400

    user = build_authenticated_user(claims)
    save_user(user)

    log.info(
        "auth.login.success",
        oid=user.oid,
        email=user.email,
        tenant_id=user.tenant_id,
    )

    return redirect(pop_next_url(default="/"))


@auth_bp.route("/logout")
def logout():
    """Clear local session and redirect to Microsoft logout."""
    settings = get_azure_settings()

    user = get_current_user()
    if user:
        log.info("auth.logout", oid=user.oid, email=user.email)

    clear_user()

    post_logout = request.host_url.rstrip("/")
    ms_logout = f"{settings.logout_uri}?post_logout_redirect_uri={post_logout}"
    return redirect(ms_logout)


@auth_bp.route("/me")
def me():
    """Return authenticated user info as JSON."""
    user = get_current_user()
    if user is None:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(
        {
            "oid": user.oid,
            "email": user.email,
            "name": user.name,
            "tenant_id": user.tenant_id,
            "preferred_username": user.preferred_username,
        }
    )


@auth_bp.route("/login-failed")
def login_failed():
    """Show a minimal error page when auth fails."""
    reason = request.args.get("reason", "unknown")
    log.warning("auth.login.failed", reason=reason)
    return jsonify({"error": "Authentication failed", "reason": reason}), 401
