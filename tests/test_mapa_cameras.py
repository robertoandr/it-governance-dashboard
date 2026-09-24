"""Mapa de Câmeras embutido: página, auth_request do nginx e login ?next."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient


@pytest.fixture
def client_as(factory_app: Flask) -> Iterator[Callable[[str], FlaskClient]]:
    """Return a factory that yields a test client logged in with the given role."""
    from app.extensions import db
    from app.models.user import User

    clients: list[FlaskClient] = []

    def _make(role: str) -> FlaskClient:
        email = f"pytest-mapa-{role}@test.local"
        with factory_app.app_context():
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(name=f"Pytest {role}", email=email, role=role)
                user.set_password("pytest-only-not-real")
                db.session.add(user)
                db.session.commit()
            user_id = user.id
        client = factory_app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        clients.append(client)
        return client

    yield _make


class TestAuthSubrequest:
    """/gov/cameras/auth — consultado pelo nginx via auth_request."""

    @pytest.mark.parametrize("role", ["admin", "gestor"])
    def test_allowed_roles_get_204(self, client_as: Callable[[str], FlaskClient], role: str) -> None:
        resp = client_as(role).get("/gov/cameras/auth")
        assert resp.status_code == 204
        assert resp.data == b""

    @pytest.mark.parametrize("role", ["operador", "visualizador"])
    def test_other_roles_get_403(self, client_as: Callable[[str], FlaskClient], role: str) -> None:
        assert client_as(role).get("/gov/cameras/auth").status_code == 403

    def test_anonymous_gets_401_not_redirect(self, factory_client: FlaskClient) -> None:
        # auth_request só entende 2xx/401/403 — um 302 viraria 500 no nginx.
        assert factory_client.get("/gov/cameras/auth").status_code == 401


class TestPage:
    """/gov/cameras — página com o iframe."""

    @pytest.mark.parametrize("role", ["admin", "gestor"])
    def test_allowed_roles_see_iframe(self, client_as: Callable[[str], FlaskClient], role: str) -> None:
        resp = client_as(role).get("/gov/cameras")
        assert resp.status_code == 200
        assert b'<iframe src="/mapa-cameras/"' in resp.data

    def test_operador_forbidden(self, client_as: Callable[[str], FlaskClient]) -> None:
        assert client_as("operador").get("/gov/cameras").status_code == 403

    def test_anonymous_redirected_to_login(self, factory_client: FlaskClient) -> None:
        resp = factory_client.get("/gov/cameras")
        assert resp.status_code == 302
        assert "/gov/login" in resp.headers["Location"]

    def test_sidebar_link_only_for_allowed_roles(self, client_as: Callable[[str], FlaskClient]) -> None:
        assert b'href="/gov/cameras"' in client_as("gestor").get("/gov/cameras").data
        operador_page = client_as("operador").get("/gov/links")
        assert operador_page.status_code == 200
        assert b'href="/gov/links"' in operador_page.data  # sidebar renderizou
        assert b'href="/gov/cameras"' not in operador_page.data


class TestSessionCookie:
    def test_cookie_names_do_not_collide_with_mapa(self, factory_app: Flask) -> None:
        assert factory_app.config["SESSION_COOKIE_NAME"] == "itgov_session"
        assert factory_app.config["REMEMBER_COOKIE_NAME"] == "itgov_remember"


class TestLoginNext:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("/gov/cameras", "/gov/cameras"),
            ("https://evil.example/", None),
            ("//evil.example/", None),
            ("/\\evil.example", None),
            ("", None),
            (None, None),
        ],
    )
    def test_safe_next(self, target: str | None, expected: str | None) -> None:
        from app.auth.routes import _safe_next

        assert _safe_next(target) == expected

    def test_login_form_keeps_next(self, factory_client: FlaskClient) -> None:
        resp = factory_client.get("/gov/login?next=/gov/cameras")
        assert b'action="/gov/login?next=/gov/cameras"' in resp.data
