"""Role-based access control decorator."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from flask import abort
from flask_login import current_user


def require_role(*allowed: str) -> Callable:
    """Restrict view to users with one of the allowed roles.

    Returns 401 if not authenticated, 403 if authenticated but role not allowed.
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in allowed:
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
