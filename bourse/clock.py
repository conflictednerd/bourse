"""Dates and times as the market understands them.

Timestamps are recorded in UTC and interpreted against the exchange's clock, so
that a trade placed late in the evening in one timezone is still attributed to
the trading session that was open when it happened.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")


def stamp() -> str:
    """The current time, in the form written into a log."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def moment(when: str) -> datetime:
    """Parse a recorded timestamp."""
    return datetime.fromisoformat(when.replace("Z", "+00:00"))


def day(entry: dict) -> date:
    """The calendar date a log entry was recorded on."""
    return moment(entry.get("at", "")).date()


def session(entry: dict) -> date:
    """The trading day a log entry belongs to."""
    return moment(entry.get("at", "")).astimezone(MARKET_TZ).date()


def today() -> date:
    """The current trading day, which is not always the local calendar day."""
    return datetime.now(MARKET_TZ).date()
