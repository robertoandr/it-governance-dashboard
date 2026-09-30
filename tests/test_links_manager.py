"""Testes de itgov/api/v1/links_manager.py — links WAN com métricas ICMP do Zabbix."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from itgov.api.v1 import links_manager as lm


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zbx.local")
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    lm._cache_data = None
    lm._cache_ts = 0.0


def _resp(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_zbx_sem_url_retorna_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "")
    with patch.object(lm.requests, "post") as post:
        assert lm._zbx("item.get", {}) is None
    post.assert_not_called()


def test_zbx_sucesso_erro_e_falha_de_rede() -> None:
    with patch.object(lm.requests, "post", return_value=_resp({"result": [1]})) as post:
        assert lm._zbx("item.get", {}) == [1]
    assert post.call_args.args[0] == "https://zbx.local/api_jsonrpc.php"

    with patch.object(lm.requests, "post", return_value=_resp({"error": "x"})):
        assert lm._zbx("item.get", {}) is None

    with patch.object(lm.requests, "post", side_effect=requests.ConnectionError("fora")):
        assert lm._zbx("item.get", {}) is None


def test_metricas_zabbix_converte_e_marca_stale() -> None:
    agora = str(int(time.time()))
    antigo = str(int(time.time()) - 3600)
    items = [
        {"hostid": "1", "key_": "icmpping", "lastvalue": "1", "lastclock": agora},
        {"hostid": "1", "key_": "icmppingloss", "lastvalue": "2.5", "lastclock": agora},
        {"hostid": "1", "key_": "icmppingsec", "lastvalue": "0.0421", "lastclock": agora},
        {"hostid": "2", "key_": "icmpping", "lastvalue": "1", "lastclock": antigo},
        {"hostid": "2", "key_": "icmppingloss", "lastvalue": "", "lastclock": antigo},
        {"hostid": "2", "key_": "icmppingsec", "lastvalue": "", "lastclock": antigo},
    ]
    with patch.object(lm, "_zbx", return_value=items):
        m = lm._metricas_zabbix(["1", "2"])

    assert m["1"] == {"up": True, "stale": False, "last_clock": int(agora), "loss_pct": 2.5, "latency_ms": 42.1}
    assert m["2"]["up"] is False
    assert m["2"]["stale"] is True
    assert m["2"]["loss_pct"] is None
    assert m["2"]["latency_ms"] is None


def test_metricas_zabbix_sem_hosts_ou_sem_itens() -> None:
    assert lm._metricas_zabbix([]) == {}
    with patch.object(lm, "_zbx", return_value=None):
        assert lm._metricas_zabbix(["1"]) == {}


def test_disponibilidade_zabbix() -> None:
    historico = [{"value": "1"}] * 3 + [{"value": "0"}]
    with patch.object(lm, "_zbx", side_effect=[[{"itemid": "9"}], historico]) as zbx:
        assert lm._disponibilidade_zabbix("1", period_h=24) == 75.0
    assert zbx.call_args.args[1]["itemids"] == ["9"]

    with patch.object(lm, "_zbx", return_value=[]):
        assert lm._disponibilidade_zabbix("1") is None
    with patch.object(lm, "_zbx", side_effect=[[{"itemid": "9"}], []]):
        assert lm._disponibilidade_zabbix("1") is None


@pytest.mark.parametrize(
    ("up", "stale", "loss", "latency", "esperado"),
    [
        (None, False, None, None, "nodata"),
        (True, True, None, None, "nodata"),
        (False, False, None, None, "down"),
        (True, False, 5.0, 10.0, "degraded"),
        (True, False, 0.0, 300.0, "degraded"),
        (True, False, 1.0, 10.0, "warn"),
        (True, False, 0.0, 100.0, "warn"),
        (True, False, 0.0, 20.0, "up"),
        (True, False, None, None, "up"),
    ],
)
def test_classificar_status(up, stale, loss, latency, esperado) -> None:
    assert lm._classificar_status(up, stale, loss, latency) == esperado


def test_buscar_dados_enriquece_e_conta() -> None:
    links = [
        {"id": 1, "name": "Algar", "zabbix_hostid": "1"},
        {"id": 2, "name": "Vivo", "zabbix_hostid": "2"},
        {"id": 3, "name": "Sem Zabbix", "zabbix_hostid": None},
    ]
    metricas = {
        "1": {"up": True, "stale": False, "loss_pct": 0.0, "latency_ms": 12.0, "last_clock": 1759200000},
        "2": {"up": False, "stale": False, "last_clock": 1759200000},
    }
    with (
        patch.object(lm, "_listar_links", return_value=links),
        patch.object(lm, "_metricas_zabbix", return_value=metricas) as met,
    ):
        dados = lm._buscar_dados()

    met.assert_called_once_with(["1", "2"])
    assert [lnk["status"] for lnk in dados["links"]] == ["up", "down", "nodata"]
    assert dados["links"][0]["last_check"]
    assert dados["links"][2]["last_check"] == ""
    assert (dados["total"], dados["up"], dados["down"], dados["degraded"], dados["nodata"]) == (3, 1, 1, 0, 1)
    assert dados["all_ok"] is False
    assert dados["has_zabbix"] is True


def test_get_cached_links_cache_invalidacao_e_falha() -> None:
    with patch.object(lm, "_buscar_dados", return_value={"links": [], "total": 0}) as buscar:
        primeiro = lm.get_cached_links()
        assert lm.get_cached_links() is primeiro
        lm.invalidar_cache()
        lm.get_cached_links()
    assert buscar.call_count == 2

    lm.invalidar_cache()
    with patch.object(lm, "_buscar_dados", side_effect=RuntimeError("db travado")):
        dados = lm.get_cached_links()
    assert dados["_erro"] == "db travado"
    assert dados["all_ok"] is False


def test_listar_links_le_do_banco(factory_app) -> None:
    from app.extensions import db
    from app.models.link import Link

    with factory_app.app_context():
        ativo = Link(name="pytest-link-ativo", ip="198.51.100.10", cidr="198.51.100.10/32")
        inativo = Link(name="pytest-link-inativo", ip="198.51.100.11", cidr="198.51.100.11/32", active=False)
        db.session.add_all([ativo, inativo])
        db.session.commit()
        try:
            nomes = [lnk["name"] for lnk in lm._listar_links()]
            assert "pytest-link-ativo" in nomes
            assert "pytest-link-inativo" not in nomes
        finally:
            db.session.delete(ativo)
            db.session.delete(inativo)
            db.session.commit()


def test_listar_links_falha_de_banco_retorna_vazio() -> None:
    with patch("app.models.link.Link") as link:
        link.query.filter_by.side_effect = RuntimeError("sem contexto")
        assert lm._listar_links() == []
