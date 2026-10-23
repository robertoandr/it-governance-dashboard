"""Investigação ao vivo de incidentes Acronis (itgov.services.acronis_cyber)."""

from __future__ import annotations

import pytest

from itgov.services import acronis_cyber

_RAW = [
    {
        "id": "a1",
        "type": "EDRIncidentDetected",
        "severity": "critical",
        "createdAt": "2026-10-23T10:00:00Z",
        "details": {"incidentId": "INC-1", "isMitigated": "false", "resourceName": "pc-rh-01"},
    },
    {
        "id": "a2",
        "type": "EDRIncidentDetected",
        "severity": "warning",
        "createdAt": "2026-10-23T09:00:00Z",
        "details": {"incidentId": "INC-1", "isMitigated": "true", "resourceName": "pc-rh-01"},
    },
    {
        "id": "a3",
        "type": "SuspiciousLogin",
        "severity": "warning",
        "createdAt": "2026-10-23T08:00:00Z",
        "details": {"incidentId": "INC-2", "resourceName": "srv-web"},
    },
]


# ── configurado() + fallback de credenciais ──────────────────────────────────


def test_configurado_false_sem_credenciais(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (
        "ACRONIS_BASE_URL",
        "ACRONIS_CYBER_CLIENT_ID",
        "ACRONIS_CYBER_CLIENT_SECRET",
        "ACRONIS_CLIENT_ID",
        "ACRONIS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(v, raising=False)
    assert acronis_cyber.configurado() is False


def test_configurado_usa_credenciais_dedicadas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACRONIS_BASE_URL", "https://cyber.test")
    monkeypatch.setenv("ACRONIS_CYBER_CLIENT_ID", "cid")
    monkeypatch.setenv("ACRONIS_CYBER_CLIENT_SECRET", "sec")
    assert acronis_cyber.configurado() is True
    assert acronis_cyber._creds() == ("cid", "sec")


def test_configurado_cai_para_credenciais_do_coletor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACRONIS_BASE_URL", "https://cyber.test")
    monkeypatch.delenv("ACRONIS_CYBER_CLIENT_ID", raising=False)
    monkeypatch.delenv("ACRONIS_CYBER_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("ACRONIS_CLIENT_ID", "base-id")
    monkeypatch.setenv("ACRONIS_CLIENT_SECRET", "base-sec")
    assert acronis_cyber.configurado() is True
    assert acronis_cyber._creds() == ("base-id", "base-sec")


# ── Normalização e agrupamento (puros) ───────────────────────────────────────


def test_normalizar_alerta_le_chaves_defensivamente() -> None:
    a = acronis_cyber.normalizar_alerta(_RAW[0])
    assert a.id == "a1"
    assert a.incident_id == "INC-1"
    assert a.recurso == "pc-rh-01"
    assert a.mitigado is False
    assert a.criado_em == "2026-10-23T10:00:00Z"


def test_normalizar_alerta_sem_incident_id_usa_o_id() -> None:
    a = acronis_cyber.normalizar_alerta({"id": "x9", "type": "T", "severity": "low", "details": {}})
    assert a.incident_id == "x9"
    assert a.recurso == "—"
    assert a.criado_em is None


def test_agrupar_incidentes_monta_timeline_e_severidade_maxima() -> None:
    incidentes = acronis_cyber.agrupar_incidentes([acronis_cyber.normalizar_alerta(r) for r in _RAW])
    assert [i.incident_id for i in incidentes] == ["INC-1", "INC-2"]  # crítico primeiro
    inc1 = incidentes[0]
    assert inc1.severidade == "critical"  # maior entre os dois alertas
    assert inc1.total_alertas == 2
    assert inc1.mitigado is True  # ao menos um alerta mitigado
    assert [e.id for e in inc1.timeline] == ["a2", "a1"]  # do mais antigo ao recente
    assert inc1.primeiro_em == "2026-10-23T09:00:00Z"
    assert inc1.ultimo_em == "2026-10-23T10:00:00Z"


def test_peso_severidade() -> None:
    assert acronis_cyber.peso_severidade("critical") > acronis_cyber.peso_severidade("warning")
    assert acronis_cyber.peso_severidade(None) == 0
    assert acronis_cyber.peso_severidade("xpto") == 0


# ── _coletar / listar / detalhe (com busca mockada) ──────────────────────────


def test_coletar_vazio_quando_nao_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acronis_cyber, "configurado", lambda: False)
    assert acronis_cyber.listar_incidentes() == []


def _mock_busca(monkeypatch: pytest.MonkeyPatch, raw: list[dict]) -> None:
    monkeypatch.setattr(acronis_cyber, "configurado", lambda: True)

    async def _fake(limit: int) -> list[dict]:
        return raw

    monkeypatch.setattr(acronis_cyber, "_buscar_alertas", _fake)


def test_listar_incidentes_agrupa(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_busca(monkeypatch, _RAW)
    incidentes = acronis_cyber.listar_incidentes()
    assert [i.incident_id for i in incidentes] == ["INC-1", "INC-2"]


def test_detalhe_incidente_encontra_e_nao_encontra(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_busca(monkeypatch, _RAW)
    assert acronis_cyber.detalhe_incidente("INC-2").recurso == "srv-web"
    assert acronis_cyber.detalhe_incidente("NAO-EXISTE") is None


def test_coletar_degrada_em_erro_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acronis_cyber, "configurado", lambda: True)

    async def _boom(limit: int) -> list[dict]:
        raise acronis_cyber.httpx.HTTPError("timeout")

    monkeypatch.setattr(acronis_cyber, "_buscar_alertas", _boom)
    assert acronis_cyber.listar_incidentes() == []
