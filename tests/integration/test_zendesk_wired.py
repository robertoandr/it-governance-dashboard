"""Integration test: Zendesk blueprint registered in Flask app.

Regression guard for issue #68 — blueprint was defined but not
wired into app.py, causing 404 in production despite unit tests
passing.

Isolation: uses Flask test client against the real app; Zendesk
credentials are fake — we only care about routing, not data.
A 404 means the blueprint is missing; any other status proves routing works.
"""

from __future__ import annotations

import os

import pytest
from flask.testing import FlaskClient


@pytest.fixture()
def client() -> FlaskClient:
    """Flask test client with minimal env for app import.

    PROPAGATE_EXCEPTIONS=False: Flask returns HTTP 500 for handler errors
    instead of bubbling them up. This lets us check status codes — we care
    that the route is FOUND (not 404), not that credentials are valid.
    """
    os.environ.setdefault("ZABBIX_FRONT_URL", "http://localhost:8080")
    import app as flask_module

    flask_module.app.config["TESTING"] = True
    flask_module.app.config["PROPAGATE_EXCEPTIONS"] = False
    return flask_module.app.test_client()


def test_zendesk_csat_endpoint_is_wired(client: FlaskClient) -> None:
    """GET /api/v1/zendesk/csat must be routed — not 404.

    200 (no auth) or 500 (missing env creds) both prove the blueprint
    is registered. 404 means it is not wired. This test would have
    caught the bug found in Sprint 10G /verify.
    """
    response = client.get("/api/v1/zendesk/csat")
    assert response.status_code != 404, (
        "Zendesk blueprint not registered in app.py — see issue #68. "
        f"Got {response.status_code}: {response.get_data(as_text=True)[:200]}"
    )


def test_zabbix_hosts_endpoint_is_wired(client: FlaskClient) -> None:
    """GET /api/v1/zabbix/hosts must be routed — not 404.

    Ensures the zabbix namespace is also wired (was missing alongside zendesk).
    """
    response = client.get("/api/v1/zabbix/hosts")
    assert response.status_code != 404, (
        "Zabbix blueprint not registered in app.py. "
        f"Got {response.status_code}: {response.get_data(as_text=True)[:200]}"
    )
