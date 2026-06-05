"""Flask session helpers for auth state."""

from __future__ import annotations

from flask import session

from itgov.auth.models import AuthenticatedUser

_SESSION_KEY = "auth_user"
_NEXT_URL_KEY = "auth_next"


def save_user(user: AuthenticatedUser) -> None:
    session[_SESSION_KEY] = user.to_session_dict()
    session.permanent = True


def get_current_user() -> AuthenticatedUser | None:
    data = session.get(_SESSION_KEY)
    if not data:
        return None
    try:
        return AuthenticatedUser.from_session_dict(data)
    except Exception:
        return None


def clear_user() -> None:
    session.pop(_SESSION_KEY, None)
    session.pop(_NEXT_URL_KEY, None)


def save_next_url(url: str) -> None:
    session[_NEXT_URL_KEY] = url


def pop_next_url(default: str = "/") -> str:
    return session.pop(_NEXT_URL_KEY, default)
