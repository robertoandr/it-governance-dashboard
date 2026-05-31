"""Tests for SLA compliance formula in get_sla_metrics().

The formula under test:
    compliance = round((1 - breached / total) * 100, 1) if total else 100.0

Tickets with age_hours > 8.0 are considered breached.
Breach is controlled by setting created_at far in the past (10h ago).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from itgov.services.zendesk_service import ZendeskService

SUBDOMAIN = "empresa"
BASE_URL = f"https://{SUBDOMAIN}.zendesk.com"


@pytest.fixture()
def svc() -> ZendeskService:
    return ZendeskService(subdomain=SUBDOMAIN, email="a@b.com", api_token="tok", max_retries=1)


def _ts(hours_ago: float) -> str:
    """ISO 8601 timestamp N hours in the past."""
    dt = datetime.now(UTC) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return _ts(0)


def _old() -> str:
    """10h ago — exceeds the 8h SLA threshold."""
    return _ts(10)


def _ticket(id: int = 1, status: str = "open", created_at: str | None = None) -> dict:
    ts = created_at or _now()
    return {
        "id": id,
        "subject": f"Ticket {id}",
        "status": status,
        "priority": "normal",
        "created_at": ts,
        "updated_at": ts,
        "requester_id": 99,
        "tags": [],
    }


def _page(tickets: list[dict]) -> dict:
    return {"tickets": tickets, "meta": {"has_more": False}, "links": {"next": None}}


class TestSLAComplianceFormula:
    """Arithmetic coverage for compliance = round((1 - breached/total)*100, 1)."""

    @respx.mock
    def test_100_percent_when_no_breaches(self, svc: ZendeskService) -> None:
        """All tickets recent → 100% compliance."""
        tickets = [_ticket(i, created_at=_now()) for i in range(1, 6)]
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_page(tickets)))
        metric = svc.get_sla_metrics()
        assert metric.total_tickets == 5
        assert metric.breached == 0
        assert metric.compliance_pct == 100.0

    @respx.mock
    def test_0_percent_when_all_breached(self, svc: ZendeskService) -> None:
        """All tickets old → 0% compliance."""
        tickets = [_ticket(i, created_at=_old()) for i in range(1, 5)]
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_page(tickets)))
        metric = svc.get_sla_metrics()
        assert metric.total_tickets == 4
        assert metric.breached == 4
        assert metric.compliance_pct == 0.0

    @respx.mock
    def test_50_percent_when_half_breached(self, svc: ZendeskService) -> None:
        """2 of 4 old → 50% compliance."""
        tickets = [
            _ticket(1, created_at=_old()),
            _ticket(2, created_at=_old()),
            _ticket(3, created_at=_now()),
            _ticket(4, created_at=_now()),
        ]
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_page(tickets)))
        metric = svc.get_sla_metrics()
        assert metric.breached == 2
        assert metric.compliance_pct == 50.0

    @respx.mock
    def test_decimal_precision_one_of_three(self, svc: ZendeskService) -> None:
        """1 of 3 breached → 66.7% (rounded to 1 decimal)."""
        tickets = [
            _ticket(1, created_at=_old()),
            _ticket(2, created_at=_now()),
            _ticket(3, created_at=_now()),
        ]
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_page(tickets)))
        metric = svc.get_sla_metrics()
        assert metric.breached == 1
        assert metric.compliance_pct == pytest.approx(66.7, abs=0.1)

    @respx.mock
    def test_single_ticket_breached(self, svc: ZendeskService) -> None:
        """1 of 1 breached → 0%."""
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(
            return_value=httpx.Response(200, json=_page([_ticket(1, created_at=_old())]))
        )
        metric = svc.get_sla_metrics()
        assert metric.total_tickets == 1
        assert metric.breached == 1
        assert metric.compliance_pct == 0.0

    @respx.mock
    def test_zero_total_returns_100_no_division_error(self, svc: ZendeskService) -> None:
        """Empty ticket list → 100% (no ZeroDivisionError, nothing breached)."""
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_page([])))
        metric = svc.get_sla_metrics()
        assert metric.total_tickets == 0
        assert metric.breached == 0
        assert metric.compliance_pct == 100.0

    @respx.mock
    def test_solved_tickets_excluded_from_sla(self, svc: ZendeskService) -> None:
        """Solved/closed tickets are not open → excluded from SLA calculation."""
        tickets = [
            _ticket(1, status="open", created_at=_old()),
            _ticket(2, status="solved", created_at=_old()),  # excluded
            _ticket(3, status="closed", created_at=_old()),  # excluded
        ]
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_page(tickets)))
        metric = svc.get_sla_metrics()
        # Only ticket 1 is open and old → 1 breach, 1 total
        assert metric.total_tickets == 1
        assert metric.breached == 1
        assert metric.compliance_pct == 0.0
