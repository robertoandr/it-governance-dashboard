"""Tests for collector/jobs/acronis_risk_collector.py — KPIs de segurança e planos."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import requests

import config as _cfg

_mock_settings = MagicMock()
_mock_settings.INFLUX_URL = "http://localhost:8086"
_mock_settings.INFLUX_TOKEN = "influx-tok"
_mock_settings.INFLUX_ORG = "testorg"
_mock_settings.INFLUX_BUCKET_RAW = "governance_raw"
_cfg.settings = _mock_settings

from collector.jobs.acronis_risk_collector import (  # noqa: E402
    AcronisRiskCollector,
    _achatar,
    planos_por_agente,
    resumo_seguranca,
)


def _app(agent_id: str, status: str = "ok", enabled: bool = True, tipo: str = "policy.protection.total") -> dict:
    return {"agent_id": agent_id, "enabled": enabled, "status": status, "policy": {"type": tipo}}


def _edr(incident_id: str, mitigado: bool, resource: str = "pc1") -> dict:
    return {
        "type": "EDRIncidentDetected",
        "severity": "warning",
        "details": {
            "incidentId": incident_id,
            "isMitigated": "true" if mitigado else "false",
            "resourceName": resource,
        },
    }


# ── planos_por_agente ────────────────────────────────────────────────────────


def test_planos_ignora_desabilitados_e_outros_tipos() -> None:
    apps = [
        _app("a1"),
        _app("a2", enabled=False),
        _app("a3", tipo="policy.backup.machine"),
    ]
    assert planos_por_agente(apps) == {"a1": "ok"}


def test_planos_mantem_pior_status_por_agente() -> None:
    apps = [_app("a1", "ok"), _app("a1", "critical"), _app("a1", "warning")]
    assert planos_por_agente(apps) == {"a1": "critical"}


def test_achatar_listas_aninhadas() -> None:
    assert _achatar([[{"a": 1}, {"b": 2}], {"c": 3}, "lixo"]) == [{"a": 1}, {"b": 2}, {"c": 3}]


# ── resumo_seguranca ─────────────────────────────────────────────────────────


def test_resumo_conta_incidentes_distintos_por_mitigacao() -> None:
    alertas = [
        _edr("i1", True),
        _edr("i1", True),  # mesmo incidente, dois alertas
        _edr("i2", False),
        _edr("i3", False),
        _edr("i3", True),  # incidente depois mitigado conta só como mitigado
    ]
    r = resumo_seguranca(alertas)
    assert r["incidents_mitigated"] == 2
    assert r["incidents_not_mitigated"] == 1
    assert r["intrusion_edr"] == 3


def test_resumo_tentativas_de_invasao_somam_edr_url_login() -> None:
    alertas = [
        _edr("i1", True),
        {"type": "MaliciousUrlDetected", "severity": "error", "details": {}},
        {"type": "MaliciousUrlDetected", "severity": "error", "details": {}},
        {"type": "MiMonitoringFailedLogins", "severity": "warning", "details": {}},
        {"type": "MiMonitoringHighMemoryUsage", "severity": "warning", "details": {}},
    ]
    r = resumo_seguranca(alertas)
    assert (r["intrusion_edr"], r["intrusion_url"], r["intrusion_login"]) == (1, 2, 1)
    assert r["intrusion_attempts"] == 4


def test_resumo_patches_por_maquina_e_severidade() -> None:
    alertas = [
        {"type": "MiMonitoringMissingPatches", "severity": "warning", "details": {"resourceName": "PC1"}},
        {"type": "PMRebootRequired", "severity": "warning", "details": {"resourceName": "pc1"}},
        {"type": "MiMonitoringMissingPatches", "severity": "warning", "details": {"resourceName": "pc2"}},
        {"type": "MiMonitoringMissingPatches", "severity": "critical", "details": {"resourceName": "pc2"}},
        {"type": "MiMonitoringWindowsUpdateDisabled", "severity": "error", "details": {"resourceName": "pc3"}},
    ]
    r = resumo_seguranca(alertas)
    # pc2 e pc3 críticos; pc1 cuidado (pc2 não conta duas vezes)
    assert r["patches_critical"] == 2
    assert r["patches_warning"] == 1


def test_resumo_sem_alertas_zera_tudo() -> None:
    assert set(resumo_seguranca([]).values()) == {0}


# ── _coletar_planos ──────────────────────────────────────────────────────────


def _collector() -> AcronisRiskCollector:
    return AcronisRiskCollector("https://acronis.test", "cid", "secret")


def test_coletar_planos_pagina_e_achata() -> None:
    c = _collector()
    paginas = [
        {"items": [[_app("a1")]], "paging": {"cursors": {"after": "x"}}},
        {"items": [_app("a2", "warning")], "paging": {"cursors": {}}},
    ]
    with patch.object(c, "_get", side_effect=paginas) as get:
        assert c._coletar_planos() == {"a1": "ok", "a2": "warning"}
    assert get.call_args_list[1].args[1] == {"after": "x", "limit": 500}


def test_coletar_planos_retorna_none_quando_api_falha() -> None:
    c = _collector()
    with patch.object(c, "_get", side_effect=requests.HTTPError("403")):
        assert c._coletar_planos() is None


# ── collect ──────────────────────────────────────────────────────────────────


def _pontos_escritos(write_api: MagicMock) -> list[Any]:
    return write_api.write.call_args.kwargs["record"]


def _rodar_collect(planos: dict[str, str] | None, agentes: list[dict], alertas: list[dict]) -> list[Any]:
    c = _collector()
    client = MagicMock()
    write_api = client.__enter__.return_value.write_api.return_value
    with (
        patch.object(c, "_coletar_agentes", return_value=agentes),
        patch.object(c, "_coletar_alertas", return_value=alertas),
        patch.object(c, "_coletar_ultimo_login", return_value={"user": "a@b.c", "time": "2026-08-21T13:52:05Z"}),
        patch.object(c, "_coletar_planos", return_value=planos),
        patch("collector.jobs.acronis_risk_collector.InfluxDBClient", return_value=client),
    ):
        c.collect()
    return _pontos_escritos(write_api)


def _agente(aid: str, online: bool = True) -> dict:
    return {"id": aid, "hostname": f"{aid}.corp", "online": online, "components": ["activeProtection"]}


def _por_measurement(pontos: list[Any], nome: str) -> list[str]:
    return [p.to_line_protocol() for p in pontos if p._name == nome]


def test_collect_usa_plano_real_e_grava_kpis_de_seguranca() -> None:
    pontos = _rodar_collect({"a1": "ok"}, [_agente("a1"), _agente("a2")], [_edr("i1", False, "a1.corp")])

    maquinas = _por_measurement(pontos, "gov_acronis_machines")
    assert any("a1.corp" in m and "has_active_plan=1i" in m for m in maquinas)
    assert any("a2.corp" in m and "has_active_plan=0i" in m for m in maquinas)

    (resumo,) = _por_measurement(pontos, "gov_acronis_risk_summary")
    assert "sem_plano=1i" in resumo
    assert "incidents_not_mitigated=1i" in resumo
    assert "intrusion_attempts=1i" in resumo
    assert "offline_gt_30d=0i" in resumo

    (incidente,) = _por_measurement(pontos, "gov_acronis_incidents")
    assert "mitigation=nao_mitigado" in incidente


def test_collect_sem_api_de_planos_cai_no_fallback_components() -> None:
    pontos = _rodar_collect(None, [_agente("a1")], [])
    (resumo,) = _por_measurement(pontos, "gov_acronis_risk_summary")
    assert "sem_plano=0i" in resumo


def test_collect_grava_login_no_horario_da_coleta_com_event_time() -> None:
    pontos = _rodar_collect({}, [_agente("a1")], [])
    login = next(p for p in pontos if p._name == "gov_acronis_last_login")
    line = login.to_line_protocol()
    assert 'event_time="2026-08-21T13:52:05Z"' in line
    # timestamp do ponto é o da coleta, não o do evento (que pode sair da janela de leitura)
    assert datetime.now(UTC) - login._time < timedelta(minutes=1)
