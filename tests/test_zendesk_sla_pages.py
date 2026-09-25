"""Páginas /sla e /zendesk renderizam o SLA real (política Zendesk, horário comercial)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def com_zendesk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENDESK_SUBDOMAIN", "test-corp")


def _period(compliance: float | None) -> dict:
    return {
        "total_tickets": 129 if compliance is not None else 0,
        "breached": 84 if compliance is not None else 0,
        "unknown": 0,
        "compliance_pct": compliance,
        "first_reply_compliance_pct": 36.2 if compliance is not None else None,
        "resolution_compliance_pct": 65.1 if compliance is not None else None,
        "avg_first_reply_minutes": 467.0 if compliance is not None else None,
        "window_days": 30,
    }


def _sla_detail(compliance: float | None, source: str = "zendesk_policy") -> dict:
    return {
        "total_open": 2,
        "total_all": 131,
        "by_priority": {
            "urgent": {
                "count": 0,
                "breached": 0,
                "unknown": 0,
                "ok": 0,
                "compliance_pct": None,
                "first_reply_h": 2.0,
                "resolution_h": 4.0,
            },
            "normal": {
                "count": 2,
                "breached": 1,
                "unknown": 1,
                "ok": 0,
                "compliance_pct": 0.0,
                "first_reply_h": 4.0,
                "resolution_h": 33.0,
            },
        },
        "oldest": [
            {
                "id": 7,
                "subject": "Impressora",
                "status": "open",
                "priority": "normal",
                "age_hours": 50.0,
                "age_str": "2d 2h",
                "breached": True,
                "created_fmt": "22/09/2026",
            },
        ],
        "age_buckets": {"<8h": 0, "8-48h": 1, "48-168h": 1, ">168h": 0},
        "resolved_7d": 23,
        "resolved_30d": 129,
        "volume": {"open": 2},
        "backlog_breached": 1,
        "period": _period(compliance),
        "sla_source": source,
        "sla_policy": "SLA Geral" if source == "zendesk_policy" else None,
    }


def test_sla_page_mostra_kpis_do_periodo_e_metas(authed_client, com_zendesk: None) -> None:
    with patch("itgov.api.v1.zendesk.get_cached_sla_detail", return_value=_sla_detail(34.9)):
        html = authed_client.get("/gov/sla").get_data(as_text=True)

    assert "SLA Geral" in html
    assert "SLA cumprido (30d)" in html
    assert "34.9" in html
    assert "1ª resposta no prazo" in html
    assert "Meta: 1ª resposta 4.0h · resolução 33.0h (úteis)" in html
    assert "Abertos com SLA violado" in html


def test_sla_page_sem_dados_nao_mostra_100(authed_client, com_zendesk: None) -> None:
    """Sem tickets avaliáveis, o KPI é "—", nunca 100%."""
    with patch("itgov.api.v1.zendesk.get_cached_sla_detail", return_value=_sla_detail(None, source="itil_default")):
        resp = authed_client.get("/gov/sla")

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "metas padrão ITIL" in html
    assert "100.0%" not in html


@pytest.mark.parametrize("compliance", [34.9, None])
def test_zendesk_mttr_page(authed_client, com_zendesk: None, compliance: float | None) -> None:
    mttr = {
        "total_open": 65,
        "breached": 63,
        "compliance_pct": compliance,
        "first_reply_compliance_pct": None,
        "resolution_compliance_pct": None,
        "avg_first_reply_minutes": None,
        "period_total": 129,
        "period_breached": 84,
        "period_unknown": 0,
        "window_days": 30,
        "avg_age_hours": 400.0,
        "sla_source": "zendesk_policy",
        "sla_policy": "SLA Geral",
        "csat_pct": None,
        "csat_sample": 0,
        "csat_good": 0,
        "csat_bad": 0,
    }
    with (
        patch("itgov.api.v1.zendesk.get_cached_mttr_summary", return_value=mttr),
        patch("itgov.api.v1.zendesk.get_cached_volume_by_status", return_value={"open": 65}),
    ):
        resp = authed_client.get("/gov/zendesk")

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Abertos com SLA violado" in html
    assert "abertos há mais de 8h" not in html
