"""Agregações de SLA servidas às páginas (/gov/sla e /gov/zendesk)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from itgov.api.v1 import zendesk as zendesk_api
from itgov.models.zendesk import CSATSummary, Ticket, TicketMetricSet
from itgov.services import sla_evaluator

NOW = datetime.now(UTC)


def _ticket(
    id: int, status: str, breach_in: timedelta | None = None, reply: int | None = None, wait: int | None = None
) -> Ticket:
    slas = []
    if breach_in is not None:
        breach_at = (NOW + breach_in).strftime("%Y-%m-%dT%H:%M:%SZ")
        slas = [{"metric": "requester_wait_time", "stage": "active", "breach_at": breach_at}]
    t = Ticket.model_validate(
        {
            "id": id,
            "subject": f"T{id}",
            "status": status,
            "priority": "normal",
            "created_at": (NOW - timedelta(days=id)).isoformat(),
            "updated_at": (NOW - timedelta(days=id)).isoformat(),
            "requester_id": 1,
            "slas": {"policy_metrics": slas},
        }
    )
    if reply is not None or wait is not None:
        t.metric_set = TicketMetricSet(
            reply_business_minutes=reply, requester_wait_business_minutes=wait, solved_at=NOW - timedelta(days=1)
        )
    return t


OPEN = [
    _ticket(1, "open", breach_in=timedelta(hours=-1), reply=30),  # resolução estourada
    _ticket(2, "new", breach_in=timedelta(hours=3), reply=30),  # no prazo
]
SOLVED = [
    _ticket(3, "solved", reply=60, wait=100),  # ok
    _ticket(4, "closed", reply=600, wait=100),  # 1ª resposta estourada
]


@pytest.fixture
def fake_svc() -> MagicMock:
    targets = sla_evaluator.targets_from_policies(
        [
            {
                "title": "SLA Geral",
                "position": 1,
                "policy_metrics": [
                    {"priority": "normal", "metric": "first_reply_time", "target": 240},
                    {"priority": "normal", "metric": "requester_wait_time", "target": 1980},
                ],
            }
        ]
    )
    svc = MagicMock()
    svc.__enter__.return_value = svc
    svc.get_open_tickets.return_value = OPEN
    svc.get_solved_tickets.return_value = SOLVED
    svc.get_sla_targets.return_value = targets
    svc.get_sla_metrics.return_value = sla_evaluator.summarize(SOLVED, targets, window_days=30)
    svc.get_csat_summary.return_value = CSATSummary(total_ratings=0, good=0, bad=0)
    return svc


@pytest.fixture(autouse=True)
def _sem_cache() -> None:
    zendesk_api._cache_sla = None
    zendesk_api._cache_mttr = None


def test_sla_detail_usa_estado_real_do_sla(fake_svc: MagicMock) -> None:
    with patch.object(zendesk_api, "_svc", return_value=fake_svc):
        data = zendesk_api.get_cached_sla_detail()

    normal = data["by_priority"]["normal"]
    assert normal["count"] == 2
    assert normal["breached"] == 1
    assert normal["compliance_pct"] == 50.0
    assert normal["first_reply_h"] == 4.0
    assert normal["resolution_h"] == 33.0
    assert data["backlog_breached"] == 1
    assert {t["id"]: t["breached"] for t in data["oldest"]} == {1: True, 2: False}
    assert data["period"]["compliance_pct"] == 50.0
    assert data["sla_source"] == sla_evaluator.SOURCE_POLICY
    assert data["sla_policy"] == "SLA Geral"
    assert data["resolved_7d"] == 2  # usa solved_at do metric_set, não updated_at


def test_mttr_summary_separa_fila_e_periodo(fake_svc: MagicMock) -> None:
    with patch.object(zendesk_api, "_svc", return_value=fake_svc):
        data = zendesk_api.get_cached_mttr_summary()

    assert data["total_open"] == 2
    assert data["breached"] == 1  # fila aberta
    assert data["compliance_pct"] == 50.0  # resolvidos na janela
    assert data["first_reply_compliance_pct"] == 50.0
    assert data["resolution_compliance_pct"] == 100.0
    assert data["period_total"] == 2
    assert data["window_days"] == 30
    fake_svc.get_sla_metrics.assert_called_once_with(days=30)
