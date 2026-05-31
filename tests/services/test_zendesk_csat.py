"""Tests for CSAT score calculation with unoffered/offered exclusion.

CSAT implementation in zendesk_service.py:
  - get_satisfaction_ratings() filters raw API scores to ("good", "bad") only
  - get_csat_summary() computes: good / (good + bad) * 100

Business rule: "unoffered" (survey not sent) and "offered" (sent, no response)
MUST NOT count in the denominator. Only answered ratings (good/bad) count.

Risk without this test: a filter bug would silently include unoffered as
implicit "bad" → CSAT artificially lowered → wrong business decisions.

Edge contract: total=0 (no answered ratings) → csat_pct=None, sample_size=0.
None distinguishes "no data" from "0% satisfaction" (all bad). Consumers
must render None as "N/A", not "0%".
"""

from __future__ import annotations

import httpx
import pytest
import respx

from itgov.services.zendesk_service import ZendeskService

SUBDOMAIN = "empresa"
BASE_URL = f"https://{SUBDOMAIN}.zendesk.com"
RATINGS_URL = f"{BASE_URL}/api/v2/satisfaction_ratings.json"


@pytest.fixture()
def svc() -> ZendeskService:
    return ZendeskService(subdomain=SUBDOMAIN, email="a@b.com", api_token="tok", max_retries=1)


def _rating(score: str, ticket_id: int = 1) -> dict:
    return {
        "ticket_id": ticket_id,
        "score": score,
        "created_at": "2026-05-31T10:00:00Z",
    }


def _ratings_page(ratings: list[dict]) -> dict:
    return {
        "satisfaction_ratings": ratings,
        "meta": {"has_more": False},
        "links": {"next": None},
    }


class TestCSATBaselines:
    @respx.mock
    def test_100_percent_all_good(self, svc: ZendeskService) -> None:
        """5 good, 0 bad → 100.0%, sample_size=5."""
        ratings = [_rating("good", i) for i in range(1, 6)]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 5
        assert summary.sample_size == 5
        assert summary.good == 5
        assert summary.bad == 0
        assert summary.csat_pct == 100.0

    @respx.mock
    def test_0_percent_all_bad(self, svc: ZendeskService) -> None:
        """0 good, 5 bad → 0.0% (has data, genuinely bad), sample_size=5."""
        ratings = [_rating("bad", i) for i in range(1, 6)]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 5
        assert summary.sample_size == 5
        assert summary.good == 0
        assert summary.csat_pct == 0.0  # 0.0 here means "all bad", NOT "no data"

    @respx.mock
    def test_75_percent_mixed(self, svc: ZendeskService) -> None:
        """3 good, 1 bad → 75.0%, sample_size=4."""
        ratings = [_rating("good", 1), _rating("good", 2), _rating("good", 3), _rating("bad", 4)]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 4
        assert summary.sample_size == 4
        assert summary.good == 3
        assert summary.bad == 1
        assert summary.csat_pct == 75.0


class TestCSATExclusion:
    """Regression guard: unoffered/offered must not enter the denominator."""

    @respx.mock
    def test_unoffered_excluded_from_denominator(self, svc: ZendeskService) -> None:
        """3 good + 1 bad + 10 unoffered → 75%, sample_size=4 (not 14)."""
        ratings = (
            [_rating("good", i) for i in range(1, 4)]
            + [_rating("bad", 4)]
            + [_rating("unoffered", i) for i in range(5, 15)]
        )
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        # Only good+bad count — unoffered stripped at ingest
        assert summary.total_ratings == 4
        assert summary.sample_size == 4
        assert summary.csat_pct == 75.0

    @respx.mock
    def test_offered_no_response_excluded(self, svc: ZendeskService) -> None:
        """3 good + 1 bad + 5 offered → 75%, sample_size=4 (not 9)."""
        ratings = (
            [_rating("good", i) for i in range(1, 4)]
            + [_rating("bad", 4)]
            + [_rating("offered", i) for i in range(5, 10)]
        )
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 4
        assert summary.sample_size == 4
        assert summary.csat_pct == 75.0

    @respx.mock
    def test_mixed_unoffered_offered_and_answered(self, svc: ZendeskService) -> None:
        """Mix of all states: only good/bad count in total_ratings, sample_size and csat_pct."""
        ratings = [
            _rating("good", 1),
            _rating("good", 2),
            _rating("good", 3),
            _rating("bad", 4),
            _rating("unoffered", 5),
            _rating("unoffered", 6),
            _rating("offered", 7),
            _rating("offered", 8),
            _rating("offered", 9),
        ]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 4  # only good+bad
        assert summary.sample_size == 4
        assert summary.good == 3
        assert summary.bad == 1
        assert summary.csat_pct == 75.0


class TestCSATEdgeCases:
    @respx.mock
    def test_only_unoffered_returns_none(self, svc: ZendeskService) -> None:
        """Only unoffered — no answered ratings → csat_pct=None, sample_size=0.

        None means "no data", NOT "0% satisfaction". Consumers must render
        as "N/A" to avoid misleading stakeholders.
        """
        ratings = [_rating("unoffered", i) for i in range(1, 6)]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 0
        assert summary.sample_size == 0
        assert summary.csat_pct is None

    @respx.mock
    def test_empty_ratings_returns_none(self, svc: ZendeskService) -> None:
        """Empty list → total=0, csat_pct=None, sample_size=0, no error."""
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page([])))
        summary = svc.get_csat_summary()
        assert summary.total_ratings == 0
        assert summary.sample_size == 0
        assert summary.csat_pct is None

    @respx.mock
    def test_only_good_no_bad(self, svc: ZendeskService) -> None:
        """Only good, no bad → 100%."""
        ratings = [_rating("good", i) for i in range(1, 4)]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.bad == 0
        assert summary.csat_pct == 100.0

    @respx.mock
    def test_decimal_precision_two_of_three_good(self, svc: ZendeskService) -> None:
        """2 good, 1 bad → 66.7% (1 decimal place)."""
        ratings = [_rating("good", 1), _rating("good", 2), _rating("bad", 3)]
        respx.get(RATINGS_URL).mock(return_value=httpx.Response(200, json=_ratings_page(ratings)))
        summary = svc.get_csat_summary()
        assert summary.csat_pct == pytest.approx(66.7, abs=0.1)
