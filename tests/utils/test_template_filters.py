"""Tests for app/utils/template_filters.py."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.utils.template_filters import datetime_br

_UTC = ZoneInfo("UTC")
_SP = ZoneInfo("America/Sao_Paulo")


class TestDatetimeBr:
    def test_none_returns_empty_string(self) -> None:
        assert datetime_br(None) == ""

    def test_empty_string_returns_empty_string(self) -> None:
        assert datetime_br("") == ""

    def test_iso_string_with_z_suffix(self) -> None:
        result = datetime_br("2026-06-07T03:00:00Z")
        # UTC 03:00 → Sao Paulo -03:00 = 00:00
        assert result == "07/06/2026 00:00:00"

    def test_iso_string_with_utc_offset(self) -> None:
        result = datetime_br("2026-06-07T03:00:00+00:00")
        assert result == "07/06/2026 00:00:00"

    def test_naive_datetime_treated_as_utc(self) -> None:
        naive = datetime(2026, 6, 7, 3, 0, 0)
        result = datetime_br(naive)
        assert result == "07/06/2026 00:00:00"

    def test_aware_utc_datetime(self) -> None:
        aware = datetime(2026, 6, 7, 15, 30, 0, tzinfo=_UTC)
        result = datetime_br(aware)
        assert result == "07/06/2026 12:30:00"

    def test_format_is_dd_mm_yyyy_hh_mm_ss(self) -> None:
        result = datetime_br("2026-01-05T12:05:09Z")
        assert result == "05/01/2026 09:05:09"

    def test_unparseable_string_returned_as_is(self) -> None:
        result = datetime_br("not-a-date")
        assert result == "not-a-date"
