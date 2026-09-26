"""Testes de itgov/api/v1/datacenter_temp_diario.py (histórico diário do
sensor de temperatura do datacenter, lido do ThingSpeak)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest

from itgov.api.v1 import datacenter_temp_diario as mod


@pytest.fixture(autouse=True)
def _limpa_cache():
    mod._cache.clear()
    yield
    mod._cache.clear()


def _utc(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 9, dia, hora, minuto, tzinfo=UTC)


def test_consolida_por_dia_no_fuso_de_brasilia():
    leituras = [
        (_utc(22, 12), 18.0),
        (_utc(22, 18), 21.0),  # 15:00 local, máxima do dia 22
        # 02:00 UTC do dia 23 ainda é 23:00 do dia 22 em Brasília
        (_utc(23, 2), 17.0),
        (_utc(23, 15, 30), 22.5),  # 12:30 local do dia 23, acima de 22°C
    ]

    dias = mod.consolidar_por_dia(leituras, [date(2026, 9, 22), date(2026, 9, 23)])

    d22, d23 = dias
    assert d22["n"] == 3
    assert d22["media"] == 18.7
    assert (d22["min"], d22["min_hora"]) == (17.0, "23:00")
    assert (d22["max"], d22["max_hora"]) == (21.0, "15:00")
    assert d22["acima_limite"] == 0

    assert d23["n"] == 1
    assert (d23["max"], d23["max_hora"]) == (22.5, "12:30")
    assert d23["acima_limite"] == 1


def test_dia_sem_leitura_aparece_vazio():
    dias = mod.consolidar_por_dia([], [date(2026, 9, 10)])

    assert dias == [
        {
            "data": "2026-09-10",
            "n": 0,
            "cobertura_pct": 0.0,
            "parcial": False,
            "media": None,
            "min": None,
            "min_hora": None,
            "max": None,
            "max_hora": None,
            "acima_limite": 0,
        }
    ]


def test_cobertura_de_dia_completo():
    leituras = [(_utc(20, 3, 0), 18.0)] * 288  # metade das 576 esperadas

    (dia,) = mod.consolidar_por_dia(leituras, [date(2026, 9, 20)])

    assert dia["cobertura_pct"] == 50.0


def test_busca_em_janelas_e_ignora_campo_vazio():
    resposta = MagicMock()
    resposta.json.return_value = {
        "feeds": [
            {"created_at": "2026-09-01T12:00:00Z", "field1": "19.5"},
            {"created_at": "2026-09-01T12:02:30Z", "field1": None},
            {"created_at": "2026-09-01T12:05:00Z", "field1": ""},
        ]
    }
    with patch.object(mod.requests, "get", return_value=resposta) as get:
        leituras = mod._buscar_leituras("123", _utc(1, 0), _utc(25, 0))

    # 24 dias em janelas de 10 → 3 chamadas
    assert get.call_count == 3
    assert get.call_args_list[0].kwargs["params"]["start"] == "2026-09-01 00:00:00"
    assert get.call_args_list[-1].kwargs["params"]["end"] == "2026-09-25 00:00:00"
    assert leituras.count((_utc(1, 12), 19.5)) == 3


def test_sem_canal_configurado(monkeypatch):
    monkeypatch.delenv("NEXTCON_CHANNEL_ID", raising=False)

    dados = mod.get_cached_temp_diaria(7)

    assert dados["disponivel"] is False
    assert dados["dias"] == []


def test_falha_na_api_nao_fica_em_cache(monkeypatch):
    monkeypatch.setenv("NEXTCON_CHANNEL_ID", "123")
    with patch.object(mod.requests, "get", side_effect=mod.requests.ConnectionError("fora")):
        dados = mod.get_cached_temp_diaria(7)

    assert dados["disponivel"] is False
    assert dados["erro"] == "Falha ao consultar o ThingSpeak"
    assert mod._cache == {}


def test_sucesso_fica_em_cache(monkeypatch):
    monkeypatch.setenv("NEXTCON_CHANNEL_ID", "123")
    resposta = MagicMock()
    resposta.json.return_value = {"feeds": [{"created_at": datetime.now(UTC).isoformat(), "field1": "19.0"}]}
    with patch.object(mod.requests, "get", return_value=resposta) as get:
        primeira = mod.get_cached_temp_diaria(1)
        segunda = mod.get_cached_temp_diaria(1)

    assert primeira is segunda
    assert get.call_count == 1
    assert primeira["disponivel"] is True
    assert primeira["dias"][-1]["parcial"] is True


class TestRota:
    """/gov/infra/temperatura-diaria.json"""

    def test_anonimo_nao_acessa(self, factory_client):
        assert factory_client.get("/gov/infra/temperatura-diaria.json").status_code in (302, 401)

    def test_admin_recebe_json_e_dias_limitado(self, authed_client):
        with patch.object(mod, "get_cached_temp_diaria", return_value={"disponivel": False, "dias": []}) as get:
            resp = authed_client.get("/gov/infra/temperatura-diaria.json?dias=500")

        assert resp.status_code == 200
        assert resp.get_json() == {"disponivel": False, "dias": []}
        get.assert_called_once_with(90)
