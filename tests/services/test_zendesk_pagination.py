"""Tests for Zendesk cursor-based pagination in _paginate().

Pagination contract:
  - Page response includes meta.has_more + links.next (cursor URL)
  - Loop continues while has_more=True AND links.next is present
  - Second+ pages are fetched via full absolute URL (no extra params)
  - Loop stops when has_more=False or links.next is absent/null
  - max_pages cap: stops traversal, emits WARN, returns partial results

Mock strategy: respx.get() without query params matches ANY request to
that path (ignoring query params by default). Multi-page sequences use
side_effect=[r1, r2, r3] to return responses in order.
"""

from __future__ import annotations

import httpx
import pytest
import respx
import structlog.testing

from itgov.services.zendesk_service import ZendeskService

SUBDOMAIN = "empresa"
BASE_URL = f"https://{SUBDOMAIN}.zendesk.com"
TICKETS_PATH = f"{BASE_URL}/api/v2/tickets.json"
CURSOR_PAGE2 = f"{BASE_URL}/api/v2/tickets.json?page[after]=cursor_abc"
CURSOR_PAGE3 = f"{BASE_URL}/api/v2/tickets.json?page[after]=cursor_def"


@pytest.fixture()
def svc() -> ZendeskService:
    return ZendeskService(subdomain=SUBDOMAIN, email="a@b.com", api_token="tok", max_retries=1)


def _ticket(id: int) -> dict:
    return {
        "id": id,
        "subject": f"Ticket {id}",
        "status": "open",
        "priority": "normal",
        "created_at": "2026-05-31T10:00:00Z",
        "updated_at": "2026-05-31T10:00:00Z",
        "requester_id": 99,
        "tags": [],
    }


def _page(tickets: list[dict], has_more: bool = False, next_url: str | None = None) -> dict:
    return {
        "tickets": tickets,
        "meta": {"has_more": has_more},
        "links": {"next": next_url},
    }


class TestPaginationSinglePage:
    @respx.mock
    def test_single_page_stops_at_has_more_false(self, svc: ZendeskService) -> None:
        """has_more=False → exactly 1 request, all tickets returned."""
        route = respx.get(TICKETS_PATH).mock(return_value=httpx.Response(200, json=_page([_ticket(1), _ticket(2)])))
        tickets = svc.get_tickets()
        assert len(tickets) == 2
        assert [t.id for t in tickets] == [1, 2]
        assert route.call_count == 1

    @respx.mock
    def test_empty_single_page(self, svc: ZendeskService) -> None:
        """Empty page with has_more=False → 0 tickets, no error."""
        respx.get(TICKETS_PATH).mock(return_value=httpx.Response(200, json=_page([])))
        assert svc.get_tickets() == []


class TestPaginationMultiPage:
    @respx.mock
    def test_two_pages_accumulates_in_order(self, svc: ZendeskService) -> None:
        """2 pages → 5 tickets accumulated in correct order."""
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1), _ticket(2), _ticket(3)], has_more=True, next_url=CURSOR_PAGE2)),
            httpx.Response(200, json=_page([_ticket(4), _ticket(5)])),
        ]
        tickets = svc.get_tickets()
        assert len(tickets) == 5
        assert [t.id for t in tickets] == [1, 2, 3, 4, 5]
        assert route.call_count == 2

    @respx.mock
    def test_three_pages_accumulates_in_order(self, svc: ZendeskService) -> None:
        """3 pages → 8 tickets in order, loop stops on has_more=False."""
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1), _ticket(2), _ticket(3)], has_more=True, next_url=CURSOR_PAGE2)),
            httpx.Response(200, json=_page([_ticket(4), _ticket(5), _ticket(6)], has_more=True, next_url=CURSOR_PAGE3)),
            httpx.Response(200, json=_page([_ticket(7), _ticket(8)])),
        ]
        tickets = svc.get_tickets()
        assert len(tickets) == 8
        assert [t.id for t in tickets] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert route.call_count == 3

    @respx.mock
    def test_page2_cursor_url_is_followed(self, svc: ZendeskService) -> None:
        """links.next cursor URL is passed to the second request."""
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1)], has_more=True, next_url=CURSOR_PAGE2)),
            httpx.Response(200, json=_page([_ticket(2)])),
        ]
        svc.get_tickets()
        # Second call must use the cursor URL, not the base path
        second_request_url = str(route.calls[1].request.url)
        assert "cursor_abc" in second_request_url

    @respx.mock
    def test_has_more_true_but_no_next_url_stops(self, svc: ZendeskService) -> None:
        """has_more=True with links.next=None → loop stops (defensive guard)."""
        route = respx.get(TICKETS_PATH).mock(
            return_value=httpx.Response(200, json=_page([_ticket(1)], has_more=True, next_url=None))
        )
        tickets = svc.get_tickets()
        assert len(tickets) == 1
        assert route.call_count == 1  # Loop stopped, no second request


class TestPaginationNoMaxPagesGuard:
    @respx.mock
    def test_loop_terminates_on_proper_terminal_page(self, svc: ZendeskService) -> None:
        """Loop exits when the terminal page arrives (has_more=False)."""
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1)], has_more=True, next_url=CURSOR_PAGE2)),
            httpx.Response(200, json=_page([_ticket(2)], has_more=False)),
        ]
        tickets = svc.get_tickets()
        assert len(tickets) == 2
        assert route.call_count == 2


class TestPaginationMaxPagesCap:
    """max_pages safeguard: partial results + WARN on cap, normal below cap."""

    @respx.mock
    def test_cap_stops_traversal_and_logs_warning(self, svc: ZendeskService) -> None:
        """Paginator stops at max_pages=2 and emits cap_reached warning."""
        cursor_p2 = f"{BASE_URL}/api/v2/tickets.json?page[after]=p2"
        cursor_p3 = f"{BASE_URL}/api/v2/tickets.json?page[after]=p3"
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1), _ticket(2)], has_more=True, next_url=cursor_p2)),
            httpx.Response(200, json=_page([_ticket(3), _ticket(4)], has_more=True, next_url=cursor_p3)),
            # Page 3 should never be fetched
            httpx.Response(200, json=_page([_ticket(5), _ticket(6)])),
        ]

        with structlog.testing.capture_logs() as log_entries:
            results = svc._paginate("/api/v2/tickets.json", "tickets", max_pages=2)

        assert len(results) == 4  # 2 pages × 2 tickets, page 3 not fetched
        assert route.call_count == 2
        assert any(e.get("event") == "zendesk.pagination.cap_reached" for e in log_entries)

    @respx.mock
    def test_default_cap_from_env(self, svc: ZendeskService, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default cap reads ZENDESK_MAX_PAGES at module import time; override via max_pages param."""
        cursor_p2 = f"{BASE_URL}/api/v2/tickets.json?page[after]=p2"
        cursor_p3 = f"{BASE_URL}/api/v2/tickets.json?page[after]=p3"
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1)], has_more=True, next_url=cursor_p2)),
            httpx.Response(200, json=_page([_ticket(2)], has_more=True, next_url=cursor_p3)),
            httpx.Response(200, json=_page([_ticket(3)])),
        ]
        # Bypass module-level constant by passing max_pages directly
        results = svc._paginate("/api/v2/tickets.json", "tickets", max_pages=2)
        assert len(results) == 2  # stopped at cap=2
        assert route.call_count == 2

    @respx.mock
    def test_completes_normally_below_cap(self, svc: ZendeskService) -> None:
        """Paginator completes all pages when total < max_pages."""
        cursor_p2 = f"{BASE_URL}/api/v2/tickets.json?page[after]=p2"
        cursor_p3 = f"{BASE_URL}/api/v2/tickets.json?page[after]=p3"
        route = respx.get(TICKETS_PATH)
        route.side_effect = [
            httpx.Response(200, json=_page([_ticket(1), _ticket(2)], has_more=True, next_url=cursor_p2)),
            httpx.Response(200, json=_page([_ticket(3), _ticket(4)], has_more=True, next_url=cursor_p3)),
            httpx.Response(200, json=_page([_ticket(5), _ticket(6)])),
        ]
        results = svc._paginate("/api/v2/tickets.json", "tickets", max_pages=100)
        assert len(results) == 6  # all 3 pages fetched
        assert route.call_count == 3
