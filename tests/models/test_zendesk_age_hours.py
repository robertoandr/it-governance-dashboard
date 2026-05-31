"""Tests for timezone-safe age_hours computation on Ticket model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from itgov.models.zendesk import Ticket, TicketStatus


def _ticket_with_created_at(dt: datetime) -> Ticket:
    return Ticket(
        id=1,
        subject="Test",
        status=TicketStatus.OPEN,
        requester_id=99,
        created_at=dt,
        updated_at=dt,
    )


class TestAgeHours:
    """Verify age_hours handles all timezone scenarios correctly."""

    def test_age_with_utc_timestamp(self) -> None:
        """Timestamp already in UTC should work normally."""
        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        t = _ticket_with_created_at(one_hour_ago)
        assert 0.99 < t.age_hours < 1.01

    def test_age_with_non_utc_negative_timezone(self) -> None:
        """Non-UTC negative offset must be converted, not just relabeled."""
        sp_tz = timezone(timedelta(hours=-3))
        three_hours_ago_sp = datetime.now(sp_tz) - timedelta(hours=3)
        t = _ticket_with_created_at(three_hours_ago_sp)
        # .replace() would give ~6h or ~0h; .astimezone() gives ~3h
        assert 2.99 < t.age_hours < 3.01, f"Expected ~3h, got {t.age_hours}h"

    def test_age_with_positive_timezone(self) -> None:
        """Positive offset (e.g. Tokyo UTC+9) must also convert correctly."""
        tokyo_tz = timezone(timedelta(hours=9))
        two_hours_ago_tokyo = datetime.now(tokyo_tz) - timedelta(hours=2)
        t = _ticket_with_created_at(two_hours_ago_tokyo)
        assert 1.99 < t.age_hours < 2.01

    def test_age_with_naive_datetime_assumes_utc(self) -> None:
        """Naive datetime must be treated as UTC (backward compat)."""
        naive_past = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
        t = _ticket_with_created_at(naive_past)
        assert 1.99 < t.age_hours < 2.01

    def test_age_future_timestamp_returns_negative(self) -> None:
        """Future timestamp must return a negative age."""
        one_hour_future = datetime.now(UTC) + timedelta(hours=1)
        t = _ticket_with_created_at(one_hour_future)
        assert -1.01 < t.age_hours < -0.99
