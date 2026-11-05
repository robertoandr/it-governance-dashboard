"""Programas instalados (avisos MiMonitoringSoftwareInstalled do Acronis) e a aba Programas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from itgov.services import acronis_programas as ap

_AGORA = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)


def _alerta(maquina: str, programa: str, versao: str = "1.0", quando: str = "2026-11-01T10:00:00Z") -> dict[str, Any]:
    return {
        "type": "MiMonitoringSoftwareInstalled",
        "createdAt": quando,
        "details": {"resourceName": maquina, "software_name": programa, "software_version": versao},
    }


_ALERTAS = [
    _alerta("DESKTOP-A.grupogadens.com.br", "TeamViewer", "15.80", "2026-11-04T12:00:00.123456789Z"),
    _alerta("DESKTOP-A.grupogadens.com.br", "TeamViewer", "15.80", "2026-10-01T12:00:00Z"),  # repetida, mais velha
    _alerta("DESKTOP-B", "Google Chrome", "142", "2026-10-20T08:00:00Z"),
    _alerta("DESKTOP-A", "Google Chrome", "142", "2026-09-11T08:00:00Z"),
    _alerta("SRV-1", "AnyDeskEXE", "9.7"),
    {"type": "MiMonitoringMissingPatches", "details": {"resourceName": "X"}},
    _alerta("X", ""),
]


def test_montar_tira_repetidas_e_ordena() -> None:
    p = ap.montar(_ALERTAS, _AGORA)
    assert [(i.maquina, i.programa) for i in p.instalacoes] == [
        ("DESKTOP-A", "TeamViewer"),
        ("SRV-1", "AnyDeskEXE"),
        ("DESKTOP-B", "Google Chrome"),
        ("DESKTOP-A", "Google Chrome"),
    ]
    assert p.instalacoes[0].quando == datetime(2026, 11, 4, 12, 0, 0, 123456, tzinfo=UTC)
    assert p.maquinas == 3
    assert p.desde == datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    assert [i.programa for i in p.acesso_remoto] == ["TeamViewer", "AnyDeskEXE"]
    assert p.mais_instalados() == [("Google Chrome", 2), ("AnyDeskEXE", 1), ("TeamViewer", 1)]


@pytest.mark.parametrize(
    ("programa", "remoto"),
    [
        ("TeamViewer", True),
        ("AnyDeskEXE 9.7.14", True),
        ("RustDesk", True),
        ("UltraVNC", True),
        ("Chrome Remote Desktop Host", True),
        ("Google Chrome", False),
        ("FortiClient VPN", False),
        ("Microsoft Teams", False),
    ],
)
def test_acesso_remoto(programa: str, remoto: bool) -> None:
    assert ap.Instalacao(maquina="m", programa=programa).acesso_remoto is remoto


def test_carregar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap.acronis_cyber, "configurado", lambda: True)

    async def falso(limit: int) -> list[dict[str, Any]]:
        return _ALERTAS

    monkeypatch.setattr(ap.acronis_cyber, "_buscar_alertas", falso)
    p = ap._carregar()
    assert p is not None and len(p.instalacoes) == 4


def test_carregar_falha_vira_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap.acronis_cyber, "configurado", lambda: True)

    async def falha(limit: int) -> list[dict[str, Any]]:
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(ap.acronis_cyber, "_buscar_alertas", falha)
    assert ap._carregar() is None


def test_obter_sem_acronis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACRONIS_BASE_URL", raising=False)
    assert ap.obter() is None and ap.carregando() is False


# ── Tela ────────────────────────────────────────────────────────────────────


@pytest.fixture
def com_painel(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cyber_page import _mock_fontes

    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")
    _mock_fontes(monkeypatch)
    painel = ap.montar(_ALERTAS, _AGORA)
    monkeypatch.setattr(ap, "obter", lambda: painel)
    monkeypatch.setattr(ap, "carregando", lambda: False)


def test_pagina_programas(authed_client, com_painel) -> None:
    html = authed_client.get("/gov/cyber/programas").get_data(as_text=True)
    assert "aba: 'programas'" in html
    assert "TeamViewer" in html and "acesso remoto" in html
    assert "04/11/2026 09:00" in html  # Brasília
    assert "desde 11/09/2026" in html


def test_pagina_programas_so_remoto_e_busca(authed_client, com_painel) -> None:
    tabela = authed_client.get("/gov/cyber/programas?ver=remoto").get_data(as_text=True).split("Versão</th>", 1)[1]
    assert "TeamViewer" in tabela and "Google Chrome" not in tabela
    tabela = authed_client.get("/gov/cyber/programas?q=desktop-b").get_data(as_text=True).split("Versão</th>", 1)[1]
    assert "DESKTOP-B" in tabela and "SRV-1" not in tabela
