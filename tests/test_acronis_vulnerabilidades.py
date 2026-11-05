"""Vulnerabilidades e patches por máquina (varreduras WinScan/TpScan do Acronis) e a aba do Cyber."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from itgov.services import acronis_vulnerabilidades as av

_AGORA = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)


def _ativ(
    tipo: str, agente: str, quando: str, va: Any = None, patches: list[str] | None = None, code: str = "ok"
) -> dict:
    return {
        "type": tipo,
        "executor": {"id": agente, "clusterId": ""},
        "completedAt": quando,
        "result": {"code": code, "payload": {"va": va or [], "patches": patches or []}},
    }


def test_data_com_sete_casas_e_z() -> None:
    assert av._data("2026-10-27T17:17:59.2652109Z") == datetime(2026, 10, 27, 17, 17, 59, 265210, tzinfo=UTC)
    assert av._data("2026-10-27T17:17:59Z") == datetime(2026, 10, 27, 17, 17, 59, tzinfo=UTC)
    assert av._data("") is None
    assert av._data("ontem") is None


def test_programas_da_varredura() -> None:
    assert av.programas_da_varredura([{"Firefox": 576}, {"TeamViewer": 1}, {"Firefox": 2}]) == {
        "Firefox": 578,
        "TeamViewer": 1,
    }
    assert av.programas_da_varredura({"Chrome": "3"}) == {"Chrome": 3}
    assert av.programas_da_varredura([{"X": "muitos"}, "lixo"]) == {}
    assert av.programas_da_varredura(None) == {}


def test_montar_painel_usa_a_ultima_de_cada_tipo_e_junta() -> None:
    atividades = [
        _ativ("WinScan", "ag1", "2026-10-14T10:00:00Z", va=[{"Windows": 9}], patches=["KB-velho"]),
        _ativ("WinScan", "ag1", "2026-10-28T10:00:00.1234567Z", va=[{"TeamViewer": 1}], patches=["KB5127648"]),
        _ativ("TpScan", "ag1", "2026-10-27T10:00:00Z", va=[{"Firefox": 576}], patches=["Firefox 140", "KB5127648"]),
        _ativ("TpScan", "ag2", "2026-10-20T10:00:00Z", patches=["AnyDesk 9.7"]),
        _ativ("WinScan", "ag3", "2026-10-29T10:00:00Z", va=[{"X": 5}], code="timedout"),
        _ativ("UpdateAgent", "ag3", "2026-10-29T10:00:00Z"),
        {"type": "WinScan", "executor": {}, "result": {"code": "ok"}},
    ]
    nomes = {"ag1": "NT0307263.grupogadens.com.br", "ag2": "SRV-FILE", "ag3": "PC-3"}
    painel = av.montar_painel(atividades, nomes, _AGORA)
    assert [m.nome for m in painel.maquinas] == ["NT0307263", "SRV-FILE"]
    pc = painel.maquinas[0]
    assert pc.programas == {"TeamViewer": 1, "Firefox": 576}
    assert pc.patches == ["Firefox 140", "KB5127648"]
    assert pc.tipos == ["Programas", "Windows"]
    assert pc.varredura_em == datetime(2026, 10, 28, 10, 0, 0, 123456, tzinfo=UTC)
    assert pc.total_vulnerabilidades == 577
    assert painel.agentes == 3
    assert painel.total_vulnerabilidades == 577 and painel.total_patches == 3
    assert painel.mais_antiga == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert painel.top_programas() == [("Firefox", 576, 1), ("TeamViewer", 1, 1)]
    assert painel.top_patches(2) == [("AnyDesk 9.7", 1), ("Firefox 140", 1)]


def test_agente_sem_nome_usa_o_id() -> None:
    painel = av.montar_painel([_ativ("TpScan", "abcdef123456", "2026-10-20T10:00:00Z")], {}, _AGORA)
    assert painel.maquinas[0].nome == "abcdef12"


def test_obter_painel_sem_acronis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACRONIS_BASE_URL", raising=False)
    assert av.obter_painel() is None
    assert av.carregando() is False


def test_carregar_falha_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    async def falha() -> av.PainelVulnerabilidades:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(av, "_buscar", falha)
    assert av._carregar() is None


# ── Página ──────────────────────────────────────────────────────────────────


@pytest.fixture
def com_painel(monkeypatch: pytest.MonkeyPatch) -> av.PainelVulnerabilidades:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    painel = av.montar_painel(
        [
            _ativ("TpScan", "ag1", "2026-10-27T10:00:00Z", va=[{"Firefox": 576}], patches=["Firefox 140"]),
            _ativ("WinScan", "ag2", "2026-10-20T10:00:00Z", patches=["2026-10 Atualização de Segurança (KB5127648)"]),
        ],
        {"ag1": "NT0307263", "ag2": "SRV-FILE", "ag3": "PC-3"},
        _AGORA,
    )
    monkeypatch.setattr(av, "obter_painel", lambda: painel)
    monkeypatch.setattr(av, "carregando", lambda: False)
    return painel


def test_pagina_vulnerabilidades(authed_client, com_painel) -> None:
    html = authed_client.get("/gov/cyber/vulnerabilidades").get_data(as_text=True)
    assert "aba: 'vulnerabilidades'" in html
    for trecho in ("NT0307263", "Firefox (576)", "KB5127648", "1 máquinas ainda sem varredura", "20/10 07:00"):
        assert trecho in html


def test_pagina_vulnerabilidades_busca(authed_client, com_painel) -> None:
    html = authed_client.get("/gov/cyber/vulnerabilidades?q=kb5127").get_data(as_text=True)
    assert "SRV-FILE" in html and "NT0307263</td>" not in html


def test_pagina_vulnerabilidades_carregando(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    with patch.object(av, "obter_painel", return_value=None), patch.object(av, "carregando", return_value=True):
        html = authed_client.get("/gov/cyber/vulnerabilidades").get_data(as_text=True)
    assert "Lendo as varreduras do Acronis" in html
