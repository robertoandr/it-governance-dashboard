"""Smoke test for the single Flask entry point (app/__init__.py:create_app()).

Context (LL-004, docs/lessons-learned/LL-004-csp-nonce-undefined.md): this
project used to ship two independent Flask entry points — the legacy
`app.py` (via `wsgi_prod.py`) and the factory app (`app/__init__.py`,
via `wsgi.py`/Docker) — with independent configuration. A bug in one did
not reproduce in the other, which caused a real production incident
(HTTP 500 on every HTML route under the factory app while the legacy
app kept working fine).

`app.py` was deleted in PR #195 (2026-08-17) after confirming production
only ever ran the factory app. This test guards two things:

1. The single remaining entry point actually boots and serves traffic.
2. Nobody reintroduces a second entry point (`app.py` / `wsgi_prod.py`)
   at the project root without it being a deliberate, reviewed decision.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Registered route count as of PR #195 (2026-08-17): 70. A tolerant floor
# catches an accidental mass de-registration (e.g. a namespace import
# silently failing) without being brittle to routes added over time.
_MIN_EXPECTED_ROUTES = 65


class TestSingleEntrypointBoots:
    def test_health_endpoint_returns_200(self, factory_client) -> None:
        resp = factory_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "alive"

    def test_registers_expected_route_count(self, factory_app) -> None:
        route_count = len(list(factory_app.url_map.iter_rules()))
        assert route_count >= _MIN_EXPECTED_ROUTES, (
            f"Only {route_count} routes registered (expected >= {_MIN_EXPECTED_ROUTES}). "
            "A namespace or blueprint likely failed to register — check startup "
            "logs for '*_unavailable' warnings (see app/__init__.py's try/except "
            "guards around api.add_namespace calls)."
        )


class TestNoSecondEntrypointRegression:
    """Guards against LL-004 reappearing: only one Flask entry point at root."""

    def test_legacy_app_py_does_not_exist(self) -> None:
        assert not (PROJECT_ROOT / "app.py").exists(), (
            "app.py reappeared at the project root. This project intentionally "
            "has a single Flask entry point (app/__init__.py:create_app(), "
            "see PR #195 and docs/lessons-learned/LL-004-csp-nonce-undefined.md). "
            "A second entry point with independent config caused a real "
            "production incident. If this is deliberate, update this test and "
            "LL-004 explaining why the constraint no longer applies."
        )

    def test_wsgi_prod_does_not_exist(self) -> None:
        assert not (PROJECT_ROOT / "wsgi_prod.py").exists(), (
            "wsgi_prod.py reappeared at the project root. It only ever existed "
            "to load the now-deleted app.py by file path and has no purpose "
            "without it — see the cleanup that removed it."
        )
