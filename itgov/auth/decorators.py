"""Auth decorators for protecting Flask routes."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import structlog
from flask import redirect, request, url_for

from itgov.auth.session import get_current_user, save_next_url

log = structlog.get_logger(__name__)


def require_auth(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Redirect unauthenticated requests to /auth/login.

    On API requests (Accept: application/json or /api/* prefix) returns 401
    instead of redirecting so that AJAX callers receive a machine-readable error.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if user is None:
            _is_api = request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", "")
            if _is_api:
                from flask import jsonify

                return jsonify({"error": "Unauthorized", "login_url": url_for("auth.login")}), 401
            save_next_url(request.url)
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)

    return wrapper
