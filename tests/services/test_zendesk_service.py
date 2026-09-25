"""Testes para ZendeskService com respx (mock httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from itgov.models.zendesk import TicketStatus
from itgov.services.zendesk_service import ZendeskService

SUBDOMAIN = "empresa"
BASE_URL = f"https://{SUBDOMAIN}.zendesk.com"
EMAIL = "agente@empresa.com"
TOKEN = "fake-api-token"


@pytest.fixture()
def svc() -> ZendeskService:
    return ZendeskService(subdomain=SUBDOMAIN, email=EMAIL, api_token=TOKEN, max_retries=1)


def _ticket(
    id: int = 1,
    subject: str = "Teste",
    status: str = "open",
    priority: str | None = "normal",
    created_at: str = "2026-05-01T10:00:00Z",
    updated_at: str = "2026-05-01T11:00:00Z",
) -> dict:
    return {
        "id": id,
        "subject": subject,
        "status": status,
        "priority": priority,
        "created_at": created_at,
        "updated_at": updated_at,
        "requester_id": 99,
        "tags": ["suporte"],
    }


def _tickets_page(tickets: list[dict], has_more: bool = False) -> dict:
    return {
        "tickets": tickets,
        "meta": {"has_more": has_more},
        "links": {"next": None},
    }


def _search_page(tickets: list[dict], next_page: str | None = None) -> dict:
    return {
        "results": tickets,
        "count": len(tickets),
        "next_page": next_page,
    }


class TestGetTickets:
    @respx.mock
    def test_returns_tickets_parsed(self, svc: ZendeskService) -> None:
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(
            return_value=httpx.Response(200, json=_tickets_page([_ticket(1), _ticket(2)]))
        )
        tickets = svc.get_tickets()
        assert len(tickets) == 2
        assert tickets[0].id == 1
        assert tickets[0].status == TicketStatus.OPEN

    @respx.mock
    def test_filters_by_status(self, svc: ZendeskService) -> None:
        route = respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(
            return_value=httpx.Response(200, json=_tickets_page([_ticket(status="pending")]))
        )
        tickets = svc.get_tickets(status="pending")
        assert len(tickets) == 1
        assert "status=pending" in str(route.calls[0].request.url)

    @respx.mock
    def test_empty_response(self, svc: ZendeskService) -> None:
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_tickets_page([])))
        assert svc.get_tickets() == []

    @respx.mock
    def test_priority_none_is_coerced(self, svc: ZendeskService) -> None:
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(
            return_value=httpx.Response(200, json=_tickets_page([_ticket(priority=None)]))
        )
        tickets = svc.get_tickets()
        assert tickets[0].priority is None


class TestGetOpenTickets:
    @respx.mock
    def test_filters_open_tickets(self, svc: ZendeskService) -> None:
        # get_open_tickets() usa search API — server retorna apenas os status filtrados
        matching = [
            _ticket(1, status="open"),
            _ticket(3, status="new"),
            _ticket(5, status="pending"),
        ]
        respx.get(f"{BASE_URL}/api/v2/search.json").mock(return_value=httpx.Response(200, json=_search_page(matching)))
        open_tickets = svc.get_open_tickets()
        open_ids = {t.id for t in open_tickets}
        assert open_ids == {1, 3, 5}  # open, new, pending

    @respx.mock
    def test_query_uses_implicit_or_for_status(self, svc: ZendeskService) -> None:
        """Zendesk faz OR ao repetir a keyword; o operador OR explícito quebra o filtro."""
        route = respx.get(f"{BASE_URL}/api/v2/search.json").mock(
            return_value=httpx.Response(200, json=_search_page([]))
        )
        svc.get_open_tickets()
        query = route.calls.last.request.url.params["query"]
        assert " OR " not in query
        for status in ("status:new", "status:open", "status:pending"):
            assert status in query


class TestSearchSideloads:
    @respx.mock
    def test_open_tickets_carry_sla_and_metric_set(self, svc: ZendeskService) -> None:
        """A busca pede slas+metric_sets e anexa ambos ao ticket certo."""
        raw = _ticket(1, status="open")
        raw["slas"] = {"policy_metrics": [{"metric": "first_reply_time", "stage": "active", "breach_at": None}]}
        page = _search_page([raw, _ticket(2, status="new")])
        page["metric_sets"] = [
            {
                "ticket_id": 1,
                "reply_time_in_minutes": {"calendar": 50, "business": 30},
                "requester_wait_time_in_minutes": {"calendar": None, "business": None},
            }
        ]
        route = respx.get(f"{BASE_URL}/api/v2/search.json").mock(return_value=httpx.Response(200, json=page))

        tickets = {t.id: t for t in svc.get_open_tickets()}

        assert route.calls.last.request.url.params["include"] == "tickets(slas,metric_sets)"
        assert tickets[1].sla_metrics[0].metric == "first_reply_time"
        assert tickets[1].metric_set is not None
        assert tickets[1].metric_set.reply_business_minutes == 30
        assert tickets[2].metric_set is None
        assert tickets[2].sla_metrics == []


class TestCSAT:
    @respx.mock
    def test_csat_pct_calculated_correctly(self, svc: ZendeskService) -> None:
        ratings = {
            "satisfaction_ratings": [
                {"ticket_id": 1, "score": "good", "created_at": "2026-05-01T10:00:00Z"},
                {"ticket_id": 2, "score": "good", "created_at": "2026-05-01T11:00:00Z"},
                {"ticket_id": 3, "score": "bad", "created_at": "2026-05-01T12:00:00Z"},
            ],
            "meta": {"has_more": False},
            "links": {"next": None},
        }
        respx.get(f"{BASE_URL}/api/v2/satisfaction_ratings.json").mock(return_value=httpx.Response(200, json=ratings))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 3
        assert summary.good == 2
        assert summary.bad == 1
        assert summary.csat_pct == pytest.approx(66.7, abs=0.1)

    @respx.mock
    def test_csat_none_when_no_ratings(self, svc: ZendeskService) -> None:
        respx.get(f"{BASE_URL}/api/v2/satisfaction_ratings.json").mock(
            return_value=httpx.Response(
                200, json={"satisfaction_ratings": [], "meta": {"has_more": False}, "links": {}}
            )
        )
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 0
        assert summary.sample_size == 0
        assert summary.csat_pct is None


class TestVolumeByStatus:
    @respx.mock
    def test_volume_counts_all_statuses(self, svc: ZendeskService) -> None:
        raw = [
            _ticket(1, status="open"),
            _ticket(2, status="open"),
            _ticket(3, status="solved"),
            _ticket(4, status="new"),
        ]
        respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(return_value=httpx.Response(200, json=_tickets_page(raw)))
        vol = svc.get_ticket_volume_by_status()
        assert vol["open"] == 2
        assert vol["solved"] == 1
        assert vol["new"] == 1
        assert vol["pending"] == 0


class TestAuthHeader:
    @respx.mock
    def test_basic_auth_sent_in_headers(self, svc: ZendeskService) -> None:
        import base64

        route = respx.get(f"{BASE_URL}/api/v2/tickets.json").mock(
            return_value=httpx.Response(200, json=_tickets_page([]))
        )
        svc.get_tickets()
        auth_header = route.calls[0].request.headers.get("authorization", "")
        expected = base64.b64encode(f"{EMAIL}/token:{TOKEN}".encode()).decode()
        assert auth_header == f"Basic {expected}"
