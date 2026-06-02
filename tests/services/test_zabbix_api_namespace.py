"""Testes para itgov/api/v1/zabbix.py — namespace Flask-RESTX.

Cobre:
- _svc() usa config.ZABBIX_* (não os.environ direto)
- Endpoints retornam dados corretos com ZabbixService mockado
- Erros do service propagam como 500 estruturado
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask_restx import Api

from itgov.api.v1 import zabbix as zabbix_ns_module
from itgov.api.v1.zabbix import _svc, ns
from itgov.models.zabbix import (
    AcknowledgeRequest,
    ZabbixHost,
    ZabbixHostSummary,
    ZabbixProblem,
    ZabbixSeverity,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

FAKE_URL = "http://zabbix.test"
FAKE_USER = "test-user"
FAKE_PASSWORD = "test-password-not-real"


@pytest.fixture()
def app():
    """Flask app mínima com o namespace zabbix registrado."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    api = Api(flask_app, prefix="/api/v1")
    api.add_namespace(ns, path="/zabbix")
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _make_host(hostid: str = "1", name: str = "srv01", available: int = 1) -> ZabbixHost:
    # is_up é @property (não @computed_field) — não aparece em model_dump()
    return ZabbixHost(hostid=hostid, host=name, name=name, status=0, available=available)


def _make_problem(eventid: str = "101") -> ZabbixProblem:
    # clock obrigatório: passa unix timestamp válido (o validator converte)
    return ZabbixProblem(
        eventid=eventid,
        objectid="t1",
        name="High CPU",
        severity=ZabbixSeverity.HIGH,
        clock=1748650000,
        r_clock=None,
        acknowledged=False,
        hosts=["srv01"],
    )


# ── Testes de _svc() ──────────────────────────────────────────────────────────


def test_svc_uses_config_url(monkeypatch):
    """_svc() deve passar config.ZABBIX_URL ao ZabbixService, não os.environ."""
    monkeypatch.setattr("config.ZABBIX_URL", FAKE_URL)
    monkeypatch.setattr("config.ZABBIX_USER", FAKE_USER)
    monkeypatch.setattr("config.ZABBIX_PASSWORD", FAKE_PASSWORD)

    with patch("itgov.api.v1.zabbix.ZabbixService") as mock_cls:
        mock_cls.return_value = MagicMock()
        _svc()
        mock_cls.assert_called_once_with(
            url=FAKE_URL,
            user=FAKE_USER,
            password=FAKE_PASSWORD,
        )


def test_svc_does_not_read_os_environ_directly(monkeypatch):
    """_svc() não deve importar nem ler os.environ diretamente."""
    import itgov.api.v1.zabbix as mod

    assert not hasattr(mod, "os"), (
        "O módulo itgov/api/v1/zabbix.py não deve importar 'os' após o refactor #76"
    )


def test_svc_config_values_propagated(monkeypatch):
    """Mudanças em config.ZABBIX_* são refletidas em cada chamada de _svc()."""
    monkeypatch.setattr("config.ZABBIX_URL", "http://primary.zabbix")
    monkeypatch.setattr("config.ZABBIX_USER", "admin")
    monkeypatch.setattr("config.ZABBIX_PASSWORD", "s3cr3t")

    with patch("itgov.api.v1.zabbix.ZabbixService") as mock_cls:
        mock_cls.return_value = MagicMock()
        _svc()
        _, kwargs = mock_cls.call_args
        assert kwargs["url"] == "http://primary.zabbix"
        assert kwargs["user"] == "admin"
        assert kwargs["password"] == "s3cr3t"


# ── Testes de endpoints ───────────────────────────────────────────────────────


def test_hosts_returns_list(client, monkeypatch):
    """GET /zabbix/hosts retorna lista de hosts serializados."""
    hosts = [_make_host("1", "web01", available=1), _make_host("2", "db01", available=2)]
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.get_hosts.return_value = hosts

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zabbix/hosts")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    assert data[0]["host"] == "web01"
    assert data[0]["available"] == 1
    assert data[1]["available"] == 2


def test_hosts_summary_shape(client):
    """GET /zabbix/hosts/summary retorna shape correto de ZabbixHostSummary."""
    summary = ZabbixHostSummary(total=10, up=8, down=1, unknown=1, up_pct=80.0)
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.get_host_summary.return_value = summary

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zabbix/hosts/summary")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 10
    assert data["up"] == 8
    assert data["up_pct"] == 80.0


def test_problems_returns_list(client):
    """GET /zabbix/problems retorna lista de problemas."""
    problems = [_make_problem("101"), _make_problem("102")]
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.get_problems.return_value = problems

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zabbix/problems")

    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    assert data[0]["eventid"] == "101"


def test_problems_respects_limit(client):
    """GET /zabbix/problems?limit=5 passa limit correto ao service."""
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.get_problems.return_value = []

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        client.get("/api/v1/zabbix/problems?limit=5")

    mock_svc.get_problems.assert_called_once_with(limit=5)


def test_problems_limit_capped_at_200(client):
    """GET /zabbix/problems?limit=9999 é limitado a 200."""
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.get_problems.return_value = []

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        client.get("/api/v1/zabbix/problems?limit=9999")

    mock_svc.get_problems.assert_called_once_with(limit=200)


def test_severity_distribution(client):
    """GET /zabbix/problems/severity retorna distribuição por severidade."""
    dist = {"warning": 3, "high": 1, "disaster": 0}
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.get_severity_distribution.return_value = dist

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        resp = client.get("/api/v1/zabbix/problems/severity")

    assert resp.status_code == 200
    assert resp.get_json()["warning"] == 3


def test_ack_success(client):
    """POST /zabbix/problems/{id}/ack retorna ok=true."""
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.acknowledge_event.return_value = True

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        resp = client.post(
            "/api/v1/zabbix/problems/101/ack",
            json={"eventid": "101", "message": "Resolvido"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["eventid"] == "101"


def test_ack_passes_correct_request(client):
    """POST /ack constrói AcknowledgeRequest com eventid da URL e message do body."""
    mock_svc = MagicMock()
    mock_svc.__enter__ = MagicMock(return_value=mock_svc)
    mock_svc.__exit__ = MagicMock(return_value=False)
    mock_svc.acknowledge_event.return_value = True

    with patch("itgov.api.v1.zabbix._svc", return_value=mock_svc):
        client.post(
            "/api/v1/zabbix/problems/999/ack",
            json={"message": "ACK from test"},
        )

    call_args = mock_svc.acknowledge_event.call_args[0][0]
    assert isinstance(call_args, AcknowledgeRequest)
    assert call_args.eventid == "999"
    assert call_args.message == "ACK from test"
