"""Testes para itgov/api/v1/zendesk.py — namespace Flask-RESTX.

Cobre:
- _svc() usa config.ZENDESK_* (não os.environ direto)
- Endpoints retornam dados corretos com ZendeskService mockado
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_restx import Api

from itgov.api.v1.zendesk import _svc, ns
from itgov.models.zendesk import Ticket, TicketPriority, TicketStatus

# ── Helpers ───────────────────────────────────────────────────────────────────

FAKE_SUBDOMAIN = "test-corp"
FAKE_EMAIL = "test@test.com"
FAKE_TOKEN = "test-token-not-real"

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def app():
    """Flask app mínima com o namespace zendesk registrado."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    api = Api(flask_app, prefix="/api/v1")
    api.add_namespace(ns, path="/zendesk")
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _make_ticket(
    ticket_id: int = 1,
    status: TicketStatus = TicketStatus.OPEN,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        subject=f"Ticket #{ticket_id}",
        status=status,
        priority=TicketPriority.NORMAL,
        created_at=_NOW,
        updated_at=_NOW,
        requester_id=42,
        tags=[],
    )


def _mock_svc_ctx() -> MagicMock:
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ── Testes de _svc() ──────────────────────────────────────────────────────────


def test_svc_uses_config_credentials(monkeypatch):
    """_svc() deve passar config.ZENDESK_* ao ZendeskService, não os.environ."""
    monkeypatch.setattr("config.ZENDESK_SUBDOMAIN", FAKE_SUBDOMAIN)
    monkeypatch.setattr("config.ZENDESK_EMAIL", FAKE_EMAIL)
    monkeypatch.setattr("config.ZENDESK_API_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr("config.ZENDESK_GROUP_ID", 0)  # 0 → group_id=None

    with patch("itgov.api.v1.zendesk.ZendeskService") as mock_cls:
        mock_cls.return_value = MagicMock()
        _svc()
        mock_cls.assert_called_once_with(
            subdomain=FAKE_SUBDOMAIN,
            email=FAKE_EMAIL,
            api_token=FAKE_TOKEN,
            group_id=None,
        )


def test_svc_does_not_import_os():
    """O módulo itgov/api/v1/zendesk.py não deve importar 'os' após refactor #78."""
    import itgov.api.v1.zendesk as mod

    assert not hasattr(
        mod, "os"
    ), "itgov/api/v1/zendesk.py não deve importar 'os' — use config.* para variáveis de ambiente"


def test_svc_config_values_propagated(monkeypatch):
    """Mudanças em config.ZENDESK_* são refletidas em cada chamada de _svc()."""
    monkeypatch.setattr("config.ZENDESK_SUBDOMAIN", "acme")
    monkeypatch.setattr("config.ZENDESK_EMAIL", "ops@acme.com")
    monkeypatch.setattr("config.ZENDESK_API_TOKEN", "secret-token")
    monkeypatch.setattr("config.ZENDESK_GROUP_ID", 0)

    with patch("itgov.api.v1.zendesk.ZendeskService") as mock_cls:
        mock_cls.return_value = MagicMock()
        _svc()
        _, kwargs = mock_cls.call_args
        assert kwargs["subdomain"] == "acme"
        assert kwargs["email"] == "ops@acme.com"
        assert kwargs["api_token"] == "secret-token"


# ── Testes de endpoints ───────────────────────────────────────────────────────


def test_tickets_returns_list(client):
    """GET /zendesk/tickets retorna lista de tickets serializados."""
    tickets = [_make_ticket(1, TicketStatus.OPEN), _make_ticket(2, TicketStatus.NEW)]
    mock_svc = _mock_svc_ctx()
    mock_svc.get_tickets.return_value = tickets

    with patch("itgov.api.v1.zendesk._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zendesk/tickets")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["status"] == "open"
    assert data[1]["status"] == "new"


def test_tickets_passes_status_filter(client):
    """GET /zendesk/tickets?status=open passa o filtro ao service."""
    mock_svc = _mock_svc_ctx()
    mock_svc.get_tickets.return_value = []

    with patch("itgov.api.v1.zendesk._svc", return_value=mock_svc):
        client.get("/api/v1/zendesk/tickets?status=open")

    mock_svc.get_tickets.assert_called_once_with(status="open")


def test_tickets_open_endpoint(client):
    """GET /zendesk/tickets/open retorna apenas tickets abertos."""
    tickets = [_make_ticket(10, TicketStatus.OPEN)]
    mock_svc = _mock_svc_ctx()
    mock_svc.get_open_tickets.return_value = tickets

    with patch("itgov.api.v1.zendesk._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zendesk/tickets/open")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["status"] == "open"


def test_tickets_volume(client):
    """GET /zendesk/tickets/volume retorna distribuição por status."""
    volume = {"new": 2, "open": 5, "pending": 1, "hold": 0, "solved": 10, "closed": 3}
    mock_svc = _mock_svc_ctx()
    mock_svc.get_ticket_volume_by_status.return_value = volume

    with patch("itgov.api.v1.zendesk._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zendesk/tickets/volume")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["open"] == 5
    assert data["solved"] == 10


def test_sla_metrics(client):
    """GET /zendesk/sla retorna métricas de SLA via get_cached_mttr_summary."""
    summary_dict = {
        "total_open": 50,
        "breached": 3,
        "compliance_pct": 94.0,
        "csat_pct": 90.0,
        "csat_good": 27,
        "csat_bad": 3,
        "csat_sample": 30,
        "by_priority": {},
        "oldest_tickets": [],
        "age_buckets": {},
        "resolved_7d": 0,
        "resolved_30d": 0,
        "volume_by_status": {},
    }

    with patch("itgov.api.v1.zendesk.get_cached_mttr_summary", return_value=summary_dict):
        resp = client.get("/api/v1/zendesk/sla")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_tickets"] == 50
    assert data["breached"] == 3
    assert data["compliance_pct"] == 94.0


def test_csat_summary(client):
    """GET /zendesk/csat retorna resumo de CSAT via get_cached_mttr_summary."""
    summary_dict = {
        "total_open": 0,
        "breached": 0,
        "compliance_pct": 100.0,
        "csat_pct": 90.0,
        "csat_good": 27,
        "csat_bad": 3,
        "csat_sample": 30,
        "by_priority": {},
        "oldest_tickets": [],
        "age_buckets": {},
        "resolved_7d": 0,
        "resolved_30d": 0,
        "volume_by_status": {},
    }

    with patch("itgov.api.v1.zendesk.get_cached_mttr_summary", return_value=summary_dict):
        resp = client.get("/api/v1/zendesk/csat")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["good"] == 27
    assert data["csat_pct"] == 90.0
    assert data["sample_size"] == 30
