"""Home em 3 níveis: score global → operação → pilares, com assets servidos localmente."""

from __future__ import annotations

from flask import url_for

_VENDOR = ("tailwind.js", "alpine-csp.min.js", "chart.umd.min.js")


def _home(authed_client) -> str:
    resp = authed_client.get("/gov/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_home_mostra_os_tres_niveis_na_ordem(authed_client) -> None:
    html = _home(authed_client)
    score = html.index("Score Global de Governança")
    operacao = html.index(">Operação</h2>")
    pilares = html.index(">Pilares</h2>")
    assert score < operacao < pilares


def test_tiles_de_operacao_tem_fonte_explicada(authed_client) -> None:
    html = _home(authed_client)
    for label in ("Disponibilidade", "Incidentes Críticos", "MTTR"):
        assert label in html
    assert 'aria-label="Fonte deste dado"' in html


def test_assets_servidos_localmente_sem_cdn(authed_client) -> None:
    html = _home(authed_client)
    assert "/static/vendor/tailwind.js" in html
    assert "/static/vendor/alpine-csp.min.js" in html
    assert "/static/vendor/chart.umd.min.js" in html
    assert "cdn.tailwindcss.com" not in html
    assert "cdn.jsdelivr.net" not in html


def test_login_tambem_sem_cdn(factory_app) -> None:
    html = factory_app.test_client().get("/gov/login").get_data(as_text=True)
    assert "/static/vendor/tailwind.js" in html
    assert "cdn.tailwindcss.com" not in html


def test_assets_vendor_existem(factory_app) -> None:
    client = factory_app.test_client()
    for name in _VENDOR:
        with factory_app.test_request_context():
            path = url_for("static", filename=f"vendor/{name}")
        assert client.get(path).status_code == 200, path
