"""Página /gov/backup (Cyber Acronis) e leitura do InfluxDB em itgov/api/v1/acronis_backup."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from itgov.api.v1 import acronis_backup


@pytest.fixture
def com_acronis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACRONIS_BASE_URL", "https://acronis.test")


_RISK = {
    "offline_gt_30d": 29,
    "sem_plano": 1,
    "incidents_total": 400,
    "license_issues": 9,
    "edr_total": 348,
    "incidents_mitigated": 282,
    "incidents_not_mitigated": 64,
    "intrusion_attempts": 385,
    "intrusion_edr": 346,
    "intrusion_url": 39,
    "intrusion_login": 0,
    "patches_critical": 0,
    "patches_warning": 33,
}


def _fake_query(flux: str) -> list[dict]:
    if "gov_acronis_agents" in flux:
        return [{"total": 294, "online": 135, "offline": 159, "outdated": 207}]
    if "gov_acronis_risk_summary" in flux:
        return [_RISK]
    if "gov_acronis_machines" in flux:
        return [
            {
                "resource_name": "pc-velho",
                "tenant": "T",
                "protection_status": "offline",
                "offline_days": 45,
                "offline_gt_30d": 1,
                "has_active_plan": 1,
            },
            {
                "resource_name": "pc-sem-plano",
                "tenant": "T",
                "protection_status": "online",
                "offline_days": 0,
                "offline_gt_30d": 0,
                "has_active_plan": 0,
            },
        ]
    if "gov_acronis_incidents" in flux:
        return [
            {
                "resource_name": "pc1",
                "tenant": "T",
                "alert_type": "EDRIncidentDetected",
                "severity": "warning",
                "mitigation": "nao_mitigado",
                "_time": None,
            }
        ]
    if "gov_acronis_last_login" in flux:
        return [{"_time": "2026-09-25T08:00:00Z", "user_email": "a@b.c", "event_time": "2026-08-21T13:52:05Z"}]
    return []


def test_buscar_dados_mapeia_kpis_de_seguranca() -> None:
    with patch.object(acronis_backup, "_query", side_effect=_fake_query):
        d = acronis_backup._buscar_dados()

    assert d["protected"] == 293
    assert d["protected_pct"] == 99.7
    assert d["offline_gt_30d_count"] == 29
    assert [m["name"] for m in d["offline_gt30"]] == ["pc-velho"]
    assert [m["name"] for m in d["sem_plano"]] == ["pc-sem-plano"]
    assert (d["incidents_mitigated"], d["incidents_not_mitigated"]) == (282, 64)
    assert d["intrusion_attempts"] == 385
    assert (d["patches_critical"], d["patches_warning"]) == (0, 33)
    assert d["outdated"] == 207
    assert d["incidentes"][0]["mitigation"] == "nao_mitigado"


def test_buscar_dados_login_usa_event_time() -> None:
    with patch.object(acronis_backup, "_query", side_effect=_fake_query):
        d = acronis_backup._buscar_dados()
    assert d["last_login_user"] == "a@b.c"
    assert d["last_login_fmt"] == "21/08/2026 13:52"


def test_buscar_dados_login_legado_sem_event_time_usa_time() -> None:
    def q(flux: str) -> list[dict]:
        if "gov_acronis_last_login" in flux:
            return [{"_time": "2026-09-01T10:00:00Z", "user_email": "x@y.z"}]
        return []

    with patch.object(acronis_backup, "_query", side_effect=q):
        d = acronis_backup._buscar_dados()
    assert d["last_login_fmt"] == "01/09/2026 10:00"
    assert d["protected_pct"] == 0.0


def test_backup_page_renderiza_kpis_novos(authed_client, com_acronis: None) -> None:
    with patch.object(acronis_backup, "_query", side_effect=_fake_query):
        acronis_backup._cache_data = None
        resp = authed_client.get("/gov/backup")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for trecho in (
        "Offline &gt; 30 dias",
        "Incidentes mitigados",
        "Incidentes não mitigados",
        "Tentativas de invasão",
        "346 EDR · 39 URLs · 0 logins",
        "Patches — crítico",
        "Patches — cuidado",
        "Agentes desatualizados",
        "pc-velho",
        "não mitigado",
    ):
        assert trecho in html, trecho


def test_backup_page_404_sem_acronis(authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACRONIS_BASE_URL", raising=False)
    assert authed_client.get("/gov/backup").status_code == 404
