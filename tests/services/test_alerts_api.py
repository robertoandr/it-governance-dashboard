"""Testes do endpoint GET /api/v1/alerts/active."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from itgov.models.db.alert import ActiveAlert


def _ativo(
    host_id: str = "10",
    tipo: str = "DVR_DOWN",
    severidade: str = "critical",
    ramo: str = "Centro",
    filhos: int | None = 3,
) -> ActiveAlert:
    agora = datetime.now(UTC)
    a = ActiveAlert()
    a.host_id = host_id
    a.host_name = f"DVR-{host_id}"
    a.hostgroup = ramo
    a.tipo = tipo
    a.severidade = severidade
    a.msg = f"🔴 [{ramo}] DVR DOWN"
    a.filhos_afetados = filhos
    a.created_at = agora
    a.updated_at = agora
    return a


@pytest.fixture
def client(tmp_path):
    """Flask test client com banco SQLite em memória."""
    import os

    os.environ.setdefault("SECRET_KEY", "test-secret")
    os.environ["DATABASE_URL"] = "sqlite://"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from itgov.models.db.base import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    with (
        patch("itgov.db.session.get_engine", return_value=engine),
        patch("itgov.db.session.get_session", return_value=Session(engine)),
    ):
        from flask import Flask
        from flask_restx import Api

        from itgov.api.v1.alerts import ns

        app = Flask(__name__)
        api = Api(app, prefix="/api/v1")
        api.add_namespace(ns)
        app.config["TESTING"] = True
        yield app.test_client()


class TestAlertsActiveEndpoint:
    def test_retorna_200_lista_vazia(self, client) -> None:
        with patch("itgov.api.v1.alerts.get_session") as mock_sess:
            sess = MagicMock()
            sess.__enter__ = lambda s: s
            sess.__exit__ = MagicMock(return_value=False)
            sess.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
            sess.query.return_value.order_by.return_value.all.return_value = []
            mock_sess.return_value = sess

            resp = client.get("/api/v1/alerts/active")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 0
            assert data["critical"] == 0
            assert data["alertas"] == []

    def test_retorna_alertas_com_contagem(self, client) -> None:
        alertas = [
            _ativo("10", "DVR_DOWN", "critical"),
            _ativo("20", "CAMERA_DOWN", "warning", filhos=None),
        ]
        with patch("itgov.api.v1.alerts.get_session") as mock_sess:
            sess = MagicMock()
            sess.__enter__ = lambda s: s
            sess.__exit__ = MagicMock(return_value=False)
            sess.query.return_value.order_by.return_value.all.return_value = alertas
            mock_sess.return_value = sess

            resp = client.get("/api/v1/alerts/active")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 2
            assert data["critical"] == 1
            assert data["warning"] == 1

    def test_campos_obrigatorios_presentes(self, client) -> None:
        alertas = [_ativo()]
        with patch("itgov.api.v1.alerts.get_session") as mock_sess:
            sess = MagicMock()
            sess.__enter__ = lambda s: s
            sess.__exit__ = MagicMock(return_value=False)
            sess.query.return_value.order_by.return_value.all.return_value = alertas
            mock_sess.return_value = sess

            resp = client.get("/api/v1/alerts/active")
            data = resp.get_json()
            item = data["alertas"][0]
            campos = {"host_id", "host_name", "hostgroup", "tipo", "severidade", "msg", "created_at"}
            for campo in campos:
                assert campo in item, f"Campo {campo!r} ausente"
