"""CFTV: histórico de quedas de ping e acompanhamento dos dispositivos offline."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from itgov.api.v1 import cftv_monitoring
from itgov.api.v1.cftv_monitoring import formatar_duracao, historico_quedas, montar_visao

_AGORA = 1_790_000_000.0


def _dev(host: str, gravador: str = "DVR-1", status: str = "up", **extra: object) -> dict:
    base = {
        "host": host,
        "name": host,
        "ip": "10.0.0.1",
        "subcat": "camera",
        "andar": "?",
        "gravador": gravador,
        "is_gravador": False,
        "canal": "",
        "loja": "",
        "vendor": "",
        "model": "",
        "status": status,
        "problems": 0,
        "offline_desde": None,
        "ultimo_ping": None,
    }
    base.update(extra)
    return base


def _ev(eventid: str, host: str, inicio: float, r_eventid: str = "0") -> dict:
    return {"eventid": eventid, "clock": str(int(inicio)), "r_eventid": r_eventid, "hosts": [{"host": host}]}


@pytest.fixture(autouse=True)
def _cache_limpo() -> None:
    cftv_monitoring._cache_quedas.limpar()


def test_formatar_duracao() -> None:
    assert formatar_duracao(30) == "< 1 min"
    assert formatar_duracao(40 * 60) == "40 min"
    assert formatar_duracao(3600) == "1 h"
    assert formatar_duracao(2 * 3600 + 15 * 60) == "2 h 15 min"
    assert formatar_duracao(3 * 86400) == "3 d"
    assert formatar_duracao(3 * 86400 + 4 * 3600 + 59) == "3 d 4 h"


def test_historico_soma_quedas_fechadas_e_em_aberto() -> None:
    eventos = [
        _ev("1", "cam-a", _AGORA - 7200, r_eventid="11"),  # 1 h offline
        _ev("2", "cam-a", _AGORA - 1800),  # em aberto: 30 min até agora
        _ev("3", "cam-b", _AGORA - 600, r_eventid="13"),  # 5 min
    ]
    fim = {"11": int(_AGORA - 3600), "13": int(_AGORA - 300)}
    h = historico_quedas(eventos, fim, _AGORA)

    assert h["cam-a"]["quedas"] == 2
    assert h["cam-a"]["segundos"] == 3600 + 1800
    assert h["cam-a"]["tempo"] == "1 h 30 min"
    assert h["cam-a"]["em_aberto"] is True
    assert h["cam-a"]["ultima_ts"] == int(_AGORA - 1800)
    assert (h["cam-b"]["quedas"], h["cam-b"]["tempo"], h["cam-b"]["em_aberto"]) == (1, "5 min", False)


def test_recuperacao_desconhecida_conta_ate_agora() -> None:
    h = historico_quedas([_ev("1", "cam-a", _AGORA - 120, r_eventid="99")], {}, _AGORA)
    assert h["cam-a"]["segundos"] == 120


def test_ranking_respeita_recorte_e_ordena_por_quedas() -> None:
    dados = {
        "enabled": True,
        "devices": [
            _dev("cam-a", "DVR-1"),
            _dev("cam-b", "DVR-1", status="down"),
            _dev("cam-c", "DVR-2"),
            _dev("cam-limpa", "DVR-1"),
        ],
    }
    quedas = {
        "cam-a": {"quedas": 2, "segundos": 100.0},
        "cam-b": {"quedas": 7, "segundos": 50.0},
        "cam-c": {"quedas": 9, "segundos": 10.0},
    }
    v = montar_visao(dados, {"DVR-1": 1, "DVR-2": 2}, {1: "Sede", 2: "Shopping"}, {}, filtro={1}, quedas=quedas)
    assert [d["host"] for d in v["ranking_quedas"]] == ["cam-b", "cam-a"]
    card = v["cards"][0]
    assert {d["host"]: d["historico"] for d in card["dispositivos"]}["cam-limpa"] is None


def test_buscar_quedas_consulta_eventos_e_recuperacoes() -> None:
    chamadas: list[tuple[str, dict]] = []

    def fake_zbx(method: str, params: dict) -> list[dict]:
        chamadas.append((method, params))
        if method == "hostgroup.get":
            return [{"groupid": "7", "name": "CFTV/Cameras"}, {"groupid": "8", "name": "Outros CFTV"}]
        if method == "event.get" and "eventids" in params:
            return [{"eventid": "11", "clock": str(int(_AGORA - 60))}]
        return [_ev("1", "cam-a", _AGORA - 120, r_eventid="11")]

    with patch.object(cftv_monitoring, "_zbx", side_effect=fake_zbx), patch("time.time", return_value=_AGORA):
        h = cftv_monitoring._buscar_quedas()

    assert h["cam-a"]["segundos"] == 60
    _, params = chamadas[1]
    assert params["groupids"] == ["7"]
    assert params["search"] == {"name": cftv_monitoring.TRIGGER_SEM_PING}
    assert params["time_from"] == int(_AGORA) - 30 * 86400


def test_falha_do_zabbix_nao_apaga_historico_bom() -> None:
    bom = {"cam-a": {"quedas": 1}}
    with patch.object(cftv_monitoring, "_buscar_quedas", return_value=bom):
        assert cftv_monitoring.get_cached_historico_quedas() == bom
    # Falha na atualização chega como None e não pode substituir o valor bom
    cftv_monitoring._cache_quedas._guardar(None)
    assert cftv_monitoring.get_cached_historico_quedas() == bom


def test_falha_sem_historico_anterior_devolve_vazio() -> None:
    with patch.object(cftv_monitoring, "_buscar_quedas", side_effect=RuntimeError("Zabbix API: erro")):
        assert cftv_monitoring.get_cached_historico_quedas() == {}


# ── Página ────────────────────────────────────────────────────────────────────


def test_pagina_mostra_offline_ha_ultimo_ping_e_historico(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZABBIX_URL", "https://zabbix.test")
    agora = time.time()
    dados = {
        "enabled": True,
        "devices": [
            _dev(
                "cam-off",
                "DVR-1",
                status="down",
                name="Câmera Off",
                offline_desde=int(agora - 2 * 3600),
                ultimo_ping=int(agora - 30),
            ),
            _dev("cam-ok", "DVR-1", name="Câmera Ok"),
        ],
    }
    quedas = {
        "cam-off": {
            "quedas": 72,
            "segundos": 5000.0,
            "tempo": "1 h 23 min",
            "ultima": "02/10 08:00",
            "em_aberto": True,
        },
    }
    monkeypatch.setattr(cftv_monitoring, "get_cached_cftv_summary", lambda: dados)
    monkeypatch.setattr(cftv_monitoring, "get_cached_historico_quedas", lambda: quedas)
    html = authed_client.get("/gov/cftv").get_data(as_text=True)

    assert "Offline há" in html and "2 h" in html
    assert "há &lt; 1 min" in html or "há < 1 min" in html
    assert "Histórico de quedas — últimos 30 dias" in html
    assert "1 h 23 min" in html and "02/10 08:00" in html
