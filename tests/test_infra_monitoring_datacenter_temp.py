"""Testes de itgov/api/v1/infra_monitoring.py — _ler_datacenter_temp()
(sensor de temperatura do datacenter, sensor Nextcon via ThingSpeak)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from itgov.api.v1.infra_monitoring import _ler_datacenter_temp

_ROW_LAST = {
    "_time": datetime(2026, 7, 9, 10, 0, 0, tzinfo=UTC),
    "temp_atual": 19.4,
    "temp_min": 17.1,
    "temp_max": 22.3,
    "uptime_min": 1234.0,
}

_ROWS_SERIE = [
    {"_time": datetime(2026, 7, 9, 9, 0, 0, tzinfo=UTC), "_value": 18.0},
    {"_time": datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC), "_value": 19.4},
]


def test_sem_dados_retorna_indisponivel():
    with patch("itgov.api.v1.infra_monitoring._query_influx", return_value=[]):
        resultado = _ler_datacenter_temp()

    assert resultado == {
        "disponivel": False,
        "temp_atual": None,
        "temp_min": None,
        "temp_max": None,
        "atualizado_em": None,
        "serie": [],
    }


def test_com_dados_retorna_valores_e_serie():
    with patch(
        "itgov.api.v1.infra_monitoring._query_influx",
        side_effect=[[_ROW_LAST], _ROWS_SERIE],
    ):
        resultado = _ler_datacenter_temp()

    assert resultado["disponivel"] is True
    assert resultado["temp_atual"] == 19.4
    assert resultado["temp_min"] == 17.1
    assert resultado["temp_max"] == 22.3
    assert resultado["atualizado_em"] == "2026-07-09T10:00:00+00:00"
    assert resultado["serie"] == [
        {"time": "2026-07-09T09:00:00+00:00", "value": 18.0},
        {"time": "2026-07-09T09:30:00+00:00", "value": 19.4},
    ]


def test_serie_ignora_pontos_sem_valor():
    rows_serie = [*_ROWS_SERIE, {"_time": datetime(2026, 7, 9, 9, 45, tzinfo=UTC), "_value": None}]
    with patch(
        "itgov.api.v1.infra_monitoring._query_influx",
        side_effect=[[_ROW_LAST], rows_serie],
    ):
        resultado = _ler_datacenter_temp()

    assert len(resultado["serie"]) == 2


def test_campos_ausentes_no_last_usam_zero():
    row_incompleto = {"_time": datetime(2026, 7, 9, 10, 0, tzinfo=UTC)}
    with patch(
        "itgov.api.v1.infra_monitoring._query_influx",
        side_effect=[[row_incompleto], []],
    ):
        resultado = _ler_datacenter_temp()

    assert resultado["disponivel"] is True
    assert resultado["temp_atual"] == 0.0
    assert resultado["temp_min"] == 0.0
    assert resultado["temp_max"] == 0.0
