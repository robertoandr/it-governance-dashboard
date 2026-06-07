"""Jinja2 template filters for the IT Governance Dashboard."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_SP = ZoneInfo("America/Sao_Paulo")
_UTC = ZoneInfo("UTC")


def datetime_br(value: datetime | str | None) -> str:
    """Format a UTC datetime as a Brazilian local timestamp (dd/mm/yyyy HH:MM:SS).

    Accepts datetime objects, ISO-8601 strings, or None.
    Returns an empty string for falsy input so templates render cleanly.
    """
    if not value:
        return ""

    if isinstance(value, str):
        # Normalise Z suffix and parse
        normalized = value.replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError:
            return value  # return original string if unparseable

    if isinstance(value, datetime):
        # Treat naive datetimes as UTC
        if value.tzinfo is None:
            value = value.replace(tzinfo=_UTC)
        local = value.astimezone(_SP)
        return local.strftime("%d/%m/%Y %H:%M:%S")

    return str(value)


def register_filters(app) -> None:  # type: ignore[type-arg]
    """Register all custom Jinja2 filters on the Flask app."""
    app.jinja_env.filters["datetime_br"] = datetime_br
