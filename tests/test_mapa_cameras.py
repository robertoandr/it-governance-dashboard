"""Mapa de Câmeras embutido: página, auth_request do nginx e login ?next."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

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


NGINX_CONF = Path(__file__).resolve().parent.parent / "docker" / "nginx" / "nginx.conf"

# Trechos reais do app.js/index.html do MapaCameras (01/10/2026): rótulos que
# devem mudar e chaves de campo ("gravador") que não podem ser tocadas.
MAPA_JS = (
    "const KIND = { switch: 'Switch', device: 'Gravador (NVR/DVR)' };\n"
    "const KIND_PL = { switch: 'Switches', device: 'Gravadores' };\n"
    "it.kind === 'device' ? (it.values.equip || 'Gravador') : x;\n"
    "o.values.gravador && 'Gravador: ' + linkName(o.values.gravador);\n"
    "else { add('Gravador', title(dev)); }\n"
    "const portKeys = (dev) => (dev.kind === 'switch' ? ['switch', 'porta_sw'] : ['gravador', 'porta']);\n"
    "d.group === 'gravador' ? 'gravador(es)' : 'planta(s)';\n"
    '<label id="fDevL">Gravador <select id="fDev"></select></label>\n'
)


def _mapa_location() -> str:
    """Bloco ``location /mapa-cameras/`` do nginx.conf de produção."""
    conf = NGINX_CONF.read_text(encoding="utf-8")
    match = re.search(r"location /mapa-cameras/ \{(.*?)\n        \}", conf, re.S)
    assert match, "location /mapa-cameras/ não encontrado no nginx.conf"
    return match.group(1)


def _sub_filters() -> list[tuple[str, str]]:
    """Pares (procura, troca) das diretivas ``sub_filter`` do bloco."""
    quoted = r"""("[^"]*"|'[^']*')"""
    pairs = re.findall(rf"^\s*sub_filter\s+{quoted}\s+{quoted};", _mapa_location(), re.M)
    return [(a[1:-1], b[1:-1]) for a, b in pairs]


def _apply(text: str) -> str:
    """Aplica os sub_filter como o nginx: sem diferenciar maiúsculas."""
    for old, new in _sub_filters():
        text = re.sub(re.escape(old), lambda _m, n=new: n, text, flags=re.I)
    return text


class TestDispositivosDeInfra:
    """nginx troca "Gravadores" por "Dispositivos de Infra" na tela do Mapa."""

    def test_resposta_sem_compressao_e_js_filtrado(self) -> None:
        bloco = _mapa_location()
        # Com gzip vindo do upstream o sub_filter não enxerga o texto.
        assert 'proxy_set_header Accept-Encoding "";' in bloco
        assert "sub_filter_types text/javascript;" in bloco
        assert "sub_filter_once off;" in bloco

    def test_rotulos_trocados(self) -> None:
        out = _apply(MAPA_JS)
        assert "device: 'Dispositivo de Infra'" in out
        assert "device: 'Dispositivos de Infra'" in out
        assert "|| 'Dispositivo de Infra')" in out
        assert "'Dispositivo de Infra: ' + linkName" in out
        assert "add('Dispositivo de Infra', title(dev))" in out
        assert '>Dispositivo de Infra <select id="fDev">' in out
        assert "Gravador" not in out

    def test_chaves_de_campo_intactas(self) -> None:
        out = _apply(MAPA_JS)
        assert out.count("values.gravador") == 2
        assert "['gravador', 'porta']" in out
        assert "d.group === 'gravador' ? 'gravador(es)'" in out

    def test_padroes_nao_casam_chave_minuscula(self) -> None:
        # sub_filter ignora maiúsculas: nenhum padrão pode ser só 'gravador'.
        for old, _new in _sub_filters():
            assert old.lower() not in ("gravador", "'gravador'", "gravadores")
