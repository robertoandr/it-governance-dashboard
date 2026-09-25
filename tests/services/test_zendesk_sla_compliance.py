"""Testes do SLA de tickets Zendesk (``sla_evaluator`` + ``ZendeskService``).

Regras sob teste (ver docstring de ``itgov.services.sla_evaluator``):

* Métrica concluída → minutos úteis do ``metric_sets`` vs meta da prioridade.
* Métrica em andamento → ``breach_at`` calculado pelo Zendesk.
* Sem dados → ``unknown``, fora do denominador (nunca conta como no prazo).
* Metas vêm da política de SLA do Zendesk; sem política, padrão ITIL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from itgov.models.zendesk import Ticket, TicketMetricSet
from itgov.services import sla_evaluator
from itgov.services.sla_evaluator import SLAStatus, evaluate_ticket, summarize, targets_from_policies
from itgov.services.zendesk_service import ZendeskService

SUBDOMAIN = "empresa"
BASE_URL = f"https://{SUBDOMAIN}.zendesk.com"
NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)

POLICY = {
    "id": 1,
    "title": "SLA Geral",
    "position": 1,
    "policy_metrics": [
        {"priority": "normal", "metric": "first_reply_time", "target": 240, "business_hours": True},
        {"priority": "normal", "metric": "requester_wait_time", "target": 1980, "business_hours": True},
        {"priority": "urgent", "metric": "first_reply_time", "target": 120, "business_hours": True},
        {"priority": "urgent", "metric": "next_reply_time", "target": 60, "business_hours": True},
    ],
}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw_ticket(
    id: int = 1,
    status: str = "open",
    priority: str | None = "normal",
    created_at: datetime = NOW - timedelta(hours=1),
    slas: list[dict] | None = None,
) -> dict:
    return {
        "id": id,
        "subject": f"Ticket {id}",
        "status": status,
        "priority": priority,
        "created_at": _iso(created_at),
        "updated_at": _iso(created_at),
        "requester_id": 99,
        "tags": [],
        "slas": {"policy_metrics": slas or []},
    }


def _ticket(
    status: str = "open",
    priority: str | None = "normal",
    created_at: datetime = NOW - timedelta(hours=1),
    slas: list[dict] | None = None,
    reply: int | None = None,
    wait: int | None = None,
    id: int = 1,
) -> Ticket:
    t = Ticket.model_validate(_raw_ticket(id, status, priority, created_at, slas))
    if reply is not None or wait is not None:
        t.metric_set = TicketMetricSet(reply_business_minutes=reply, requester_wait_business_minutes=wait)
    return t


def _running(metric: str, breach_at: datetime | None, stage: str = "active") -> dict:
    return {"metric": metric, "stage": stage, "breach_at": _iso(breach_at) if breach_at else None}


@pytest.fixture()
def policy_targets() -> sla_evaluator.SLATargets:
    targets = targets_from_policies([POLICY])
    assert targets is not None
    return targets


class TestTargetsFromPolicies:
    def test_reads_policy_targets_in_minutes(self, policy_targets: sla_evaluator.SLATargets) -> None:
        normal = policy_targets.for_priority("normal")
        assert normal.first_reply_minutes == 240
        assert normal.resolution_minutes == 1980
        assert policy_targets.source == sla_evaluator.SOURCE_POLICY
        assert policy_targets.policy_name == "SLA Geral"

    def test_missing_metrics_fall_back_to_itil(self, policy_targets: sla_evaluator.SLATargets) -> None:
        """urgent só define 1ª resposta; next_reply_time é ignorado; resolução herda ITIL."""
        urgent = policy_targets.for_priority("urgent")
        assert urgent.first_reply_minutes == 120
        assert urgent.resolution_minutes == sla_evaluator.ITIL_DEFAULT_TARGETS["urgent"].resolution_minutes
        assert policy_targets.for_priority("low") == sla_evaluator.ITIL_DEFAULT_TARGETS["low"]

    def test_uses_lowest_position_policy(self) -> None:
        other = {**POLICY, "title": "VIP", "position": 0, "policy_metrics": []}
        targets = targets_from_policies([POLICY, other])
        assert targets is not None
        assert targets.policy_name == "VIP"

    def test_no_policies_returns_none(self) -> None:
        assert targets_from_policies([]) is None

    def test_ticket_without_priority_uses_normal(self, policy_targets: sla_evaluator.SLATargets) -> None:
        assert policy_targets.for_priority(None).first_reply_minutes == 240


class TestEvaluateTicket:
    def test_measured_reply_within_target_is_ok(self, policy_targets: sla_evaluator.SLATargets) -> None:
        result = evaluate_ticket(_ticket(reply=240), policy_targets, NOW)
        assert result.first_reply == SLAStatus.OK

    def test_measured_reply_over_target_is_breached(self, policy_targets: sla_evaluator.SLATargets) -> None:
        result = evaluate_ticket(_ticket(reply=241), policy_targets, NOW)
        assert result.first_reply == SLAStatus.BREACHED
        assert result.overall == SLAStatus.BREACHED

    def test_old_ticket_is_not_breached_by_age_alone(self, policy_targets: sla_evaluator.SLATargets) -> None:
        """Regressão: a heurística antiga marcava breach por idade > 8h corridas."""
        t = _ticket(
            created_at=NOW - timedelta(days=3),
            reply=30,
            slas=[_running("requester_wait_time", NOW + timedelta(hours=5))],
        )
        result = evaluate_ticket(t, policy_targets, NOW)
        assert result.overall == SLAStatus.OK

    def test_running_clock_past_breach_at_is_breached(self, policy_targets: sla_evaluator.SLATargets) -> None:
        t = _ticket(slas=[_running("first_reply_time", NOW - timedelta(minutes=1))])
        assert evaluate_ticket(t, policy_targets, NOW).first_reply == SLAStatus.BREACHED

    def test_running_clock_before_breach_at_is_ok(self, policy_targets: sla_evaluator.SLATargets) -> None:
        t = _ticket(slas=[_running("first_reply_time", NOW + timedelta(minutes=1))])
        assert evaluate_ticket(t, policy_targets, NOW).first_reply == SLAStatus.OK

    def test_paused_resolution_keeps_breach_at_rule(self, policy_targets: sla_evaluator.SLATargets) -> None:
        within = _ticket(status="pending", slas=[_running("requester_wait_time", NOW + timedelta(days=1), "paused")])
        late = _ticket(status="pending", slas=[_running("requester_wait_time", NOW - timedelta(days=1), "paused")])
        assert evaluate_ticket(within, policy_targets, NOW).resolution == SLAStatus.OK
        assert evaluate_ticket(late, policy_targets, NOW).resolution == SLAStatus.BREACHED

    def test_open_ticket_wait_time_is_not_final(self, policy_targets: sla_evaluator.SLATargets) -> None:
        """Em aberto, requester_wait parcial não decide — vale o breach_at do Zendesk."""
        t = _ticket(wait=5000, slas=[_running("requester_wait_time", NOW + timedelta(hours=2))])
        assert evaluate_ticket(t, policy_targets, NOW).resolution == SLAStatus.OK

    def test_solved_ticket_uses_measured_wait(self, policy_targets: sla_evaluator.SLATargets) -> None:
        ok = _ticket(status="solved", reply=60, wait=1980)
        late = _ticket(status="closed", reply=60, wait=1981)
        assert evaluate_ticket(ok, policy_targets, NOW).resolution == SLAStatus.OK
        assert evaluate_ticket(late, policy_targets, NOW).resolution == SLAStatus.BREACHED

    def test_no_data_is_unknown_not_ok(self, policy_targets: sla_evaluator.SLATargets) -> None:
        """Contrato 2: sem política aplicável, o ticket é unknown."""
        result = evaluate_ticket(_ticket(), policy_targets, NOW)
        assert result.first_reply == SLAStatus.UNKNOWN
        assert result.resolution == SLAStatus.UNKNOWN
        assert result.overall == SLAStatus.UNKNOWN

    def test_solved_without_reply_counts_resolution_only(self, policy_targets: sla_evaluator.SLATargets) -> None:
        t = _ticket(status="solved", reply=None, wait=100)
        result = evaluate_ticket(t, policy_targets, NOW)
        assert result.first_reply == SLAStatus.UNKNOWN
        assert result.overall == SLAStatus.OK

    def test_itil_default_recent_ticket_is_provably_ok(self) -> None:
        """Sem política: tempo corrido abaixo da meta implica tempo útil abaixo da meta."""
        t = _ticket(created_at=NOW - timedelta(minutes=10))
        result = evaluate_ticket(t, sla_evaluator.default_targets(), NOW)
        assert result.overall == SLAStatus.OK

    def test_itil_default_old_ticket_without_data_is_unknown(self) -> None:
        t = _ticket(created_at=NOW - timedelta(days=5))
        result = evaluate_ticket(t, sla_evaluator.default_targets(), NOW)
        assert result.overall == SLAStatus.UNKNOWN


class TestSummarize:
    def test_compliance_excludes_unknown(self, policy_targets: sla_evaluator.SLATargets) -> None:
        tickets = [
            _ticket(id=1, status="solved", reply=60, wait=100),  # ok
            _ticket(id=2, status="solved", reply=500, wait=100),  # breached (1ª resposta)
            _ticket(id=3, status="solved", reply=60, wait=3000),  # breached (resolução)
            _ticket(id=4, status="solved"),  # unknown
        ]
        metric = summarize(tickets, policy_targets, NOW, window_days=30)
        assert metric.total_tickets == 4
        assert metric.breached == 2
        assert metric.unknown == 1
        assert metric.compliance_pct == pytest.approx(33.3, abs=0.1)
        assert metric.first_reply_compliance_pct == pytest.approx(66.7, abs=0.1)
        assert metric.resolution_compliance_pct == pytest.approx(66.7, abs=0.1)
        assert metric.avg_first_reply_minutes == pytest.approx((60 + 500 + 60) / 3, abs=0.1)
        assert metric.window_days == 30

    def test_empty_is_no_data_not_100(self, policy_targets: sla_evaluator.SLATargets) -> None:
        metric = summarize([], policy_targets, NOW)
        assert metric.total_tickets == 0
        assert metric.compliance_pct is None
        assert metric.avg_first_reply_minutes is None

    def test_all_unknown_is_no_data(self, policy_targets: sla_evaluator.SLATargets) -> None:
        metric = summarize([_ticket(id=1), _ticket(id=2)], policy_targets, NOW)
        assert metric.unknown == 2
        assert metric.compliance_pct is None


@pytest.fixture()
def svc() -> ZendeskService:
    return ZendeskService(subdomain=SUBDOMAIN, email="a@b.com", api_token="tok", max_retries=1)


class TestServiceSLA:
    @respx.mock
    def test_targets_from_zendesk_policy(self, svc: ZendeskService) -> None:
        route = respx.get(f"{BASE_URL}/api/v2/slas/policies.json").mock(
            return_value=httpx.Response(200, json={"sla_policies": [POLICY]})
        )
        targets = svc.get_sla_targets()
        assert targets.source == sla_evaluator.SOURCE_POLICY
        svc.get_sla_targets()
        assert route.call_count == 1  # memorizado na instância

    @respx.mock
    def test_targets_fall_back_to_itil_when_plan_has_no_sla(self, svc: ZendeskService) -> None:
        respx.get(f"{BASE_URL}/api/v2/slas/policies.json").mock(return_value=httpx.Response(403, json={}))
        targets = svc.get_sla_targets()
        assert targets.source == sla_evaluator.SOURCE_ITIL_DEFAULT

    @respx.mock
    def test_get_sla_metrics_uses_solved_tickets_with_sideloads(self, svc: ZendeskService) -> None:
        respx.get(f"{BASE_URL}/api/v2/slas/policies.json").mock(
            return_value=httpx.Response(200, json={"sla_policies": [POLICY]})
        )
        search = respx.get(f"{BASE_URL}/api/v2/search.json").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [_raw_ticket(1, status="solved"), _raw_ticket(2, status="closed")],
                    "metric_sets": [
                        {
                            "ticket_id": 1,
                            "reply_time_in_minutes": {"calendar": 90, "business": 60},
                            "requester_wait_time_in_minutes": {"calendar": 200, "business": 100},
                            "solved_at": _iso(NOW),
                        },
                        {
                            "ticket_id": 2,
                            "reply_time_in_minutes": {"calendar": 900, "business": 600},
                            "requester_wait_time_in_minutes": {"calendar": 200, "business": 100},
                        },
                    ],
                    "next_page": None,
                },
            )
        )
        metric = svc.get_sla_metrics(days=30)

        params = search.calls.last.request.url.params
        assert params["include"] == "tickets(slas,metric_sets)"
        assert "status:solved" in params["query"]
        assert "status:closed" in params["query"]
        assert "solved>" in params["query"]
        assert metric.total_tickets == 2
        assert metric.breached == 1
        assert metric.compliance_pct == 50.0
        assert metric.first_reply_compliance_pct == 50.0
        assert metric.resolution_compliance_pct == 100.0
