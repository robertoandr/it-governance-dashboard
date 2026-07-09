"""Tests for collector/jobs/datacenter_temp_collector.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

import config as _cfg

# Patch config before importing the module under test
_mock_settings = MagicMock()
_mock_settings.NEXTCON_CHANNEL_ID = "3372562"
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.datacenter_temp_collector import (  # noqa: E402
    _buscar_feed,
    _parse_feed,
    collect,
)

_FEED_OK = {
    "created_at": "2026-07-09T12:00:00Z",
    "field1": "18.5",
    "field2": "16.2",
    "field3": "21.0",
    "field4": "4321",
}


# ── _buscar_feed ─────────────────────────────────────────────────────────────


def test_buscar_feed_retorna_ultimo_feed():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"feeds": [_FEED_OK]}
    mock_resp.raise_for_status.return_value = None

    with patch("collector.jobs.datacenter_temp_collector.requests.get", return_value=mock_resp) as mock_get:
        feed = _buscar_feed()

    assert feed == _FEED_OK
    _, kwargs = mock_get.call_args
    assert kwargs["timeout"] == 10


def test_buscar_feed_sem_feeds_retorna_none():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"feeds": []}
    mock_resp.raise_for_status.return_value = None

    with patch("collector.jobs.datacenter_temp_collector.requests.get", return_value=mock_resp):
        assert _buscar_feed() is None


def test_buscar_feed_falha_de_rede_retorna_none_sem_excecao():
    with patch(
        "collector.jobs.datacenter_temp_collector.requests.get",
        side_effect=requests.ConnectionError("boom"),
    ):
        assert _buscar_feed() is None


def test_buscar_feed_timeout_retorna_none_sem_excecao():
    with patch(
        "collector.jobs.datacenter_temp_collector.requests.get",
        side_effect=requests.Timeout("timed out"),
    ):
        assert _buscar_feed() is None


# ── _parse_feed ──────────────────────────────────────────────────────────────


def test_parse_feed_campos_validos():
    dados = _parse_feed(_FEED_OK)
    assert dados["temp_atual"] == 18.5
    assert dados["temp_min"] == 16.2
    assert dados["temp_max"] == 21.0
    assert dados["uptime_min"] == 4321.0


def test_parse_feed_campo_ausente_retorna_none():
    feed = dict(_FEED_OK)
    del feed["field1"]
    assert _parse_feed(feed) is None


def test_parse_feed_campo_nao_numerico_retorna_none():
    feed = dict(_FEED_OK, field1="n/a")
    assert _parse_feed(feed) is None


def test_parse_feed_created_at_invalido_usa_now():
    feed = dict(_FEED_OK, created_at="not-a-date")
    dados = _parse_feed(feed)
    assert dados is not None


# ── collect ──────────────────────────────────────────────────────────────────


def test_collect_sem_channel_id_nao_chama_requests(monkeypatch):
    monkeypatch.setattr(_mock_settings, "NEXTCON_CHANNEL_ID", "")
    with patch("collector.jobs.datacenter_temp_collector.requests.get") as mock_get:
        collect()
    mock_get.assert_not_called()
    monkeypatch.setattr(_mock_settings, "NEXTCON_CHANNEL_ID", "3372562")


def test_collect_escreve_no_influxdb():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"feeds": [_FEED_OK]}
    mock_resp.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_write_api = MagicMock()
    mock_client.write_api.return_value = mock_write_api
    mock_client.__enter__.return_value = mock_client

    with (
        patch("collector.jobs.datacenter_temp_collector.requests.get", return_value=mock_resp),
        patch("collector.jobs.datacenter_temp_collector.InfluxDBClient", return_value=mock_client),
    ):
        collect()

    mock_write_api.write.assert_called_once()
    _, kwargs = mock_write_api.write.call_args
    assert kwargs["bucket"] == "governance_raw"
    lp = kwargs["record"].to_line_protocol()
    assert "gov_thingspeak_temperatura" in lp
    assert "temp_atual=18.5" in lp
    assert "sensor=DSB-WIFI-3372562" in lp
    assert "local=datacenter" in lp


def test_collect_feed_invalido_nao_escreve():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"feeds": [{"field1": "n/a"}]}
    mock_resp.raise_for_status.return_value = None

    with (
        patch("collector.jobs.datacenter_temp_collector.requests.get", return_value=mock_resp),
        patch("collector.jobs.datacenter_temp_collector.InfluxDBClient") as mock_influx,
    ):
        collect()

    mock_influx.assert_not_called()


def test_collect_falha_de_rede_nao_levanta_excecao():
    with patch(
        "collector.jobs.datacenter_temp_collector.requests.get",
        side_effect=requests.ConnectionError("boom"),
    ):
        collect()  # não deve levantar


def test_collect_falha_de_escrita_influx_nao_levanta_excecao():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"feeds": [_FEED_OK]}
    mock_resp.raise_for_status.return_value = None

    with (
        patch("collector.jobs.datacenter_temp_collector.requests.get", return_value=mock_resp),
        patch(
            "collector.jobs.datacenter_temp_collector.InfluxDBClient",
            side_effect=RuntimeError("influx down"),
        ),
    ):
        collect()  # não deve levantar
